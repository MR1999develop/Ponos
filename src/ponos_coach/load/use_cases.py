# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Caso d'uso "panoramica della forma": carico delle attività di una sorgente e PMC.

È l'API che usa l'interfaccia: `compute_fitness_overview(source, params, today)`.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from types import MappingProxyType
from zoneinfo import ZoneInfo

from ponos_coach.activities.domain import Activity, FileId
from ponos_coach.activities.fit_decoder import (
    FitDecodeError,
    FitDecodeReason,
    FitLimits,
    decode_activity,
)
from ponos_coach.activities.ports import (
    FitFileRef,
    FitFileSource,
    FitFileTooLargeError,
    FitSourceError,
)
from ponos_coach.load.domain import (
    ActivityLoad,
    Physiology,
    PmcSeries,
    Sport,
    SportWeight,
    compute_activity_load,
    compute_pmc,
    default_weight,
)


class RejectionReason(Enum):
    """Perché un file non entra nella panoramica."""

    EMPTY = "empty"
    TOO_LARGE = "too_large"
    CORRUPT = "corrupt"
    NOT_AN_ACTIVITY = "not_an_activity"
    TOO_MANY_RECORDS = "too_many_records"
    TOO_LONG = "too_long"
    UNREADABLE = "unreadable"
    DUPLICATE = "duplicate"  # stesso file_id di un'attività già considerata


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
    """

    file_name: str
    file_id: FileId
    sport: Sport
    start: datetime
    local_day: date
    duration_s: float
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


def _load_entry(ref: FitFileRef, activity: Activity, params: FitnessParameters) -> ActivityEntry:
    load = compute_activity_load(
        activity.sport,
        activity.duration_s,
        activity.heart_rate,
        params.physiology,
        params.weight_of(activity.sport),
    )
    local_day = activity.start.astimezone(params.timezone).date()
    return ActivityEntry(
        ref.name, activity.file_id, activity.sport, activity.start, local_day,
        activity.duration_s, load,
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
    con lo stesso `file_id` è `DUPLICATE`. Il PMC parte dal primo giorno locale con attività
    (le attività dopo `today` compaiono nell'elenco ma non nel PMC) e somma i carichi pesati
    del giorno. Deterministico: stessa sorgente e parametri, stesso risultato.
    """
    entries: list[ActivityEntry] = []
    rejected: list[RejectedFile] = []
    seen: set[FileId] = set()
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
        if activity.file_id in seen:
            rejected.append(RejectedFile(ref.name, RejectionReason.DUPLICATE))
            continue
        seen.add(activity.file_id)
        entries.append(_load_entry(ref, activity, params))
    entries.sort(key=lambda entry: (entry.start, entry.file_name))
    return FitnessOverview(_pmc_of(entries, today), tuple(entries), tuple(rejected))


def _pmc_of(entries: list[ActivityEntry], today: date) -> PmcSeries:
    first_day = min((e.local_day for e in entries), default=today)
    if first_day > today:
        return PmcSeries.empty(today)
    if not entries:
        return PmcSeries.empty(first_day)
    daily: dict[date, float] = defaultdict(float)
    for entry in entries:
        daily[entry.local_day] += entry.load.load
    return compute_pmc(daily, first_day, today)
