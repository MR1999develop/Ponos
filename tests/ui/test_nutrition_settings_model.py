# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Rules of the nutrition settings form, without a window."""

from __future__ import annotations

from datetime import date

import pytest

from ponos_coach.nutrition.domain import (
    GoalPeriod,
    MaintenanceGoal,
    PerformanceGoal,
    WeightLossGoal,
)
from ponos_coach.ui.calendar_model import GoalKind
from ponos_coach.ui.nutrition_settings_model import (
    DEFAULT_PACE_G,
    MAX_PACE_G,
    MIN_PACE_G,
    MealField,
    PeriodProblem,
    goal_of,
    meal_time_problems,
    meal_times_of,
    pace_in_grams,
    pace_of,
    period_issue,
)

SAVED = [
    GoalPeriod(date(2026, 6, 8), date(2026, 6, 14), WeightLossGoal(500)),
    GoalPeriod(date(2026, 6, 20), None, MaintenanceGoal()),
]


def test_default_looking_meal_times_have_no_problem() -> None:
    assert meal_time_problems((7 * 60 + 30, 13 * 60, 20 * 60)) == frozenset()


@pytest.mark.parametrize(
    ("minutes", "problems"),
    [
        ((450, 450 + 119, 1200), {MealField.LUNCH}),  # one minute short of two hours
        ((450, 450 + 120, 1200), set()),  # exactly two hours is allowed
        ((450, 780, 780 + 119), {MealField.DINNER}),
        ((450, 400, 1200), {MealField.LUNCH}),  # lunch before breakfast
        ((450, 500, 520), {MealField.LUNCH, MealField.DINNER}),
    ],
)
def test_the_field_that_is_too_close_to_the_meal_before_it_has_the_problem(
    minutes: tuple[int, int, int], problems: set[MealField]
) -> None:
    assert meal_time_problems(minutes) == problems


def test_valid_minutes_become_domain_meal_times_and_invalid_ones_do_not() -> None:
    valid = meal_times_of((450, 780, 1200))

    assert valid is not None
    assert valid.minutes == (450, 780, 1200)
    assert meal_times_of((450, 500, 1200)) is None


def test_a_period_that_ends_before_it_starts_is_refused() -> None:
    issue = period_issue(date(2026, 7, 1), date(2026, 6, 30), SAVED)

    assert issue is not None
    assert issue.problem is PeriodProblem.LAST_BEFORE_FIRST


@pytest.mark.parametrize(
    ("first", "last", "other_first"),
    [
        (date(2026, 6, 14), date(2026, 6, 16), date(2026, 6, 8)),  # shares the last day
        (date(2026, 6, 1), date(2026, 6, 8), date(2026, 6, 8)),  # shares the first day
        (date(2026, 6, 9), date(2026, 6, 10), date(2026, 6, 8)),  # inside
        (date(2026, 6, 15), None, date(2026, 6, 20)),  # open-ended, runs into the next period
        (date(2026, 8, 1), date(2026, 8, 5), date(2026, 6, 20)),  # after an open-ended one
    ],
)
def test_overlaps_name_the_period_they_run_into(
    first: date, last: date | None, other_first: date
) -> None:
    issue = period_issue(first, last, SAVED)

    assert issue is not None
    assert issue.problem is PeriodProblem.OVERLAPS
    assert issue.other is not None
    assert issue.other.first_day == other_first


def test_adjacent_periods_do_not_overlap() -> None:
    assert period_issue(date(2026, 6, 15), date(2026, 6, 19), SAVED) is None


def test_the_period_being_changed_does_not_overlap_itself() -> None:
    assert period_issue(date(2026, 6, 9), date(2026, 6, 13), SAVED, date(2026, 6, 8)) is None
    assert period_issue(date(2026, 6, 9), date(2026, 6, 13), SAVED) is not None


@pytest.mark.parametrize(
    ("kilograms", "grams"),
    [(0.5, 500), (0.05, MIN_PACE_G), (3.0, MAX_PACE_G), (0.125, 125)],
)
def test_the_pace_in_kilograms_becomes_grams_inside_the_domain_limits(
    kilograms: float, grams: int
) -> None:
    assert pace_in_grams(kilograms) == grams


def test_goals_are_built_from_their_kind_and_only_weight_loss_has_a_pace() -> None:
    assert goal_of(GoalKind.WEIGHT_LOSS, 700) == WeightLossGoal(700)
    assert goal_of(GoalKind.WEIGHT_LOSS, 5000) == WeightLossGoal(MAX_PACE_G)
    assert goal_of(GoalKind.PERFORMANCE, 700) == PerformanceGoal()
    assert goal_of(GoalKind.MAINTENANCE, 700) == MaintenanceGoal()
    assert pace_of(WeightLossGoal(300)) == 300
    assert pace_of(PerformanceGoal()) == DEFAULT_PACE_G
