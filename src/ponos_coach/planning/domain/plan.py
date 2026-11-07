# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""The plan: what the engine returns. A value, versioned, with a canonical serialisation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum

from ponos_coach.activities.domain import Sport
from ponos_coach.goals.domain import Priority, SessionType
from ponos_coach.planning.domain.inputs import PlanInputs
from ponos_coach.planning.domain.phases import Phase
from ponos_coach.planning.domain.serialization import canonical_json
from ponos_coach.planning.domain.target import TargetClass


class BlockRole(Enum):
    """Role of a block of a session. Work and rest with the same `repeats` alternate, starting
    with work (the rest has one repetition less)."""

    WARMUP = "warmup"
    WORK = "work"
    REST = "rest"
    COOLDOWN = "cooldown"
    STEADY = "steady"


@dataclass(frozen=True, slots=True)
class Block:
    """A part of a session: `minutes` (each repetition) in zone `zone` (1 to 5).

    The HR range is in whole bpm (both ends included); the pace range in whole s/km, slowest
    first, and `None` where the zone has no end (or without a threshold pace, or off running).
    """

    role: BlockRole
    minutes: int
    zone: int
    repeats: int
    hr_low_bpm: int
    hr_high_bpm: int
    pace_slow_s_per_km: int | None
    pace_fast_s_per_km: int | None

    def __post_init__(self) -> None:
        if self.minutes < 1 or self.repeats < 1 or not 1 <= self.zone <= 5:
            raise ValueError("a block lasts at least a minute, in a zone from 1 to 5")
        if self.hr_low_bpm > self.hr_high_bpm:
            raise ValueError("the HR range of a block is inverted")


@dataclass(frozen=True, slots=True)
class PlannedSession:
    """A training session of a day: sport, type, duration, structure and estimated load."""

    sport: Sport
    session_type: SessionType
    minutes: int
    blocks: tuple[Block, ...]
    load: float


@dataclass(frozen=True, slots=True)
class PlannedRace:
    """A race of a day with the time and the load the plan expects from it."""

    goal_id: str
    priority: Priority
    sport: Sport
    distance_m: int
    expected_time_s: int
    load: float


@dataclass(frozen=True, slots=True)
class PlanDay:
    """A day of the plan: at most one session, or a race; `load` is the total estimated load."""

    day: date
    phase: Phase
    session: PlannedSession | None
    race: PlannedRace | None
    load: float


class WeekKind(Enum):
    """Character of a plan week. The value is a stable identifier."""

    MAINTENANCE = "maintenance"
    BASE = "base"
    BUILD = "build"
    PEAK = "peak"
    UNLOAD = "unload"  # the fourth week of a 3:1 mesocycle
    TAPER = "taper"
    RECOVERY = "recovery"
    RACE = "race"


@dataclass(frozen=True, slots=True)
class PlanWeek:
    """A week (Monday to Sunday, the first one possibly partial) of the plan."""

    week_start: date
    kind: WeekKind
    session_minutes: int
    load: float


@dataclass(frozen=True, slots=True)
class PmcPoint:
    """Projected end-of-day CTL and ATL; `tsb` is the morning form of the day."""

    day: date
    ctl: float
    atl: float
    tsb: float


@dataclass(frozen=True, slots=True)
class TargetAssessment:
    """Verdict on a race's target time. `best_time_s` is the best time possible at the race, or
    `None` when it cannot be estimated (`TargetClass.NOT_ESTIMABLE`)."""

    goal_id: str
    classification: TargetClass
    best_time_s: int | None


# --- Warnings: the plan is built, but the user should know ---------------------------------------
@dataclass(frozen=True, slots=True)
class MissingLongSession:
    """A race of a half marathon or more, and no long session chosen: endurance will lag."""

    goal_id: str


@dataclass(frozen=True, slots=True)
class MissingIntenseSessions:
    """Neither threshold nor VO2max sessions chosen: the build phase has no quality work."""

    goal_id: str


@dataclass(frozen=True, slots=True)
class CloseARaces:
    """Two A races with no room for a training block between them: unload, recover, maintain."""

    first_id: str
    second_id: str
    gap_days: int


@dataclass(frozen=True, slots=True)
class CtlTargetNotReached:
    """The CTL to reach for a goal is out of reach at the maximum ramp (or the time given)."""

    goal_id: str
    projected_ctl: float
    target_ctl: float


@dataclass(frozen=True, slots=True)
class TargetTimeUnreachable:
    """The target time cannot be reached by the race, even with the allowed improvement."""

    goal_id: str


@dataclass(frozen=True, slots=True)
class RecoveryInserted:
    """TSB too low or ATL too high: the first days of the plan are recovery."""

    tsb: float
    atl: float
    ctl: float
    days: int


@dataclass(frozen=True, slots=True)
class LongDayUnavailable:
    """The long session is chosen but the long day has no time: no long session is planned."""


@dataclass(frozen=True, slots=True)
class GoalBeyondHorizon:
    """The goal is further than the plan reaches; only the weeks up to the horizon are planned."""

    goal_id: str


type PlanWarning = (
    MissingLongSession
    | MissingIntenseSessions
    | CloseARaces
    | CtlTargetNotReached
    | TargetTimeUnreachable
    | RecoveryInserted
    | LongDayUnavailable
    | GoalBeyondHorizon
)


# --- Where to test the threshold -----------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class RetestBeforePlan:
    """No threshold test yet: do it before the plan starts."""


@dataclass(frozen=True, slots=True)
class RetestInUnloadWeek:
    """Do the threshold test in the unload week starting on `week_start`."""

    week_start: date


@dataclass(frozen=True, slots=True)
class RetestNotPlaceable:
    """No unload week falls in the retest window within the plan."""


type RetestProposal = RetestBeforePlan | RetestInUnloadWeek | RetestNotPlaceable


@dataclass(frozen=True, slots=True)
class Plan:
    """A training plan: the days from tomorrow, the weeks, the projected PMC, the verdicts and
    warnings, with the method version, the parameters and the inputs that produced it.

    Pure data. `to_json` is canonical: same plan, same bytes.
    """

    method_version: int
    parameters: tuple[tuple[str, float], ...]
    generated_on: date
    inputs: PlanInputs
    days: tuple[PlanDay, ...]
    weeks: tuple[PlanWeek, ...]
    projection: tuple[PmcPoint, ...]
    targets: tuple[TargetAssessment, ...]
    warnings: tuple[PlanWarning, ...]
    retest_proposal: RetestProposal

    def to_json(self) -> str:
        """Canonical JSON: sorted keys, no spaces, floats rounded to 3 decimals."""
        return canonical_json(self)
