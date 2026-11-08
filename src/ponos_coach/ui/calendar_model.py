# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Presentation model of the month view: which days there are and what each one shows.

Pure and without Qt: it works out the weeks of a month (Monday first), what each past day holds
(sports in order of start, total load) and which nutrition goal period covers it; from the plan
layer, what was or is planned (session, phase, race), how it went against what was done and which
goals fall on it. The domain gives categories (`GoalKind`, `Phase`); the page chooses colours and
words. Every day of the month can be selected; only a day up to today holds a day page.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from enum import Enum

from ponos_coach.activities.domain import Sport
from ponos_coach.goals.ports import Goal
from ponos_coach.load.use_cases import ProfileActivity
from ponos_coach.nutrition.domain import (
    GoalPeriod,
    MaintenanceGoal,
    NutritionGoal,
    PerformanceGoal,
    WeightLossGoal,
)
from ponos_coach.nutrition.domain.settings import MIN_VALID_DATE
from ponos_coach.planning.domain import DayAdherence, Plan, PlanDay, TargetAssessment
from ponos_coach.planning.ports import FrozenDay
from ponos_coach.ui.injury_presentation import ProblemSpan

DAYS_PER_WEEK = 7
MONTHS_AHEAD = 12  # the plan reaches 52 weeks: the calendar opens a year ahead of today


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
class DayGoal:
    """A goal that falls on a day, with the verdict on its target time (races with a plan)."""

    goal: Goal
    assessment: TargetAssessment | None = None


@dataclass(frozen=True, slots=True)
class PlanLayer:
    """What the plan says, by day: the planned days (future ones from the current plan, past ones
    as they were planned at the time), the planned-against-done of the past days and the goals."""

    planned: Mapping[date, PlanDay] = field(default_factory=dict)
    adherence: Mapping[date, DayAdherence] = field(default_factory=dict)
    goals: Mapping[date, tuple[DayGoal, ...]] = field(default_factory=dict)


def plan_layer_of(
    plan: Plan | None,
    past: Sequence[FrozenDay],
    adherence: Sequence[DayAdherence],
    goals: Sequence[Goal],
) -> PlanLayer:
    """The layer from the current plan (if any), the past days as planned then, how they went and
    the goals. A day frozen in the past wins over the current plan's day."""
    planned: dict[date, PlanDay] = {}
    assessments: dict[str, TargetAssessment] = {}
    if plan is not None:
        planned.update({item.day: item for item in plan.days})
        assessments = {item.goal_id: item for item in plan.targets}
    planned.update({frozen.day.day: frozen.day for frozen in past})
    by_day: dict[date, list[DayGoal]] = defaultdict(list)
    for goal in goals:
        by_day[goal.day].append(DayGoal(goal, assessments.get(goal.goal_id.value)))
    return PlanLayer(
        planned,
        {item.day: item for item in adherence},
        {day: tuple(items) for day, items in by_day.items()},
    )


@dataclass(frozen=True, slots=True)
class CalendarDay:
    """A cell of the month grid.

    `sports` are the distinct sports of the day's activities in order of start; `goal` is the kind
    of the period covering the day (`None` when no period covers it). `plan` is the day as
    planned, `adherence` how it went (past days), `goals` the goals of the day and `problems` the
    injuries and illnesses present on it (an open one reaches today). A day outside
    the shown month has no data and cannot be selected; a day after today can be selected and has
    a day page only if the plan covers it.
    """

    day: date
    in_month: bool
    is_today: bool
    is_future: bool
    activity_count: int
    sports: tuple[Sport, ...]
    load: float
    goal: GoalKind | None
    plan: PlanDay | None = None
    adherence: DayAdherence | None = None
    goals: tuple[DayGoal, ...] = ()
    problems: tuple[ProblemSpan, ...] = ()

    @property
    def can_open(self) -> bool:
        """The day belongs to the month and is not after today, or is a day of the plan: it holds
        a day page (for a future day, an estimate from the plan)."""
        return self.in_month and (not self.is_future or self.plan is not None)

    @property
    def can_select(self) -> bool:
        """The day belongs to the month."""
        return self.in_month


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


def grid_range(first_day: date) -> tuple[date, date]:
    """The first and the last day shown by the grid of a month (whole weeks)."""
    days = _grid_days(first_day)
    return (days[0], days[-1])


def last_month_shown(today: date) -> date:
    """The first day of the last month the calendar can show."""
    return shift_month(first_of_month(today), MONTHS_AHEAD)


def build_month(
    first_day: date,
    today: date,
    activities: Sequence[ProfileActivity],
    periods: Sequence[GoalPeriod],
    layer: PlanLayer | None = None,
    problems: Sequence[ProblemSpan] = (),
) -> MonthView:
    """The month that starts on `first_day` (which must be a first of the month)."""
    if first_day.day != 1:
        raise ValueError("a month starts on its first day")
    by_day: dict[date, list[ProfileActivity]] = defaultdict(list)
    for activity in sorted(activities, key=lambda a: a.start):
        by_day[activity.local_day].append(activity)
    layer = layer or PlanLayer()
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
                layer.planned.get(day) if in_month else None,
                layer.adherence.get(day) if in_month else None,
                layer.goals.get(day, ()) if in_month else (),
                tuple(span for span in problems if span.covers(day, today)) if in_month else (),
            )
        )
    weeks = tuple(tuple(cells[i : i + DAYS_PER_WEEK]) for i in range(0, len(cells), DAYS_PER_WEEK))
    return MonthView(first_day, weeks)
