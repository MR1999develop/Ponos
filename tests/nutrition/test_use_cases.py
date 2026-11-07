# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Use cases of the nutrition settings, on the in-memory profile."""

from __future__ import annotations

from datetime import date, time

import pytest

from ponos_coach.nutrition.domain import (
    ActivityLevel,
    ActivityLevelEntry,
    GoalPeriod,
    MaintenanceGoal,
    MealTimes,
    PerformanceGoal,
    WeightLossGoal,
)
from ponos_coach.nutrition.ports import (
    ActivityLevelNotFoundError,
    GoalPeriodNotFoundError,
    GoalPeriodOverlapError,
)
from ponos_coach.nutrition.use_cases import (
    AddGoalPeriod,
    LevelDateInFutureError,
    ReadNutritionSettings,
    RemoveActivityLevel,
    RemoveGoalPeriod,
    ReplaceGoalPeriod,
    SetActivityLevel,
    SetMealTimes,
)
from ponos_coach.profiles.domain import ProfileId, ProfileName
from ponos_coach.profiles.ports import OpenProfile
from tests.builders import absolute_path
from tests.fakes.profile_storage import InMemoryProfileStorage

TODAY = date(2026, 3, 10)
MARCH = GoalPeriod(date(2026, 3, 1), date(2026, 3, 31), WeightLossGoal(500))
APRIL = GoalPeriod(date(2026, 4, 1), date(2026, 4, 30), PerformanceGoal())


class Rig:
    def __init__(self) -> None:
        self.profile: OpenProfile = InMemoryProfileStorage().create(
            absolute_path("p"),
            ProfileId.parse("00000000-0000-4000-8000-000000000001"),
            ProfileName.parse("Atleta di prova"),
            "Europe/Rome",
        )
        self.read = ReadNutritionSettings(
            self.profile.activity_levels, self.profile.meal_times, self.profile.goal_periods
        )
        self.set_level = SetActivityLevel(
            self.profile.activities, self.profile.activity_levels, lambda: TODAY
        )
        self.remove_level = RemoveActivityLevel(
            self.profile.activities, self.profile.activity_levels
        )
        self.add = AddGoalPeriod(self.profile.activities, self.profile.goal_periods)
        self.replace = ReplaceGoalPeriod(self.profile.activities, self.profile.goal_periods)
        self.remove = RemoveGoalPeriod(self.profile.activities, self.profile.goal_periods)


def test_a_new_profile_reads_empty_settings_with_the_default_meal_times() -> None:
    settings = Rig().read()

    assert settings.levels == ()
    assert settings.goal_periods == ()
    assert settings.meal_times == MealTimes.default()
    assert not settings.meal_times_set


def test_the_activity_level_is_saved_from_a_day_and_the_same_day_replaces_it() -> None:
    rig = Rig()

    rig.set_level(ActivityLevel.LIGHT, date(2026, 3, 1))
    rig.set_level(ActivityLevel.ACTIVE, date(2026, 3, 1))
    rig.set_level(ActivityLevel.SEDENTARY, TODAY)  # today is allowed

    assert rig.read().levels == (
        ActivityLevelEntry(date(2026, 3, 1), ActivityLevel.ACTIVE),
        ActivityLevelEntry(TODAY, ActivityLevel.SEDENTARY),
    )


def test_a_level_from_a_future_day_is_refused_and_nothing_is_written() -> None:
    rig = Rig()

    with pytest.raises(LevelDateInFutureError):
        rig.set_level(ActivityLevel.ACTIVE, date(2026, 3, 11))

    assert rig.read().levels == ()


def test_removing_a_level_entry_leaves_the_others_and_a_missing_one_is_an_error() -> None:
    rig = Rig()
    rig.set_level(ActivityLevel.LIGHT, date(2026, 3, 1))
    rig.set_level(ActivityLevel.ACTIVE, date(2026, 3, 5))

    rig.remove_level(date(2026, 3, 5))
    with pytest.raises(ActivityLevelNotFoundError):
        rig.remove_level(date(2026, 3, 5))

    assert rig.read().levels == (ActivityLevelEntry(date(2026, 3, 1), ActivityLevel.LIGHT),)


def test_the_meal_times_are_saved_and_read_back_as_set() -> None:
    rig = Rig()
    times = MealTimes(time(6, 30), time(12, 0), time(19, 0))

    SetMealTimes(rig.profile.meal_times)(times)

    settings = rig.read()
    assert settings.meal_times == times
    assert settings.meal_times_set


def test_goal_periods_are_added_and_listed_by_first_day() -> None:
    rig = Rig()

    rig.add(APRIL)
    rig.add(MARCH)

    assert rig.read().goal_periods == (MARCH, APRIL)


def test_an_overlapping_period_is_refused_and_changes_nothing() -> None:
    rig = Rig()
    rig.add(MARCH)

    with pytest.raises(GoalPeriodOverlapError):
        rig.add(GoalPeriod(date(2026, 3, 31), date(2026, 4, 5), MaintenanceGoal()))

    assert rig.read().goal_periods == (MARCH,)


def test_a_period_can_be_changed_in_place_without_clashing_with_itself() -> None:
    rig = Rig()
    rig.add(MARCH)
    longer = GoalPeriod(date(2026, 3, 1), date(2026, 3, 20), WeightLossGoal(300))

    rig.replace(MARCH.first_day, longer)

    assert rig.read().goal_periods == (longer,)


def test_a_period_can_be_moved_to_another_first_day() -> None:
    rig = Rig()
    rig.add(MARCH)
    moved = GoalPeriod(date(2026, 3, 5), date(2026, 3, 31), MARCH.goal)

    rig.replace(MARCH.first_day, moved)

    assert rig.read().goal_periods == (moved,)


def test_a_replacement_that_overlaps_another_period_leaves_both_as_they_were() -> None:
    rig = Rig()
    rig.add(MARCH)
    rig.add(APRIL)

    with pytest.raises(GoalPeriodOverlapError):
        rig.replace(MARCH.first_day, GoalPeriod(date(2026, 3, 1), date(2026, 4, 2), MARCH.goal))

    assert rig.read().goal_periods == (MARCH, APRIL)


def test_replacing_a_period_that_does_not_start_that_day_is_an_error() -> None:
    rig = Rig()
    rig.add(MARCH)

    with pytest.raises(GoalPeriodNotFoundError):
        rig.replace(date(2026, 3, 2), APRIL)

    assert rig.read().goal_periods == (MARCH,)


def test_a_removed_period_can_be_added_back_which_is_how_the_interface_undoes() -> None:
    rig = Rig()
    rig.add(MARCH)
    rig.add(APRIL)

    rig.remove(MARCH.first_day)
    assert rig.read().goal_periods == (APRIL,)
    rig.add(MARCH)

    assert rig.read().goal_periods == (MARCH, APRIL)


def test_removing_a_period_that_does_not_exist_is_an_error() -> None:
    with pytest.raises(GoalPeriodNotFoundError):
        Rig().remove(date(2026, 3, 1))
