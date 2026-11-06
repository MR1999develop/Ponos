# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Aggregazioni per i grafici: settimane ISO, sport, zone; settimane vuote a zero."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

import numpy as np
import pytest
from hypothesis import given
from hypothesis import strategies as st

from ponos_coach.activities.domain import Sport
from ponos_coach.load.domain import (
    ZONE_COUNT,
    DayLoad,
    ZoneSeconds,
    iso_week_start,
    weekly_load_by_sport,
    weekly_zone_seconds,
)

MONDAY = date(2026, 3, 2)  # lunedì
SUNDAY = date(2026, 3, 8)


def test_iso_week_starts_on_monday_and_sunday_belongs_to_the_week_before() -> None:
    assert iso_week_start(MONDAY) == MONDAY
    assert iso_week_start(SUNDAY) == MONDAY
    assert iso_week_start(SUNDAY + timedelta(days=1)) == MONDAY + timedelta(weeks=1)
    assert iso_week_start(date(2026, 1, 1)) == date(2025, 12, 29)  # a cavallo dell'anno


def test_load_per_week_and_sport_by_hand() -> None:
    items = [
        DayLoad(MONDAY, Sport.RUNNING, 50.0),
        DayLoad(SUNDAY, Sport.RUNNING, 30.0),  # stessa settimana ISO del lunedì
        DayLoad(SUNDAY, Sport.CYCLING, 20.0),
        DayLoad(SUNDAY + timedelta(days=1), Sport.RUNNING, 10.0),  # lunedì successivo
    ]

    result = weekly_load_by_sport(items, date(2026, 3, 9))

    assert result.weeks == (MONDAY, MONDAY + timedelta(weeks=1))
    assert result.sports == (Sport.RUNNING, Sport.CYCLING)
    assert result.load.tolist() == [[80.0, 20.0], [10.0, 0.0]]
    assert result.total_by_week.tolist() == [100.0, 10.0]


def test_weeks_without_activities_are_present_with_zero() -> None:
    items = [
        DayLoad(MONDAY, Sport.RUNNING, 40.0),
        DayLoad(MONDAY + timedelta(weeks=3), Sport.RUNNING, 25.0),
    ]

    result = weekly_load_by_sport(items, MONDAY + timedelta(weeks=3))

    assert len(result.weeks) == 4
    assert result.load[:, 0].tolist() == [40.0, 0.0, 0.0, 25.0]


def test_the_range_goes_on_to_the_week_of_today_with_trailing_zeros() -> None:
    result = weekly_load_by_sport([DayLoad(MONDAY, Sport.RUNNING, 40.0)], date(2026, 3, 20))

    assert result.weeks == (MONDAY, date(2026, 3, 9), date(2026, 3, 16))
    assert result.load[:, 0].tolist() == [40.0, 0.0, 0.0]


def test_activities_after_today_are_ignored_like_in_the_pmc() -> None:
    items = [DayLoad(MONDAY, Sport.RUNNING, 40.0), DayLoad(date(2026, 4, 1), Sport.CYCLING, 99.0)]

    result = weekly_load_by_sport(items, date(2026, 3, 4))

    assert result.weeks == (MONDAY,)
    assert result.sports == (Sport.RUNNING,)  # nemmeno lo sport futuro compare
    assert result.load.tolist() == [[40.0]]


def test_no_activities_give_empty_aggregations() -> None:
    load = weekly_load_by_sport([], date(2026, 3, 4))
    zones = weekly_zone_seconds([], date(2026, 3, 4))

    assert load.weeks == () and load.sports == () and load.load.shape == (0, 0)
    assert zones.weeks == () and zones.seconds.shape == (0, ZONE_COUNT)


