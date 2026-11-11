# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Rules of the nutrition settings form, testable without a window.

What the dialogs ask the user and when they refuse: meal times at least two hours apart (with the
problem on the field that is too close to the one before), goal periods that do not overlap and
whose end is not before their start, weight-loss paces within the domain's limits. The use cases
check the same rules again when saving; here the user hears about them while typing, on the field.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from enum import Enum

from ponos_coach.nutrition.domain import (
    GoalPeriod,
    MaintenanceGoal,
    MealTimes,
    NutritionGoal,
    PerformanceGoal,
    WeightLossGoal,
    defaults,
)
from ponos_coach.ui.calendar_model import GoalKind

MIN_MEAL_GAP_MIN = defaults.MIN_MEAL_GAP_MIN
MIN_PACE_G = defaults.MIN_WEEKLY_LOSS_G
MAX_PACE_G = defaults.MAX_WEEKLY_LOSS_G
DEFAULT_PACE_G = 500
_GRAMS_PER_KG = 1000


class MealField(Enum):
    """The three meal time fields, in the order of the day."""

    BREAKFAST = "breakfast"
    LUNCH = "lunch"
    DINNER = "dinner"


def meal_time_problems(minutes: tuple[int, int, int]) -> frozenset[MealField]:
    """The fields that are less than two hours after the meal before them (or before it).

    The first field never has a problem; an empty result means the times can be saved.
    """
    breakfast, lunch, dinner = minutes
    problems = set()
    if lunch - breakfast < MIN_MEAL_GAP_MIN:
        problems.add(MealField.LUNCH)
    if dinner - lunch < MIN_MEAL_GAP_MIN:
        problems.add(MealField.DINNER)
    return frozenset(problems)


def meal_times_of(minutes: tuple[int, int, int]) -> MealTimes | None:
    """The domain value for valid minutes after midnight, or `None` if a rule is broken."""
    if meal_time_problems(minutes):
        return None
    return MealTimes.from_minutes(*minutes)


class PeriodProblem(Enum):
    """Why a goal period cannot be saved."""

    LAST_BEFORE_FIRST = "last_before_first"
    OVERLAPS = "overlaps"


@dataclass(frozen=True, slots=True)
class PeriodIssue:
    """A problem with a period; `other` is the saved period it overlaps, if that is the problem."""

    problem: PeriodProblem
    other: GoalPeriod | None = None


def period_issue(
    first_day: date,
    last_day: date | None,
    saved: Sequence[GoalPeriod],
    editing_first_day: date | None = None,
) -> PeriodIssue | None:
    """What is wrong with a period dates, if anything.

    `editing_first_day` is the first day of the period being edited: it does not count as another
    period. The dates are checked against the saved periods only; the goal does not matter.
    """
    if last_day is not None and last_day < first_day:
        return PeriodIssue(PeriodProblem.LAST_BEFORE_FIRST)
    candidate = GoalPeriod(first_day, last_day, MaintenanceGoal())
    for period in saved:
        if period.first_day != editing_first_day and candidate.overlaps(period):
            return PeriodIssue(PeriodProblem.OVERLAPS, period)
    return None


def pace_in_grams(kilograms_per_week: float) -> int:
    """A pace in kg per week as whole grams, kept inside the domain's limits."""
    grams = round(kilograms_per_week * _GRAMS_PER_KG)
    return min(max(grams, MIN_PACE_G), MAX_PACE_G)


def goal_of(kind: GoalKind, weekly_loss_g: int) -> NutritionGoal:
    """The domain goal of a kind; the pace only matters for weight loss."""
    match kind:
        case GoalKind.WEIGHT_LOSS:
            return WeightLossGoal(min(max(weekly_loss_g, MIN_PACE_G), MAX_PACE_G))
        case GoalKind.PERFORMANCE:
            return PerformanceGoal()
        case GoalKind.MAINTENANCE:
            return MaintenanceGoal()


def pace_of(goal: NutritionGoal) -> int:
    """The pace of a goal in grams per week (the default for goals that have none)."""
    return goal.weekly_loss_g if isinstance(goal, WeightLossGoal) else DEFAULT_PACE_G
