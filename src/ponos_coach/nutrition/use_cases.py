# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Use cases of the nutrition settings: activity level, meal times, goal periods.

Each change is a command saved in one transaction. Reading everything for the settings page is
`ReadNutritionSettings`. Advice for a day is computed by `profiles.PlanDayNutrition`, which also
needs the profile's body data.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date

from ponos_coach.activities.ports import ActivityIndex
from ponos_coach.nutrition.domain import ActivityLevel, ActivityLevelEntry, GoalPeriod, MealTimes
from ponos_coach.nutrition.ports import (
    ActivityLevelStore,
    GoalPeriodNotFoundError,
    GoalPeriodOverlapError,
    GoalPeriodStore,
    MealTimesStore,
)


class LevelDateInFutureError(ValueError):
    """The activity level is said to hold from a day that has not come yet."""


@dataclass(frozen=True, slots=True)
class NutritionSettings:
    """What the settings page shows: levels by ascending date, the meal times in force (the
    defaults if never set; `meal_times_set` says which) and the goal periods by first day."""

    levels: tuple[ActivityLevelEntry, ...]
    meal_times: MealTimes
    meal_times_set: bool
    goal_periods: tuple[GoalPeriod, ...]


class ReadNutritionSettings:
    """Reads the nutrition settings of the open profile."""

    def __init__(
        self, levels: ActivityLevelStore, meal_times: MealTimesStore, goals: GoalPeriodStore
    ) -> None:
        self._levels = levels
        self._meal_times = meal_times
        self._goals = goals

    def __call__(self) -> NutritionSettings:
        """The settings.

        Raises:
            NutritionStoreError: unreadable data.
        """
        saved = self._meal_times.get()
        return NutritionSettings(
            tuple(self._levels.list_entries()),
            MealTimes.default() if saved is None else saved,
            saved is not None,
            tuple(self._goals.list_periods()),
        )


class SetActivityLevel:
    """Sets the activity level outside sport from a day (included).

    The same day replaces the entry. `today` (today's date in the profile's time zone) is injected
    by the Composition Root.
    """

    def __init__(
        self, activities: ActivityIndex, store: ActivityLevelStore, today: Callable[[], date]
    ) -> None:
        self._activities = activities
        self._store = store
        self._today = today

    def __call__(self, level: ActivityLevel, valid_from: date) -> None:
        """Saves the level.

        Raises:
            LevelDateInFutureError: `valid_from` is after today; nothing was written.
            ValueError: `valid_from` is earlier than 1900-01-01.
            NutritionStoreError: write failed; nothing changes.
        """
        if valid_from > self._today():
            raise LevelDateInFutureError("the validity day is after today")
        entry = ActivityLevelEntry(valid_from, level)
        with self._activities.transaction():
            self._store.put(entry)


class RemoveActivityLevel:
    """Removes the activity level entry of a day (the days it covered fall back to the entry before
    it, or to the assumed level if there is none)."""

    def __init__(self, activities: ActivityIndex, store: ActivityLevelStore) -> None:
        self._activities = activities
        self._store = store

    def __call__(self, valid_from: date) -> None:
        """Deletes the entry that starts on `valid_from`.

        Raises:
            ActivityLevelNotFoundError: there is none.
            NutritionStoreError: write failed. Nothing changes.
        """
        with self._activities.transaction():
            self._store.remove(valid_from)


class SetMealTimes:
    """Sets the typical breakfast, lunch and dinner times of the profile."""

    def __init__(self, store: MealTimesStore) -> None:
        self._store = store

    def __call__(self, times: MealTimes) -> None:
        """Saves the times.

        Raises:
            NutritionStoreError: write failed; the previous times stay.
        """
        self._store.set(times)


def _check_free(period: GoalPeriod, others: Sequence[GoalPeriod]) -> None:
    if any(period.overlaps(other) for other in others):
        raise GoalPeriodOverlapError("the period overlaps a saved one")


class AddGoalPeriod:
    """Adds a nutrition goal period; it cannot overlap a saved one."""

    def __init__(self, activities: ActivityIndex, store: GoalPeriodStore) -> None:
        self._activities = activities
        self._store = store

    def __call__(self, period: GoalPeriod) -> None:
        """Saves the period.

        Raises:
            GoalPeriodOverlapError: it shares a day with a saved period; nothing changes.
            NutritionStoreError: write failed; nothing changes.
        """
        with self._activities.transaction():
            _check_free(period, self._store.list_periods())
            self._store.add(period)


class ReplaceGoalPeriod:
    """Changes a goal period (dates and goal), all or nothing: the old one is removed and the new
    one added in one transaction, so a failure leaves the old one in place."""

    def __init__(self, activities: ActivityIndex, store: GoalPeriodStore) -> None:
        self._activities = activities
        self._store = store

    def __call__(self, old_first_day: date, new: GoalPeriod) -> None:
        """Replaces the period that starts on `old_first_day` with `new`.

        Raises:
            GoalPeriodNotFoundError: there is no period starting on `old_first_day`.
            GoalPeriodOverlapError: `new` shares a day with another saved period.
            NutritionStoreError: write failed. In every case nothing changes.
        """
        with self._activities.transaction():
            saved = self._store.list_periods()
            others = [p for p in saved if p.first_day != old_first_day]
            if len(others) == len(saved):
                raise GoalPeriodNotFoundError("no period starts on that day")
            _check_free(new, others)
            self._store.remove(old_first_day)
            self._store.add(new)


class RemoveGoalPeriod:
    """Removes a goal period (the days it covered go back to maintenance)."""

    def __init__(self, activities: ActivityIndex, store: GoalPeriodStore) -> None:
        self._activities = activities
        self._store = store

    def __call__(self, first_day: date) -> None:
        """Deletes the period that starts on `first_day`.

        Raises:
            GoalPeriodNotFoundError: there is none.
            NutritionStoreError: write failed. Nothing changes.
        """
        with self._activities.transaction():
            self._store.remove(first_day)
