# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""The month grid, without a window: weeks, past and future days, sports, load and goal periods."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from ponos_coach.activities.domain import Sport
from ponos_coach.load.use_cases import ProfileActivity
from ponos_coach.nutrition.domain import (
    GoalPeriod,
    MaintenanceGoal,
    PerformanceGoal,
    WeightLossGoal,
)
from ponos_coach.ui.calendar_model import (
    GoalKind,
    build_month,
    first_of_month,
    goal_kind_of,
    shift_month,
)
from ponos_coach.ui.tokens import Theme, goal_tints_for, tokens_for
from tests.ui.helpers import profile_activity

JUNE = date(2026, 6, 1)  # a Monday


def run_at(day: date, hour: int, load: float, sport: Sport = Sport.RUNNING) -> ProfileActivity:
    return profile_activity(
        datetime(day.year, day.month, day.day, hour, 0, tzinfo=UTC), sport=sport, load=load
    )


def test_a_month_is_a_list_of_whole_weeks_from_monday_to_sunday() -> None:
    month = build_month(JUNE, date(2026, 6, 15), [], [])

    assert len(month.weeks) == 5
    assert all(len(week) == 7 for week in month.weeks)
    assert month.weeks[0][0].day == date(2026, 6, 1)  # June 2026 starts on a Monday
    assert month.weeks[-1][-1].day == date(2026, 7, 5)
    assert [d.in_month for d in month.weeks[-1]] == [True, True, False, False, False, False, False]


def test_a_month_that_starts_midweek_begins_on_the_monday_before() -> None:
    month = build_month(date(2026, 7, 1), date(2026, 7, 20), [], [])

    assert month.weeks[0][0].day == date(2026, 6, 29)
    assert month.weeks[0][2].day == date(2026, 7, 1)
    assert [d.in_month for d in month.weeks[0]] == [False, False, True, True, True, True, True]


def test_only_past_days_of_the_month_can_be_opened_and_today_can() -> None:
    today = date(2026, 6, 15)
    days = {cell.day: cell for cell in build_month(JUNE, today, [], []).days}

    assert days[date(2026, 6, 14)].can_open
    assert days[today].can_open
    assert days[today].is_today
    assert days[date(2026, 6, 16)].is_future
    assert not days[date(2026, 6, 16)].can_open
    assert not days[date(2026, 7, 1)].can_open  # outside the month


def test_a_day_lists_its_sports_in_order_of_start_and_adds_up_the_load() -> None:
    day = date(2026, 6, 10)
    activities = [
        run_at(day, 17, 40.0, Sport.CYCLING),
        run_at(day, 7, 30.0, Sport.RUNNING),
        run_at(day, 18, 20.0, Sport.CYCLING),
        run_at(date(2026, 6, 11), 7, 99.0),
    ]

    cell = next(
        c for c in build_month(JUNE, date(2026, 6, 30), activities, []).days if c.day == day
    )

    assert cell.sports == (Sport.RUNNING, Sport.CYCLING)
    assert cell.activity_count == 3
    assert cell.load == pytest.approx(90.0)


def test_a_day_without_activities_has_no_sports_and_no_load() -> None:
    cell = build_month(JUNE, date(2026, 6, 30), [], []).days[3]

    assert (cell.activity_count, cell.sports, cell.load) == (0, (), 0.0)


def test_the_goal_period_of_a_day_gives_its_kind_and_days_outside_have_none() -> None:
    periods = [
        GoalPeriod(date(2026, 6, 8), date(2026, 6, 14), WeightLossGoal(500)),
        GoalPeriod(date(2026, 6, 20), None, PerformanceGoal()),
    ]

    goals = {c.day: c.goal for c in build_month(JUNE, date(2026, 6, 30), [], periods).days}

    assert goals[date(2026, 6, 7)] is None
    assert goals[date(2026, 6, 8)] is GoalKind.WEIGHT_LOSS
    assert goals[date(2026, 6, 14)] is GoalKind.WEIGHT_LOSS
    assert goals[date(2026, 6, 15)] is None
    assert goals[date(2026, 6, 30)] is GoalKind.PERFORMANCE


def test_goal_kinds_follow_the_domain_goals() -> None:
    assert goal_kind_of(MaintenanceGoal()) is GoalKind.MAINTENANCE
    assert goal_kind_of(PerformanceGoal()) is GoalKind.PERFORMANCE
    assert goal_kind_of(WeightLossGoal(300)) is GoalKind.WEIGHT_LOSS


def test_a_month_starts_on_its_first_day() -> None:
    with pytest.raises(ValueError, match="first day"):
        build_month(date(2026, 6, 2), date(2026, 6, 30), [], [])


@pytest.mark.parametrize(
    ("start", "step", "expected"),
    [
        (date(2026, 6, 1), 1, date(2026, 7, 1)),
        (date(2026, 12, 1), 1, date(2027, 1, 1)),
        (date(2026, 1, 1), -1, date(2025, 12, 1)),
        (date(2026, 6, 1), -18, date(2024, 12, 1)),
        (date(1900, 1, 1), -1, date(1900, 1, 1)),  # never before the earliest valid date
    ],
)
def test_shifting_a_month_crosses_years_and_stops_at_the_earliest_date(
    start: date, step: int, expected: date
) -> None:
    assert shift_month(start, step) == expected


def test_first_of_month() -> None:
    assert first_of_month(date(2026, 6, 17)) == date(2026, 6, 1)


@pytest.mark.parametrize("theme", list(Theme))
def test_goal_tints_are_distinct_not_warm_and_keep_the_text_readable(theme: Theme) -> None:
    tints = goal_tints_for(theme)
    text = tokens_for(theme).text

    assert len(set(tints)) == 3
    for tint in tints:
        red, green, blue = (int(tint[i : i + 2], 16) for i in (1, 3, 5))
        assert red <= max(green, blue) + 8  # never red or orange
        assert _contrast(text, tint) >= 4.5


def _luminance(color: str) -> float:
    def channel(value: int) -> float:
        fraction = value / 255
        return fraction / 12.92 if fraction <= 0.03928 else ((fraction + 0.055) / 1.055) ** 2.4

    red, green, blue = (channel(int(color[i : i + 2], 16)) for i in (1, 3, 5))
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def _contrast(first: str, second: str) -> float:
    light, dark = sorted((_luminance(first), _luminance(second)), reverse=True)
    return (light + 0.05) / (dark + 0.05)
