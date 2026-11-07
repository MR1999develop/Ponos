# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Pace zones on the threshold pace, and the retest window (T-PIANO-06)."""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pytest
from hypothesis import given
from hypothesis import strategies as st

from ponos_coach.assessment.domain import (
    NoTest,
    NotYet,
    Overdue,
    Recommended,
    pace_zone_indices,
    pace_zone_starts_s_per_km,
    retest_status,
)

# --- Pace zones ----------------------------------------------------------------------------


def zones_of(*paces: float, threshold: int = 300) -> list[int]:
    return [int(i) for i in pace_zone_indices(np.array(paces, dtype=np.float64), threshold)]


def test_the_threshold_pace_itself_is_the_start_of_zone_5() -> None:
    assert zones_of(300.0, 299.0, 301.0) == [4, 4, 3]


def test_a_pace_much_slower_than_the_threshold_is_zone_1() -> None:
    assert zones_of(600.0, 385.0) == [0, 0]


@pytest.mark.parametrize(("percent", "zone_index"), [(78, 1), (88, 2), (94, 3), (100, 4)])
def test_each_boundary_belongs_to_the_faster_zone(percent: int, zone_index: int) -> None:
    # A threshold pace divisible by every percentage makes each boundary a whole number of
    # seconds, so the comparison is on the boundary itself and one second to either side.
    threshold = 78 * 88 * 94
    boundary = threshold * 100 // percent

    assert zones_of(float(boundary), threshold=threshold) == [zone_index]
    assert zones_of(float(boundary + 1), threshold=threshold) == [zone_index - 1]


def test_zone_starts_are_the_paces_at_each_share_of_the_threshold_speed() -> None:
    assert pace_zone_starts_s_per_km(300) == pytest.approx(
        (300 / 0.78, 300 / 0.88, 300 / 0.94, 300.0)
    )


@given(st.integers(120, 1500), st.lists(st.integers(60, 3000), min_size=1, max_size=40))
def test_a_faster_pace_never_lands_in_a_slower_zone(threshold: int, paces: list[int]) -> None:
    ordered = sorted(paces, reverse=True)  # from the slowest to the fastest

    zones = pace_zone_indices(np.array(ordered, dtype=np.float64), threshold)

    assert (np.diff(zones) >= 0).all()
    assert zones.min() >= 0
    assert zones.max() <= 4


# --- Retest --------------------------------------------------------------------------------

LAST = date(2026, 1, 10)


def test_without_tests_the_first_one_is_due() -> None:
    assert retest_status([], date(2026, 3, 1)) == NoTest()


@pytest.mark.parametrize(
    ("days_after", "expected"),
    [
        (0, "not_yet"),
        (41, "not_yet"),  # the day before the window opens
        (42, "recommended"),  # first day
        (49, "recommended"),
        (56, "recommended"),  # last day
        (57, "overdue"),  # the first day late
        (100, "overdue"),
    ],
)
def test_retest_status_at_the_boundaries(days_after: int, expected: str) -> None:
    status = retest_status([LAST], LAST + timedelta(days=days_after))

    assert {NotYet: "not_yet", Recommended: "recommended", Overdue: "overdue"}[type(status)] == (
        expected
    )


def test_the_window_runs_from_the_42nd_to_the_56th_day_after_the_last_test() -> None:
    status = retest_status([LAST], LAST + timedelta(days=10))

    assert status == NotYet(date(2026, 2, 21), date(2026, 3, 7))
    assert retest_status([LAST], date(2026, 2, 21)) == Recommended(
        date(2026, 2, 21), date(2026, 3, 7)
    )


def test_overdue_says_for_how_many_days() -> None:
    assert retest_status([LAST], date(2026, 3, 8)) == Overdue(1, date(2026, 3, 7))
    assert retest_status([LAST], date(2026, 3, 17)) == Overdue(10, date(2026, 3, 7))


def test_only_the_latest_test_counts() -> None:
    days = [date(2025, 11, 1), LAST, date(2025, 12, 5)]

    assert retest_status(days, date(2026, 2, 21)) == Recommended(
        date(2026, 2, 21), date(2026, 3, 7)
    )


@given(
    st.lists(st.dates(min_value=date(2000, 1, 1), max_value=date(2090, 1, 1)), max_size=6),
    st.dates(min_value=date(2000, 1, 1), max_value=date(2090, 1, 1)),
)
def test_the_status_depends_on_the_latest_test_only(days: list[date], today: date) -> None:
    if not days:
        assert retest_status(days, today) == NoTest()
        return
    assert retest_status(days, today) == retest_status([max(days)], today)
