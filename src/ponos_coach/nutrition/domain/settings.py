# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""What the user chooses for nutrition: activity level, typical meal times, goal periods.

These are health-related data: they never appear in error messages or logs.
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, time
from enum import Enum

from ponos_coach.nutrition.domain import defaults

MIN_VALID_DATE = date(1900, 1, 1)


class ActivityLevel(Enum):
    """Daily activity outside sport; the value is a stable identifier (used in saved data)."""

    SEDENTARY = "sedentary"
    LIGHT = "light"
    ACTIVE = "active"
    VERY_ACTIVE = "very_active"

    @property
    def factor(self) -> float:
        """Multiplier of the basal metabolic rate for the day outside sport."""
        return _PAL[self]


_PAL = {
    ActivityLevel.SEDENTARY: defaults.PAL_SEDENTARY,
    ActivityLevel.LIGHT: defaults.PAL_LIGHT,
    ActivityLevel.ACTIVE: defaults.PAL_ACTIVE,
    ActivityLevel.VERY_ACTIVE: defaults.PAL_VERY_ACTIVE,
}


@dataclass(frozen=True, slots=True)
class ActivityLevelEntry:
    """Activity level valid from a day (included) until the next entry."""

    valid_from: date
    level: ActivityLevel

    def __post_init__(self) -> None:
        if self.valid_from < MIN_VALID_DATE:
            raise ValueError("the validity date cannot be earlier than 1900-01-01")


def level_for_day(entries: Sequence[ActivityLevelEntry], day: date) -> ActivityLevel | None:
    """Level valid on a day: the entry with the latest date not after it, None before the first.

    `entries` is sorted by ascending date, one entry per date (as the store gives them).
    """
    index = bisect_right([entry.valid_from for entry in entries], day) - 1
    return None if index < 0 else entries[index].level


def _minutes(moment: time) -> int:
    return moment.hour * 60 + moment.minute


@dataclass(frozen=True, slots=True)
class MealTimes:
    """Typical breakfast, lunch and dinner times, whole minutes, in increasing order and each at
    least two hours after the previous one."""

    breakfast: time
    lunch: time
    dinner: time

    def __post_init__(self) -> None:
        for moment in (self.breakfast, self.lunch, self.dinner):
            if moment.second or moment.microsecond or moment.tzinfo is not None:
                raise ValueError("meal times are whole minutes of a local day")
        if (
            _minutes(self.lunch) - _minutes(self.breakfast) < defaults.MIN_MEAL_GAP_MIN
            or _minutes(self.dinner) - _minutes(self.lunch) < defaults.MIN_MEAL_GAP_MIN
        ):
            raise ValueError("meals must be in order and at least two hours apart")

    @classmethod
    def default(cls) -> MealTimes:
        """The default times (07:30, 13:00, 20:00)."""
        return cls.from_minutes(*defaults.DEFAULT_MEAL_MINUTES)

    @classmethod
    def from_minutes(cls, breakfast: int, lunch: int, dinner: int) -> MealTimes:
        """Times from minutes after midnight.

        Raises:
            ValueError: a value outside the day, or meals out of order or too close.
        """
        for value in (breakfast, lunch, dinner):
            if not 0 <= value <= defaults.LAST_MINUTE_OF_DAY:
                raise ValueError("minutes after midnight must be within the day")
        return cls(*(time(value // 60, value % 60) for value in (breakfast, lunch, dinner)))

    @property
    def minutes(self) -> tuple[int, int, int]:
        """Breakfast, lunch and dinner as minutes after midnight."""
        return (_minutes(self.breakfast), _minutes(self.lunch), _minutes(self.dinner))


@dataclass(frozen=True, slots=True)
class MaintenanceGoal:
    """Keep the weight: energy intake equal to expenditure."""


@dataclass(frozen=True, slots=True)
class PerformanceGoal:
    """Support training: energy intake equal to expenditure, carbohydrates at the top of the band
    for the day's load."""


@dataclass(frozen=True, slots=True)
class WeightLossGoal:
    """Lose weight at a pace in grams per week, between 100 and 1000.

    The pace is what the user asks for; the model always applies its safety limits on top.
    """

    weekly_loss_g: int

    def __post_init__(self) -> None:
        if not defaults.MIN_WEEKLY_LOSS_G <= self.weekly_loss_g <= defaults.MAX_WEEKLY_LOSS_G:
            raise ValueError(
                f"the weekly loss must be between {defaults.MIN_WEEKLY_LOSS_G} "
                f"and {defaults.MAX_WEEKLY_LOSS_G} g"
            )


type NutritionGoal = MaintenanceGoal | PerformanceGoal | WeightLossGoal


@dataclass(frozen=True, slots=True)
class GoalPeriod:
    """A goal valid from `first_day` to `last_day`, both included; no `last_day` means until the
    next period (open-ended)."""

    first_day: date
    last_day: date | None
    goal: NutritionGoal

    def __post_init__(self) -> None:
        if self.first_day < MIN_VALID_DATE:
            raise ValueError("the first day cannot be earlier than 1900-01-01")
        if self.last_day is not None and self.last_day < self.first_day:
            raise ValueError("the last day cannot be before the first day")

    def contains(self, day: date) -> bool:
        """True if `day` is in the period."""
        return self.first_day <= day and (self.last_day is None or day <= self.last_day)

    def overlaps(self, other: GoalPeriod) -> bool:
        """True if the two periods share at least one day."""
        ends_before = self.last_day is not None and self.last_day < other.first_day
        other_ends_before = other.last_day is not None and other.last_day < self.first_day
        return not (ends_before or other_ends_before)


def goal_for_day(periods: Sequence[GoalPeriod], day: date) -> NutritionGoal:
    """The goal of a day: that of the period containing it, otherwise maintenance."""
    for period in periods:
        if period.contains(day):
            return period.goal
    return MaintenanceGoal()
