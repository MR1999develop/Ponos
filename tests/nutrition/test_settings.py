# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""The user's nutrition choices as domain types: level with date, meal times, goal periods."""

from __future__ import annotations

from datetime import UTC, date, time

import pytest
from hypothesis import given
from hypothesis import strategies as st

from ponos_coach.nutrition.domain import (
    ActivityLevel,
    ActivityLevelEntry,
    GoalPeriod,
    MaintenanceGoal,
    MealTimes,
    PerformanceGoal,
    WeightLossGoal,
    goal_for_day,
    level_for_day,
)

D = date(2026, 3, 10)


# --- Activity level with validity date ----------------------------------------------------


def test_the_level_in_force_is_the_latest_entry_not_after_the_day() -> None:
    entries = [
        ActivityLevelEntry(date(2026, 1, 1), ActivityLevel.LIGHT),
        ActivityLevelEntry(date(2026, 3, 1), ActivityLevel.ACTIVE),
    ]

    assert level_for_day(entries, date(2026, 2, 28)) is ActivityLevel.LIGHT
    assert level_for_day(entries, date(2026, 3, 1)) is ActivityLevel.ACTIVE  # from its own day
    assert level_for_day(entries, date(2030, 1, 1)) is ActivityLevel.ACTIVE


def test_before_the_first_entry_or_without_entries_there_is_no_level() -> None:
    entries = [ActivityLevelEntry(date(2026, 1, 1), ActivityLevel.LIGHT)]

    assert level_for_day(entries, date(2025, 12, 31)) is None
    assert level_for_day([], D) is None


def test_a_level_cannot_hold_from_before_1900() -> None:
    with pytest.raises(ValueError, match="1900"):
        ActivityLevelEntry(date(1899, 12, 31), ActivityLevel.LIGHT)
    ActivityLevelEntry(date(1900, 1, 1), ActivityLevel.LIGHT)


# --- Meal times ---------------------------------------------------------------------------


def test_the_default_meal_times_are_half_past_seven_one_and_eight() -> None:
    assert MealTimes.default().minutes == (450, 780, 1200)


def test_meal_times_two_hours_apart_are_accepted_and_closer_ones_are_not() -> None:
    MealTimes(time(8, 0), time(10, 0), time(12, 0))  # exactly two hours: on the boundary
    with pytest.raises(ValueError, match="two hours"):
        MealTimes(time(8, 0), time(9, 59), time(12, 0))  # one minute short
    with pytest.raises(ValueError, match="two hours"):
        MealTimes(time(8, 0), time(10, 0), time(11, 59))


def test_meal_times_out_of_order_are_refused() -> None:
    with pytest.raises(ValueError, match="order"):
        MealTimes(time(13, 0), time(8, 0), time(20, 0))


@pytest.mark.parametrize("moment", [time(8, 0, 1), time(8, 0, 0, 5), time(8, 0, tzinfo=UTC)])
def test_meal_times_are_whole_minutes(moment: time) -> None:
    with pytest.raises(ValueError, match="whole minutes"):
        MealTimes(moment, time(13, 0), time(20, 0))


@pytest.mark.parametrize("minutes", [(-1, 780, 1200), (450, 780, 1440)])
def test_minutes_outside_the_day_are_refused(minutes: tuple[int, int, int]) -> None:
    with pytest.raises(ValueError, match="within the day"):
        MealTimes.from_minutes(*minutes)


@given(st.integers(0, 1439 - 240), st.integers(120, 600), st.integers(120, 600))
def test_meal_times_round_trip_through_minutes(first: int, gap_a: int, gap_b: int) -> None:
    if first + gap_a + gap_b > 1439:
        return
    times = MealTimes.from_minutes(first, first + gap_a, first + gap_a + gap_b)

    assert MealTimes.from_minutes(*times.minutes) == times


# --- Goals and periods --------------------------------------------------------------------


@pytest.mark.parametrize("grams", [100, 500, 1000])
def test_a_weekly_loss_between_100_and_1000_grams_is_accepted(grams: int) -> None:
    assert WeightLossGoal(grams).weekly_loss_g == grams


@pytest.mark.parametrize("grams", [-500, 0, 99, 1001])
def test_a_weekly_loss_outside_100_and_1000_grams_is_refused(grams: int) -> None:
    with pytest.raises(ValueError, match="weekly loss"):
        WeightLossGoal(grams)


def test_a_period_contains_both_its_ends() -> None:
    period = GoalPeriod(date(2026, 3, 1), date(2026, 3, 31), MaintenanceGoal())

    assert period.contains(date(2026, 3, 1))
    assert period.contains(date(2026, 3, 31))
    assert not period.contains(date(2026, 2, 28))
    assert not period.contains(date(2026, 4, 1))


def test_an_open_ended_period_contains_every_later_day() -> None:
    period = GoalPeriod(date(2026, 3, 1), None, PerformanceGoal())

    assert period.contains(date(2099, 1, 1))
    assert not period.contains(date(2026, 2, 28))


def test_a_period_may_last_one_day_but_not_end_before_it_starts() -> None:
    GoalPeriod(D, D, MaintenanceGoal())
    with pytest.raises(ValueError, match="last day"):
        GoalPeriod(D, date(2026, 3, 9), MaintenanceGoal())
    with pytest.raises(ValueError, match="1900"):
        GoalPeriod(date(1899, 12, 31), None, MaintenanceGoal())


@pytest.mark.parametrize(
    ("other", "overlaps"),
    [
        (GoalPeriod(date(2026, 4, 1), date(2026, 4, 30), MaintenanceGoal()), False),  # after
        (GoalPeriod(date(2026, 2, 1), date(2026, 2, 28), MaintenanceGoal()), False),  # before
        (GoalPeriod(date(2026, 4, 1), None, MaintenanceGoal()), False),  # open, after
        (GoalPeriod(date(2026, 3, 31), date(2026, 4, 30), MaintenanceGoal()), True),  # one day
        (GoalPeriod(date(2026, 2, 1), date(2026, 3, 1), MaintenanceGoal()), True),  # one day
        (GoalPeriod(date(2026, 3, 10), date(2026, 3, 12), MaintenanceGoal()), True),  # inside
        (GoalPeriod(date(2026, 1, 1), None, MaintenanceGoal()), True),  # open, around
    ],
)
def test_periods_overlap_exactly_when_they_share_a_day(other: GoalPeriod, overlaps: bool) -> None:
    period = GoalPeriod(date(2026, 3, 1), date(2026, 3, 31), WeightLossGoal(500))

    assert period.overlaps(other) is overlaps
    assert other.overlaps(period) is overlaps


def test_the_goal_of_a_day_comes_from_its_period_and_defaults_to_maintenance() -> None:
    periods = [
        GoalPeriod(date(2026, 3, 1), date(2026, 3, 31), WeightLossGoal(500)),
        GoalPeriod(date(2026, 4, 1), None, PerformanceGoal()),
    ]

    assert goal_for_day(periods, date(2026, 2, 28)) == MaintenanceGoal()
    assert goal_for_day(periods, date(2026, 3, 31)) == WeightLossGoal(500)
    assert goal_for_day(periods, date(2026, 4, 1)) == PerformanceGoal()  # from its own day
    assert goal_for_day([], D) == MaintenanceGoal()
