# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""What the plan engine is given, and why it may refuse."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date
from enum import Enum

from ponos_coach.activities.domain import Sport
from ponos_coach.assessment.domain import DEFAULT_PROTOCOL, NoTest, RetestStatus, ThresholdProtocol
from ponos_coach.goals.domain import (
    FitnessGoal,
    Priority,
    RaceGoal,
    SessionChoices,
    WeeklyAvailability,
)
from ponos_coach.injury.domain import PlanInjury
from ponos_coach.load.domain import Physiology

MAX_PACE_S_PER_KM = 3600
MIN_PACE_S_PER_KM = 90


@dataclass(frozen=True, slots=True)
class FitnessState:
    """CTL and ATL at the end of today (from the PMC), both finite and non-negative."""

    ctl: float
    atl: float

    def __post_init__(self) -> None:
        if not all(math.isfinite(v) and v >= 0 for v in (self.ctl, self.atl)):
            raise ValueError("CTL and ATL must be finite and non-negative")

    @property
    def tsb_tomorrow(self) -> float:
        """The form tomorrow morning: today's CTL minus today's ATL."""
        return self.ctl - self.atl


@dataclass(frozen=True, slots=True)
class PlanInputs:
    """Everything a plan is computed from, besides today's date.

    `threshold_pace_s_per_km` is the pace of the last threshold test, `None` without one.
    `injuries` are the open injuries and illnesses
    (what the plan needs of them, no free text); `after_injury` says one was closed recently.
    Either lowers the maximum ramp (`ramp_reduced`). `retest` says where today stands against the
    threshold-test window.
    `test_protocol` is the protocol the user chose for the test the plan schedules; `missed_tests`
    are the days on which the plan had a test that was not done (see `missed_threshold_tests`).
    """

    races: tuple[RaceGoal, ...]
    fitness_goals: tuple[FitnessGoal, ...]
    availability: WeeklyAvailability
    choices: SessionChoices
    physiology: Physiology
    fitness: FitnessState
    threshold_pace_s_per_km: int | None = None
    after_injury: bool = False
    retest: RetestStatus = field(default_factory=NoTest)
    injuries: tuple[PlanInjury, ...] = ()
    test_protocol: ThresholdProtocol = DEFAULT_PROTOCOL
    missed_tests: tuple[date, ...] = ()

    def __post_init__(self) -> None:
        pace = self.threshold_pace_s_per_km
        if pace is not None and not MIN_PACE_S_PER_KM <= pace <= MAX_PACE_S_PER_KM:
            raise ValueError("threshold pace outside the plausible range")
        ids = [injury.injury_id.value for injury in self.injuries]
        if len(set(ids)) != len(ids):
            raise ValueError("an injury is given twice")
        if len(set(self.missed_tests)) != len(self.missed_tests):
            raise ValueError("a missed test is given twice")

    @property
    def ramp_reduced(self) -> bool:
        """True if the weekly ramp is the lower one: an open injury or illness, or one closed
        recently."""
        return self.after_injury or bool(self.injuries)

    def normalised(self) -> PlanInputs:
        """The same inputs with the goals in a stable order (date, priority, id): the order they
        were given in never matters."""
        races = tuple(sorted(self.races, key=lambda r: (r.day, r.priority.value, r.goal_id.value)))
        fitness = tuple(sorted(self.fitness_goals, key=lambda g: (g.day, g.goal_id.value)))
        return PlanInputs(
            races,
            fitness,
            self.availability,
            self.choices,
            self.physiology,
            self.fitness,
            self.threshold_pace_s_per_km,
            self.after_injury,
            self.retest,
            tuple(sorted(self.injuries, key=lambda i: i.injury_id.value)),
            self.test_protocol,
            tuple(sorted(self.missed_tests)),
        )

    def plan_sport(self) -> Sport:
        """The sport the plan trains: that of the first A race, else of the first race, else
        running."""
        for priority in (Priority.A, None):
            for race in sorted(self.races, key=lambda r: (r.day, r.goal_id.value)):
                if priority is None or race.priority is priority:
                    return race.sport
        return Sport.RUNNING


class RefusalReason(Enum):
    """Why no plan is built. The value is a stable identifier."""

    NO_GOALS = "no_goals"
    GOAL_NOT_IN_FUTURE = "goal_not_in_future"  # `goal_ids` lists them
    GOALS_ON_SAME_DAY = "goals_on_same_day"  # two races on one day
    NO_AVAILABILITY = "no_availability"
    NO_SESSION_TYPES = "no_session_types"  # nothing chosen for the sport of the plan


@dataclass(frozen=True, slots=True)
class PlanRefusal:
    """No plan, and the reason; `goal_ids` are the goals concerned (possibly none)."""

    reason: RefusalReason
    goal_ids: tuple[str, ...] = ()
