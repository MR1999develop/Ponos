# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Builders of plan inputs for the tests: neutral data, one obvious default for everything."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace
from datetime import date

from ponos_coach.activities.domain import Sport
from ponos_coach.goals.domain import (
    FitnessGoal,
    GoalId,
    Priority,
    RaceGoal,
    SessionChoices,
    SessionType,
    WeeklyAvailability,
)
from ponos_coach.injury.domain import PlanInjury
from ponos_coach.load.domain import HeartRateLimits, Physiology, Sex, ThresholdHeartRate
from ponos_coach.planning.domain import FitnessState, PlanInputs

TODAY = date(2026, 3, 2)  # a Monday
ALL_TYPES = frozenset(SessionType)


def physiology() -> Physiology:
    """Test athlete: rest 50, max 190, threshold 170."""
    return Physiology(HeartRateLimits(50, 190), ThresholdHeartRate(170), Sex.MALE)


def race(
    goal_id: str,
    day: date,
    priority: Priority = Priority.A,
    distance_m: int = 10_000,
    target_time_s: int | None = None,
    sport: Sport = Sport.RUNNING,
) -> RaceGoal:
    """A race with a neutral id."""
    return RaceGoal(GoalId(goal_id), day, priority, sport, distance_m, target_time_s)


def fitness_goal(goal_id: str, day: date, ctl: float) -> FitnessGoal:
    """A fitness goal."""
    return FitnessGoal(GoalId(goal_id), day, ctl)


def availability(
    minutes: tuple[int, int, int, int, int, int, int] = (60, 60, 60, 60, 60, 60, 120),
    long_day: int = 6,
    max_week_minutes: int = 600,
) -> WeeklyAvailability:
    """Availability: an hour on weekdays, two on Sunday (the long day)."""
    return WeeklyAvailability(minutes, long_day, max_week_minutes)


BIG_WEEK = availability((120, 120, 120, 120, 120, 120, 180), 6, 1200)  # lots of time


def inputs(
    races: Iterable[RaceGoal] = (),
    fitness_goals: Iterable[FitnessGoal] = (),
    *,
    ctl: float = 40.0,
    atl: float = 40.0,
    types: Iterable[SessionType] = ALL_TYPES,
    avail: WeeklyAvailability | None = None,
    pace: int | None = 300,
    after_injury: bool = False,
    choices_for: Sport = Sport.RUNNING,
    injuries: Iterable[PlanInjury] = (),
) -> PlanInputs:
    """Plan inputs for the test athlete with the given goals."""
    return PlanInputs(
        races=tuple(races),
        fitness_goals=tuple(fitness_goals),
        availability=avail or availability(),
        choices=SessionChoices.of({choices_for: types}),
        physiology=physiology(),
        fitness=FitnessState(ctl, atl),
        threshold_pace_s_per_km=pace,
        after_injury=after_injury,
        injuries=tuple(injuries),
    )


def with_fitness(base: PlanInputs, ctl: float, atl: float) -> PlanInputs:
    """The same inputs with another PMC state."""
    return replace(base, fitness=FitnessState(ctl, atl))
