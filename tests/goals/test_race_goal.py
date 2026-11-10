# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""The elevation gain of a race is valid by construction: whole metres from 0 to 30 000, and never
steeper on average than 50%."""

from __future__ import annotations

from datetime import date

import pytest
from hypothesis import given
from hypothesis import strategies as st

from ponos_coach.activities.domain import Sport
from ponos_coach.goals.domain import GoalId, Priority, RaceGoal
from ponos_coach.goals.domain.goal import MAX_ASCENT_M, MAX_DISTANCE_M, MIN_DISTANCE_M

DAY = date(2026, 9, 20)


def make(distance_m: int, ascent_m: int | None = None) -> RaceGoal:
    if ascent_m is None:
        return RaceGoal(GoalId("a"), DAY, Priority.A, Sport.TRAIL, distance_m)
    return RaceGoal(GoalId("a"), DAY, Priority.A, Sport.TRAIL, distance_m, None, ascent_m)


def test_a_race_without_an_elevation_gain_is_flat() -> None:
    race = make(42_195)

    assert race.ascent_m == 0
    assert race.ascent_m_per_km == 0.0


@pytest.mark.parametrize(
    ("distance_m", "ascent_m"),
    [
        (100_000, 0),  # the lowest
        (100_000, 1),
        (100_000, MAX_ASCENT_M),  # the highest
        (MIN_DISTANCE_M, MIN_DISTANCE_M // 2),  # 50% on the shortest race: the steepest
        (5_000, 2_500),
    ],
)
def test_the_elevation_gain_is_valid_at_its_limits(distance_m: int, ascent_m: int) -> None:
    assert make(distance_m, ascent_m).ascent_m == ascent_m


@pytest.mark.parametrize(
    ("distance_m", "ascent_m"),
    [
        (100_000, -1),  # one below the lowest
        (100_000, MAX_ASCENT_M + 1),  # one above the highest
        (MIN_DISTANCE_M, MIN_DISTANCE_M // 2 + 1),  # one metre steeper than 50%
        (5_000, 2_501),
    ],
)
def test_the_elevation_gain_outside_its_limits_is_refused(distance_m: int, ascent_m: int) -> None:
    with pytest.raises(ValueError, match=r"elevation gain|mean grade"):
        make(distance_m, ascent_m)


def test_the_mean_climb_is_metres_per_kilometre() -> None:
    assert make(30_000, 1_500).ascent_m_per_km == pytest.approx(50.0)
    assert make(10_000, 199).ascent_m_per_km == pytest.approx(19.9)


@given(
    st.integers(MIN_DISTANCE_M, MAX_DISTANCE_M),
    st.integers(-10, MAX_ASCENT_M + 10),
)
def test_a_race_exists_exactly_when_its_climb_is_within_the_limits(
    distance_m: int, ascent_m: int
) -> None:
    valid = 0 <= ascent_m <= MAX_ASCENT_M and ascent_m * 2 <= distance_m
    if valid:
        assert make(distance_m, ascent_m).ascent_m == ascent_m
    else:
        with pytest.raises(ValueError, match=r"elevation gain|mean grade"):
            make(distance_m, ascent_m)
