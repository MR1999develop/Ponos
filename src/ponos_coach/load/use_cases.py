# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Casi d'uso della panoramica della forma: carico delle attività e PMC.

- `ComputeProfileOverview`: panoramica di un profilo (indice, archivio, cache dei carichi).
- `compute_fitness_overview(source, params, today)`: panoramica di una cartella di file FIT
  (usata oggi dall'interfaccia; sarà rimossa con l'opzione `--fit-folder`).

I due usano gli stessi passi interni: giorno locale, calcolo del carico, serie PMC.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from types import MappingProxyType
from zoneinfo import ZoneInfo

from ponos_coach.activities.domain import (
    MAX_ACTIVITY_FILE_BYTES,
    Activity,
    ActivityIdentity,
    ActivityRecord,
    ActivitySummary,
    FileFormat,
    Sport,
)
from ponos_coach.activities.fit_decoder import (
    FitDecodeError,
    FitDecodeReason,
    FitLimits,
    decode_activity,
)
from ponos_coach.activities.ports import (
    ActivityArchive,
    ActivityIndex,
    ArchiveError,
    ArchiveFileMissingError,
    ArchiveIntegrityError,
    ArchiveTooLargeError,
    FitFileRef,
    FitFileSource,
    FitFileTooLargeError,
    FitSourceError,
)
from ponos_coach.load.domain import (
    ActivityLoad,
    LoadFingerprint,
    Physiology,
    PmcSeries,
    SportWeight,
    compute_activity_load,
    compute_pmc,
    default_weight,
    load_fingerprint,
    physiology_for_day,
)
from ponos_coach.load.ports import LoadCache, PhysiologyStore


class RejectionReason(Enum):
    """Perché un file non entra nella panoramica."""

    EMPTY = "empty"
    TOO_LARGE = "too_large"
    CORRUPT = "corrupt"
    NOT_AN_ACTIVITY = "not_an_activity"
    TOO_MANY_RECORDS = "too_many_records"
    TOO_LONG = "too_long"
    UNREADABLE = "unreadable"
    DUPLICATE = "duplicate"  # stessa identità di un'attività già considerata


_DECODE_TO_REJECTION = {
    FitDecodeReason.EMPTY: RejectionReason.EMPTY,
    FitDecodeReason.TOO_LARGE: RejectionReason.TOO_LARGE,
    FitDecodeReason.CORRUPT: RejectionReason.CORRUPT,
    FitDecodeReason.NOT_AN_ACTIVITY: RejectionReason.NOT_AN_ACTIVITY,
    FitDecodeReason.TOO_MANY_RECORDS: RejectionReason.TOO_MANY_RECORDS,
    FitDecodeReason.TOO_LONG: RejectionReason.TOO_LONG,
}


@dataclass(frozen=True, slots=True)
class FitnessParameters:
    """Parametri della panoramica.

    `timezone` assegna ogni attività al giorno locale della sua ora d'inizio. `sport_weights`
    sovrascrive i pesi di default (uno sport assente usa il suo default). `fit_limits` sono i
    tetti di lettura dei file.
    """

    physiology: Physiology
    timezone: ZoneInfo
    sport_weights: Mapping[Sport, SportWeight] = field(default_factory=lambda: MappingProxyType({}))
    fit_limits: FitLimits = field(default_factory=FitLimits)

    def __post_init__(self) -> None:
        object.__setattr__(self, "sport_weights", MappingProxyType(dict(self.sport_weights)))

    def weight_of(self, sport: Sport) -> SportWeight:
        """Peso dello sport: quello impostato o il default."""
        return self.sport_weights.get(sport, default_weight(sport))


@dataclass(frozen=True, slots=True)
class ActivityEntry:
    """Un'attività con il suo carico (la serie di FC non si conserva).

    `local_day` è la data locale dell'inizio nel fuso dei parametri; `start` è in UTC.
    `elapsed_s` è il tempo trascorso e `timer_s` quello in movimento (su cui è calcolato il
    carico).
    """

    file_name: str
    identity: ActivityIdentity
    sport: Sport
    start: datetime
    local_day: date
    elapsed_s: float
    timer_s: float
    load: ActivityLoad


@dataclass(frozen=True, slots=True)
class RejectedFile:
    """Un file escluso e il motivo."""

    file_name: str
    reason: RejectionReason


@dataclass(frozen=True, slots=True)
class FitnessOverview:
    """Esito: PMC dal primo giorno con attività a `today` (vuoto se non ce ne sono),
    attività in ordine di inizio, file rifiutati nell'ordine della sorgente."""

    pmc: PmcSeries
    activities: tuple[ActivityEntry, ...]
    rejected: tuple[RejectedFile, ...]


def _local_day(start: datetime, timezone: ZoneInfo) -> date:
    """Giorno locale (nel fuso del profilo) dell'inizio di un'attività."""
    return start.astimezone(timezone).date()


def _load_of(activity: Activity, physiology: Physiology, weight: SportWeight) -> ActivityLoad:
    """Carico dell'attività sul tempo in movimento (unico punto di calcolo)."""
    return compute_activity_load(
        activity.sport, activity.timer_s, activity.heart_rate, physiology, weight
    )


