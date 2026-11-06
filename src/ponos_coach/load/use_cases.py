# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Casi d'uso della panoramica della forma: carico delle attività e PMC.

- `ComputeProfileOverview`: panoramica di un profilo (indice, archivio, cache dei carichi):
  giorno locale, calcolo del carico, serie PMC.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from types import MappingProxyType
from zoneinfo import ZoneInfo

from ponos_coach.activities.decoding import ActivityFileError
from ponos_coach.activities.domain import (
    MAX_ACTIVITY_FILE_BYTES,
    Activity,
    ActivityIdentity,
    ActivityRecord,
    ActivitySummary,
    FileFormat,
    Sport,
)
from ponos_coach.activities.ports import (
    ActivityArchive,
    ActivityIndex,
    ArchiveError,
    ArchiveFileMissingError,
    ArchiveIntegrityError,
    ArchiveTooLargeError,
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


def _local_day(start: datetime, timezone: ZoneInfo) -> date:
    """Giorno locale (nel fuso del profilo) dell'inizio di un'attività."""
    return start.astimezone(timezone).date()


def _load_of(activity: Activity, physiology: Physiology, weight: SportWeight) -> ActivityLoad:
    """Carico dell'attività sul tempo in movimento (unico punto di calcolo)."""
    return compute_activity_load(
        activity.sport, activity.timer_s, activity.heart_rate, physiology, weight
    )


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
"""Decodifica i byte di un file archiviato; solleva `ActivityFileError` se non è valido."""


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
        except ActivityFileError:
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
