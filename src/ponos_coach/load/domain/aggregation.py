# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Aggregations for the charts: load per week and sport, seconds per zone per week.

Weeks are ISO (Monday to Sunday) on the profile's local days, which the caller has
already computed for each activity. Weeks without activities inside the range are there, at
zero. The range goes from the week of the first activity to the week of `through`
(today); activities after `through` are ignored, as in the PMC. Sums are vectorized (NumPy),
with no per-activity loops.
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
    """An activity as seen by the aggregations: local day, sport, load and seconds in zone."""

    day: date
    sport: Sport
    load: float
    zones: ZoneSeconds = ZoneSeconds.ZERO


def iso_week_start(day: date) -> date:
    """The Monday of the ISO week containing `day`."""
    return day - timedelta(days=day.weekday())


@dataclass(frozen=True, slots=True, eq=False)
class WeeklyLoad:
    """Load per week and sport: `load[i, j]` is the load of week `weeks[i]` for the
    sport `sports[j]`.

    `weeks` are the consecutive Mondays; `sports` the sports with at least one activity, in the
    order of
    `Sport`. Without activities they are empty. Read-only arrays.
    """

    weeks: tuple[date, ...]
    sports: tuple[Sport, ...]
    load: NDArray[np.float64]

    def __post_init__(self) -> None:
        if self.load.shape != (len(self.weeks), len(self.sports)):
            raise ValueError("the load matrix does not match weeks and sports")
        self.load.setflags(write=False)

    @property
    def total_by_week(self) -> NDArray[np.float64]:
        """Total load of each week (all sports)."""
        return np.asarray(self.load.sum(axis=1), dtype=np.float64)


@dataclass(frozen=True, slots=True, eq=False)
class WeeklyZones:
    """Seconds per zone and week: `seconds[i, z]` for week `weeks[i]` and zone Z(z+1).

    Read-only arrays, `len(weeks)` rows and five columns.
    """

    weeks: tuple[date, ...]
    seconds: NDArray[np.float64]

    def __post_init__(self) -> None:
        if self.seconds.shape != (len(self.weeks), ZONE_COUNT):
            raise ValueError("the seconds matrix does not match weeks and zones")
        self.seconds.setflags(write=False)


def _week_axis(
    items: Sequence[DayLoad], through: date
) -> tuple[tuple[date, ...], NDArray[np.intp], Sequence[DayLoad]]:
    """Consecutive Mondays, week index of each kept activity and the kept activities."""
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
    """Load per ISO week and per sport, from the week of the first activity to that of
    `through`; empty weeks are worth 0."""
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
    """Seconds in each zone per ISO week; same range as `weekly_load_by_sport`."""
    weeks, week_index, kept = _week_axis(items, through)
    matrix = np.zeros((len(weeks), ZONE_COUNT), dtype=np.float64)
    if kept:
        per_item = np.array([item.zones.values for item in kept], dtype=np.float64)
        np.add.at(matrix, week_index, per_item)
    return WeeklyZones(weeks, matrix)