def test_zone_seconds_per_week_by_hand() -> None:
    items = [
        DayLoad(MONDAY, Sport.RUNNING, 1.0, ZoneSeconds((10.0, 20.0, 0.0, 0.0, 0.0))),
        DayLoad(SUNDAY, Sport.CYCLING, 1.0, ZoneSeconds((0.0, 5.0, 7.0, 0.0, 3.0))),
        DayLoad(
            SUNDAY + timedelta(weeks=2), Sport.RUNNING, 1.0, ZoneSeconds((1.0, 1.0, 1.0, 1.0, 1.0))
        ),
    ]

    result = weekly_zone_seconds(items, SUNDAY + timedelta(weeks=2))

    assert result.weeks == (MONDAY, date(2026, 3, 9), date(2026, 3, 16))
    assert result.seconds.tolist() == [
        [10.0, 25.0, 7.0, 0.0, 3.0],
        [0.0, 0.0, 0.0, 0.0, 0.0],  # settimana senza attività
        [1.0, 1.0, 1.0, 1.0, 1.0],
    ]


def test_results_are_read_only() -> None:
    result = weekly_load_by_sport([DayLoad(MONDAY, Sport.RUNNING, 40.0)], MONDAY)

    with pytest.raises(ValueError, match="read-only"):
        result.load[0, 0] = 1.0


# --- Contro una versione ingenua --------------------------------------------------------------

_zone_values = st.tuples(*[st.floats(min_value=0.0, max_value=5000.0)] * ZONE_COUNT)
_day_loads = st.builds(
    DayLoad,
    day=st.dates(min_value=date(2020, 1, 1), max_value=date(2022, 12, 31)),
    sport=st.sampled_from(list(Sport)),
    load=st.floats(min_value=0.0, max_value=500.0),
    zones=_zone_values.map(ZoneSeconds),
)


def _naive_weeks(items: list[DayLoad], through: date) -> list[date]:
    kept = [item for item in items if item.day <= through]
    if not kept:
        return []
    week = iso_week_start(min(item.day for item in kept))
    weeks = []
    while week <= iso_week_start(through):
        weeks.append(week)
        week += timedelta(days=7)
    return weeks


@given(
    items=st.lists(_day_loads, max_size=60),
    through=st.dates(min_value=date(2020, 1, 1), max_value=date(2023, 6, 30)),
)
def test_weekly_load_equals_the_naive_dictionary_version(
    items: list[DayLoad], through: date
) -> None:
    weeks = _naive_weeks(items, through)
    expected: dict[tuple[date, Sport], float] = defaultdict(float)
    for item in items:
        if item.day <= through:
            expected[(iso_week_start(item.day), item.sport)] += item.load

    result = weekly_load_by_sport(items, through)

    assert list(result.weeks) == weeks
    assert result.sports == tuple(sorted({s for _, s in expected}, key=list(Sport).index))
    for i, week in enumerate(result.weeks):
        for j, sport in enumerate(result.sports):
            assert result.load[i, j] == pytest.approx(expected.get((week, sport), 0.0))


@given(
    items=st.lists(_day_loads, max_size=60),
    through=st.dates(min_value=date(2020, 1, 1), max_value=date(2023, 6, 30)),
)
def test_weekly_zones_equal_the_naive_dictionary_version(
    items: list[DayLoad], through: date
) -> None:
    expected: dict[tuple[date, int], float] = defaultdict(float)
    for item in items:
        if item.day <= through:
            for zone, seconds in enumerate(item.zones.values):
                expected[(iso_week_start(item.day), zone)] += seconds

    result = weekly_zone_seconds(items, through)

    assert list(result.weeks) == _naive_weeks(items, through)
    for i, week in enumerate(result.weeks):
        for zone in range(ZONE_COUNT):
            assert result.seconds[i, zone] == pytest.approx(expected.get((week, zone), 0.0))


@given(
    items=st.lists(_day_loads, min_size=1, max_size=60),
    through=st.dates(min_value=date(2023, 1, 1), max_value=date(2023, 6, 30)),
)
def test_aggregation_conserves_the_total_load_and_the_total_zone_seconds(
    items: list[DayLoad], through: date
) -> None:
    load = weekly_load_by_sport(items, through)
    zones = weekly_zone_seconds(items, through)

    assert float(load.load.sum()) == pytest.approx(sum(i.load for i in items))
    assert float(zones.seconds.sum()) == pytest.approx(sum(i.zones.total for i in items))
    assert np.all(load.load >= 0) and np.all(zones.seconds >= 0)
    assert all(week.weekday() == 0 for week in load.weeks)