def _load_entry(ref: FitFileRef, activity: Activity, params: FitnessParameters) -> ActivityEntry:
    load = _load_of(activity, params.physiology, params.weight_of(activity.sport))
    local_day = _local_day(activity.start, params.timezone)
    return ActivityEntry(
        ref.name, activity.identity, activity.sport, activity.start, local_day,
        activity.elapsed_s, activity.timer_s, load,
    )  # fmt: skip


def _read_activity(source: FitFileSource, ref: FitFileRef, limits: FitLimits) -> Activity:
    """Legge e decodifica; ogni guasto diventa `FitDecodeError` o `FitSourceError`."""
    return decode_activity(source.read(ref, limits.max_bytes), limits)


def compute_fitness_overview(
    source: FitFileSource, params: FitnessParameters, today: date
) -> FitnessOverview:
    """Carica i file della sorgente e calcola carichi per attività e PMC fino a `today`.

    Ogni file è letto con il tetto di byte dei parametri, decodificato e calcolato; un file
    illeggibile o non valido finisce in `rejected` senza fermare gli altri, e un secondo file
    con la stessa identità è `DUPLICATE`. Il PMC parte dal primo giorno locale con attività
    (le attività dopo `today` compaiono nell'elenco ma non nel PMC) e somma i carichi pesati
    del giorno. Deterministico: stessa sorgente e parametri, stesso risultato.
    """
    entries: list[ActivityEntry] = []
    rejected: list[RejectedFile] = []
    seen: set[ActivityIdentity] = set()
    for ref in source.list_files():
        try:
            activity = _read_activity(source, ref, params.fit_limits)
        except FitDecodeError as error:
            rejected.append(RejectedFile(ref.name, _DECODE_TO_REJECTION[error.reason]))
            continue
        except FitFileTooLargeError:
            rejected.append(RejectedFile(ref.name, RejectionReason.TOO_LARGE))
            continue
        except FitSourceError:
            rejected.append(RejectedFile(ref.name, RejectionReason.UNREADABLE))
            continue
        if activity.identity in seen:
            rejected.append(RejectedFile(ref.name, RejectionReason.DUPLICATE))
            continue
        seen.add(activity.identity)
        entries.append(_load_entry(ref, activity, params))
    entries.sort(key=lambda entry: (entry.start, entry.file_name))
    pmc = _pmc_of(((e.local_day, e.load.load) for e in entries), today)
    return FitnessOverview(pmc, tuple(entries), tuple(rejected))


def _pmc_of(day_loads: Iterable[tuple[date, float]], today: date) -> PmcSeries:
    """PMC dal primo giorno con attività a `today`; vuoto se non ce ne sono (o sono future)."""
    daily: dict[date, float] = defaultdict(float)
    for day, load in day_loads:
        daily[day] += load
    if not daily:
        return PmcSeries.empty(today)
    first_day = min(daily)
    if first_day > today:
        return PmcSeries.empty(today)
    return compute_pmc(daily, first_day, today)


# --- Panoramica di un profilo ---------------------------------------------------------------


class ExclusionReason(Enum):
    """Perché un'attività del profilo non entra nella panoramica."""

    FILE_MISSING = "file_missing"  # il file non è più nell'archivio
    FILE_ALTERED = "file_altered"  # dimensione o SHA-256 diversi, o identità diversa
    FILE_TOO_LARGE = "file_too_large"  # oltre il tetto di byte
    UNREADABLE = "unreadable"  # altro errore dell'archivio
    DECODE_FAILED = "decode_failed"  # il contenuto non è un'attività valida


class OverviewCancelledError(Exception):
    """Il calcolo è stato annullato dall'utente; nessun risultato parziale."""


type Decoder = Callable[[bytes, FileFormat], Activity]
"""Decodifica i byte di un file; solleva `FitDecodeError` se non è un'attività valida."""


@dataclass(frozen=True, slots=True)
class ProfileActivity:
    """Un'attività del profilo con il suo carico e il riepilogo (distanza, dislivello).

    `start` è in UTC, `local_day` la data locale nel fuso del profilo; `load.source` dice se il
    carico viene dalla FC o da una stima.
    """

    identity: ActivityIdentity
    sport: Sport
    start: datetime
    local_day: date
    elapsed_s: float
    timer_s: float
    summary: ActivitySummary
    load: ActivityLoad


@dataclass(frozen=True, slots=True)
class ExcludedActivity:
    """Un'attività che non si è potuta calcolare e il motivo."""

    identity: ActivityIdentity
    reason: ExclusionReason


@dataclass(frozen=True, slots=True)
class ProfileOverview:
    """Esito: PMC dal primo giorno con attività a `today` (vuoto senza attività), attività con
    carico per inizio crescente, escluse nello stesso ordine."""

    pmc: PmcSeries
    activities: tuple[ProfileActivity, ...]
    excluded: tuple[ExcludedActivity, ...]


def _never_cancelled() -> bool:
    return False


def _ignore_progress(_done: int, _total: int) -> None:
    return None


