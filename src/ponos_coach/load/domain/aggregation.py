# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Aggregazioni per i grafici: carico per settimana e sport, secondi per zona per settimana.

Le settimane sono ISO (da lunedì a domenica) sui giorni locali del profilo, che chi chiama ha
già calcolato per ogni attività. Le settimane senza attività dentro l'intervallo ci sono, a
zero. L'intervallo va dalla settimana della prima attività alla settimana di `through`
(oggi); le attività dopo `through` si ignorano, come nel PMC. Le somme sono vettoriali (NumPy),
senza cicli per attività.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np
from numpy.typing import NDArray

from ponos_coach.activities.domain import Sport
from ponos_coach.load.domain.zones import ZONE_COUNT, ZoneSeconds

_DAYS_PER_WEEK = 7


@dataclass(frozen=True, slots=True)
class DayLoad:
    """Un'attività vista dalle aggregazioni: giorno locale, sport, carico e secondi in zona."""

    day: date
    sport: Sport
    load: float
    zones: ZoneSeconds = ZoneSeconds.ZERO


def iso_week_start(day: date) -> date:
    """Il lunedì della settimana ISO che contiene `day`."""
    return day - timedelta(days=day.weekday())


@dataclass(frozen=True, slots=True, eq=False)
class WeeklyLoad:
    """Carico per settimana e sport: `load[i, j]` è il carico della settimana `weeks[i]` per lo
    sport `sports[j]`.

    `weeks` sono i lunedì consecutivi; `sports` gli sport con almeno un'attività, nell'ordine di
    `Sport`. Senza attività sono vuoti. Array di sola lettura.
    """

    weeks: tuple[date, ...]
    sports: tuple[Sport, ...]
    load: NDArray[np.float64]

    def __post_init__(self) -> None:
        if self.load.shape != (len(self.weeks), len(self.sports)):
            raise ValueError("la matrice del carico non corrisponde a settimane e sport")
        self.load.setflags(write=False)

    @property
    def total_by_week(self) -> NDArray[np.float64]:
        """Carico totale di ogni settimana (tutti gli sport)."""
        return np.asarray(self.load.sum(axis=1), dtype=np.float64)


@dataclass(frozen=True, slots=True, eq=False)
class WeeklyZones:
    """Secondi per zona e settimana: `seconds[i, z]` per la settimana `weeks[i]` e la zona Z(z+1).

    Array di sola lettura, `len(weeks)` righe e cinque colonne.
    """

    weeks: tuple[date, ...]
    seconds: NDArray[np.float64]

    def __post_init__(self) -> None:
        if self.seconds.shape != (len(self.weeks), ZONE_COUNT):
            raise ValueError("la matrice dei secondi non corrisponde a settimane e zone")
        self.seconds.setflags(write=False)


def _week_axis(
    items: Sequence[DayLoad], through: date
) -> tuple[tuple[date, ...], NDArray[np.intp], Sequence[DayLoad]]:
    """Lunedì consecutivi, indice di settimana di ogni attività tenuta e le attività tenute."""
    kept = [item for item in items if item.day <= through]
    if not kept:
        return (), np.zeros(0, dtype=np.intp), ()
    first_monday = iso_week_start(min(item.day for item in kept))
    last_monday = iso_week_start(through)
    count = (last_monday - first_monday).days // _DAYS_PER_WEEK + 1
    weeks = tuple(first_monday + timedelta(weeks=i) for i in range(count))
    offsets = np.array([(item.day - first_monday).days for item in kept], dtype=np.intp)
    return weeks, offsets // _DAYS_PER_WEEK, kept


def weekly_load_by_sport(items: Sequence[DayLoad], through: date) -> WeeklyLoad:
    """Carico per settimana ISO e per sport, dalla settimana della prima attività a quella di
    `through`; le settimane vuote valgono 0."""
    weeks, week_index, kept = _week_axis(items, through)
    present = {item.sport for item in kept}
    sports = tuple(sport for sport in Sport if sport in present)
    column = {sport: i for i, sport in enumerate(sports)}
    matrix = np.zeros((len(weeks), len(sports)), dtype=np.float64)
    if kept:
        columns = np.array([column[item.sport] for item in kept], dtype=np.intp)
        loads = np.array([item.load for item in kept], dtype=np.float64)
        np.add.at(matrix, (week_index, columns), loads)
    return WeeklyLoad(weeks, sports, matrix)


def weekly_zone_seconds(items: Sequence[DayLoad], through: date) -> WeeklyZones:
    """Secondi in ciascuna zona per settimana ISO; stesso intervallo di `weekly_load_by_sport`."""
    weeks, week_index, kept = _week_axis(items, through)
    matrix = np.zeros((len(weeks), ZONE_COUNT), dtype=np.float64)
    if kept:
        per_item = np.array([item.zones.values for item in kept], dtype=np.float64)
        np.add.at(matrix, week_index, per_item)
    return WeeklyZones(weeks, matrix)
