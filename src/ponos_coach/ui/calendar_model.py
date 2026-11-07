# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Presentation model of the month view: which days there are and what each one shows.

Pure and without Qt: it works out the weeks of a month (Monday first), what each past day holds
(sports in order of start, total load) and which nutrition goal period covers it. The domain gives
categories (`GoalKind`); the page chooses colours and words. Days after today are not clickable:
they will carry the plan in a later phase.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from enum import Enum

from ponos_coach.activities.domain import Sport
from ponos_coach.load.use_cases import ProfileActivity
from ponos_coach.nutrition.domain import (
    GoalPeriod,
    MaintenanceGoal,
    NutritionGoal,
    PerformanceGoal,
    WeightLossGoal,
)
from ponos_coach.nutrition.domain.settings import MIN_VALID_DATE

DAYS_PER_WEEK = 7


class GoalKind(Enum):
    """The kind of nutrition goal of a period; the order of the members is the legend order."""

    WEIGHT_LOSS = "weight_loss"
    PERFORMANCE = "performance"
    MAINTENANCE = "maintenance"


def goal_kind_of(goal: NutritionGoal) -> GoalKind:
    """The category of a goal."""
    match goal:
        case WeightLossGoal():
            return GoalKind.WEIGHT_LOSS
        case PerformanceGoal():
            return GoalKind.PERFORMANCE
        case MaintenanceGoal():
            return GoalKind.MAINTENANCE


@dataclass(frozen=True, slots=True)
class CalendarDay:
    """A cell of the month grid.

    `sports` are the distinct sports of the day's activities in order of start; `goal` is the kind
    of the period covering the day (`None` when no period covers it). A day outside the shown month
    has no data and cannot be opened; neither can a day after today.
    """

    day: date
    in_month: bool
    is_today: bool
    is_future: bool
    activity_count: int
    sports: tuple[Sport, ...]
    load: float
    goal: GoalKind | None

    @property
    def can_open(self) -> bool:
        """The day belongs to the month and is not after today."""
        return self.in_month and not self.is_future


@dataclass(frozen=True, slots=True)
class MonthView:
    """The weeks of a month, each with seven days from Monday to Sunday."""

    first_day: date
    weeks: tuple[tuple[CalendarDay, ...], ...]

    @property
    def days(self) -> tuple[CalendarDay, ...]:
        """Every cell, week after week."""
        return tuple(day for week in self.weeks for day in week)

    def cell_of(self, day: date) -> tuple[int, int] | None:
        """Row and column of a day of the shown month, or `None` if it is not in the grid."""
        for row, week in enumerate(self.weeks):
            for column, cell in enumerate(week):
                if cell.day == day:
                    return (row, column)
        return None


def first_of_month(day: date) -> date:
    """The first day of the month that holds `day`."""
    return day.replace(day=1)


def shift_month(first_day: date, months: int) -> date:
    """The first day of the month `months` away, not before the earliest valid date."""
    index = first_day.year * 12 + (first_day.month - 1) + months
    year, month = divmod(index, 12)
    try:
        shifted = date(year, month + 1, 1)
    except ValueError:  # year 0 or beyond 9999
        return first_day
    return max(shifted, first_of_month(MIN_VALID_DATE))


def _goal_of(day: date, periods: Sequence[GoalPeriod]) -> GoalKind | None:
    for period in periods:
        if period.contains(day):
            return goal_kind_of(period.goal)
    return None


def _grid_days(first_day: date) -> list[date]:
    """The days of the weeks that cover the month, from the Monday on or before its first day."""
    start = first_day - timedelta(days=first_day.weekday())
    next_month = shift_month(first_day, 1)
    end = next_month + timedelta(days=(DAYS_PER_WEEK - next_month.weekday()) % DAYS_PER_WEEK)
    return [start + timedelta(days=offset) for offset in range((end - start).days)]


def build_month(
    first_day: date,
    today: date,
    activities: Sequence[ProfileActivity],
    periods: Sequence[GoalPeriod],
) -> MonthView:
    """The month that starts on `first_day` (which must be a first of the month)."""
    if first_day.day != 1:
        raise ValueError("a month starts on its first day")
    by_day: dict[date, list[ProfileActivity]] = defaultdict(list)
    for activity in sorted(activities, key=lambda a: a.start):
        by_day[activity.local_day].append(activity)
    cells = []
    for day in _grid_days(first_day):
        in_month = day.month == first_day.month and day.year == first_day.year
        todays = by_day.get(day, []) if in_month else []
        sports = tuple(dict.fromkeys(a.sport for a in todays))
        cells.append(
            CalendarDay(
                day,
                in_month,
                day == today,
                day > today,
                len(todays),
                sports,
                sum(a.load.load for a in todays),
                _goal_of(day, periods) if in_month else None,
            )
        )
    weeks = tuple(tuple(cells[i : i + DAYS_PER_WEEK]) for i in range(0, len(cells), DAYS_PER_WEEK))
    return MonthView(first_day, weeks)