type _Computed = tuple[ActivityIdentity, LoadFingerprint, ActivityLoad]


class ComputeProfileOverview:
    """Calcola la panoramica del profilo aperto.

    Per ogni attività dell'indice: giorno locale, parametri validi quel giorno, impronta; se la
    cache ha il carico con quella impronta non si legge nulla, altrimenti si legge l'originale
    dall'archivio (con il tetto di byte), lo si decodifica e si calcola il carico sul tempo in
    movimento. Un guasto su una attività la mette tra le escluse senza fermare le altre.
    I carichi calcolati si scrivono in cache in una sola transazione alla fine (anche dopo un
    annullamento). Deterministico: stessi dati e parametri, stesso risultato.

    Dipendenze volatili iniettate: porte, `decode` (fornita dal Composition Root), `today`
    (oggi, nel fuso del profilo), `is_cancelled` (annullamento cooperativo, controllato prima di
    ogni attività) e `report(fatte, totali)` per l'avanzamento.
    """

    def __init__(  # noqa: PLR0913 - ogni porta è una dipendenza volatile distinta
        self,
        index: ActivityIndex,
        archive: ActivityArchive,
        physiology: PhysiologyStore,
        cache: LoadCache,
        decode: Decoder,
        *,
        timezone: ZoneInfo,
        today: Callable[[], date],
        sport_weights: Mapping[Sport, SportWeight] | None = None,
        max_file_bytes: int = MAX_ACTIVITY_FILE_BYTES,
        is_cancelled: Callable[[], bool] = _never_cancelled,
        report: Callable[[int, int], None] = _ignore_progress,
    ) -> None:
        if max_file_bytes <= 0:
            raise ValueError("il tetto di byte deve essere positivo")
        self._index = index
        self._archive = archive
        self._physiology = physiology
        self._cache = cache
        self._decode = decode
        self._timezone = timezone
        self._today = today
        self._weights = MappingProxyType(dict(sport_weights or {}))
        self._max_file_bytes = max_file_bytes
        self._is_cancelled = is_cancelled
        self._report = report

    def execute(self) -> ProfileOverview:
        """Calcola la panoramica.

        Raises:
            OverviewCancelledError: `is_cancelled` è diventato vero.
            MissingPhysiologyError: ci sono attività ma nessun parametro fisiologico.
        """
        records = self._index.list_by_start()
        today = self._today()
        entries = self._physiology.list_entries() if records else ()
        activities: list[ProfileActivity] = []
        excluded: list[ExcludedActivity] = []
        computed: list[_Computed] = []
        self._report(0, len(records))
        try:
            for done, record in enumerate(records, start=1):
                if self._is_cancelled():
                    raise OverviewCancelledError
                local_day = _local_day(record.start, self._timezone)
                physiology = physiology_for_day(entries, local_day)
                fingerprint = load_fingerprint(physiology, self._weights)
                load = self._cache.get(record.identity, fingerprint)
                if load is None:
                    outcome = self._compute(record, physiology)
                    if isinstance(outcome, ExclusionReason):
                        excluded.append(ExcludedActivity(record.identity, outcome))
                    else:
                        computed.append((record.identity, fingerprint, outcome))
                        activities.append(_profile_activity(record, local_day, outcome))
                else:
                    activities.append(_profile_activity(record, local_day, load))
                self._report(done, len(records))
        finally:
            self._store(computed)
        pmc = _pmc_of(((a.local_day, a.load.load) for a in activities), today)
        return ProfileOverview(pmc, tuple(activities), tuple(excluded))

    def _compute(
        self, record: ActivityRecord, physiology: Physiology
    ) -> ActivityLoad | ExclusionReason:
        """Carico dall'originale in archivio, o il motivo per cui non si può calcolare."""
        data = self._read(record)
        if isinstance(data, ExclusionReason):
            return data
        try:
            activity = self._decode(data, record.file_format)
        except FitDecodeError:
            return ExclusionReason.DECODE_FAILED
        if activity.identity != record.identity:
            return ExclusionReason.FILE_ALTERED
        weight = self._weights.get(record.sport, default_weight(record.sport))
        return _load_of(activity, physiology, weight)

    def _read(self, record: ActivityRecord) -> bytes | ExclusionReason:
        try:
            return self._archive.read(record.archived, self._max_file_bytes)
        except ArchiveFileMissingError:
            return ExclusionReason.FILE_MISSING
        except ArchiveIntegrityError:
            return ExclusionReason.FILE_ALTERED
        except ArchiveTooLargeError:
            return ExclusionReason.FILE_TOO_LARGE
        except ArchiveError:
            return ExclusionReason.UNREADABLE

    def _store(self, computed: list[_Computed]) -> None:
        if not computed:
            return
        with self._index.transaction():
            for identity, fingerprint, load in computed:
                self._cache.put(identity, fingerprint, load)


def _profile_activity(
    record: ActivityRecord, local_day: date, load: ActivityLoad
) -> ProfileActivity:
    return ProfileActivity(
        record.identity, record.sport, record.start, local_day,
        record.elapsed_s, record.timer_s, record.summary, load,
    )  # fmt: skip
