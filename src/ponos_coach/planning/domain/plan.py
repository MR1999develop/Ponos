# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""The plan: what the engine returns. A value, versioned, with a canonical serialisation."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from enum import Enum

from ponos_coach.activities.domain import Sport
from ponos_coach.assessment.domain import ThresholdProtocol
from ponos_coach.goals.domain import INTENSE_TYPES, Priority, SessionType
from ponos_coach.injury.domain import HaltReason
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
    `race_pace_s_per_km` is the pace of the race the block rehearses (whole s/km), only on the
    work blocks of a race-pace session and only when the race time can be expected.
    """

    role: BlockRole
    minutes: int
    zone: int
    repeats: int
    hr_low_bpm: int
    hr_high_bpm: int
    pace_slow_s_per_km: int | None
    pace_fast_s_per_km: int | None
    race_pace_s_per_km: int | None = None

    def __post_init__(self) -> None:
        if self.minutes < 1 or self.repeats < 1 or not 1 <= self.zone <= 5:
            raise ValueError("a block lasts at least a minute, in a zone from 1 to 5")
        if self.hr_low_bpm > self.hr_high_bpm:
            raise ValueError("the HR range of a block is inverted")


def _is_load(value: float) -> bool:
    return math.isfinite(value) and value >= 0


@dataclass(frozen=True, slots=True)
class PlannedSession:
    """A training session of a day: sport, type, duration, structure and estimated load.

    `race_pace` marks a session of the peak or the taper whose work is at the pace of the A race
    (medium for a marathon, threshold for a half marathon or a 10 km or shorter race).
    `threshold_test` marks the threshold test, of that protocol: a hard session (its type is
    `THRESHOLD`) that is not one of the types the user chose.
    `ascent_m` is the indicative elevation gain in metres of a session on hills, 0 on the flat: an
    intense session with it is a hill-repeats session (its work is uphill, no pace target), a long
    one is a hilly long run.
    """

    sport: Sport
    session_type: SessionType
    minutes: int
    blocks: tuple[Block, ...]
    load: float
    race_pace: bool = False
    threshold_test: ThresholdProtocol | None = None
    ascent_m: int = 0

    def __post_init__(self) -> None:
        if self.minutes < 1 or not _is_load(self.load):
            raise ValueError(
                "a session lasts at least a minute and has a finite load, not negative"
            )
        if self.ascent_m < 0:
            raise ValueError("the elevation gain of a session is not negative")
        if self.ascent_m > 0 and (self.threshold_test is not None or self.race_pace):
            raise ValueError("a session on hills is neither the threshold test nor at race pace")
        if self.threshold_test is not None and (
            self.session_type is not SessionType.THRESHOLD or self.race_pace
        ):
            raise ValueError("the threshold test is a threshold session, never at race pace")

    @property
    def is_hill_repeats(self) -> bool:
        """True for an intense session whose work is run uphill."""
        return self.ascent_m > 0 and self.session_type in INTENSE_TYPES

    @property
    def is_hilly_long(self) -> bool:
        """True for a long run on a hilly course."""
        return self.ascent_m > 0 and self.session_type is SessionType.LONG


@dataclass(frozen=True, slots=True)
class PlannedRace:
    """A race of a day with the time and the load the plan expects from it."""

    goal_id: str
    priority: Priority
    sport: Sport
    distance_m: int
    expected_time_s: int
    load: float
    ascent_m: int = 0

    def __post_init__(self) -> None:
        if not self.goal_id or self.distance_m < 1 or self.expected_time_s < 1:
            raise ValueError("a race has an id, a distance and an expected time above zero")
        if self.ascent_m < 0:
            raise ValueError("the elevation gain of a race is not negative")
        if not _is_load(self.load):
            raise ValueError("the load of a race is finite and not negative")


@dataclass(frozen=True, slots=True)
class PlanDay:
    """A day of the plan: at most one session, or a race; `load` is the total estimated load."""

    day: date
    phase: Phase
    session: PlannedSession | None
    race: PlannedRace | None
    load: float

    def __post_init__(self) -> None:
        if not _is_load(self.load):
            raise ValueError("the load of a day is finite and not negative")


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
    """A week (Monday to Sunday, the first one possibly partial) of the plan.

    The two 80/20 shares of the week: `low_zone_share` is the share of the session time in
    zones 1-2 (1.0 without sessions), `intense_session_share` the share of the sessions that
    have an intense goal (threshold, VO2max, medium; 0.0 without sessions).
    """

    week_start: date
    kind: WeekKind
    session_minutes: int
    load: float
    session_count: int = 0
    low_zone_share: float = 1.0
    intense_session_share: float = 0.0


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
    """The long session is chosen but the long day has no time: no long session is planned.

    Not produced since method version 6 (the long day is always an available day); kept so that
    the plans saved before still read."""


@dataclass(frozen=True, slots=True)
class GoalBeyondHorizon:
    """The goal is further than the plan reaches; only the weeks up to the horizon are planned."""

    goal_id: str


@dataclass(frozen=True, slots=True)
class PlanHalted:
    """No session at all: a warning sign or an illness stops the plan until it is seen to. The app
    invites a visit to a doctor (it never says what the sign means); `injury_ids` are the
    problems that stop it."""

    reason: HaltReason
    injury_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PlanAdapted:
    """The plan leaves out what the open injuries and illnesses should not take; `injury_ids` are
    the problems that limit it."""

    injury_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class NoSessionPossible:
    """Nothing the user chose can be scheduled without loading the injured zone (no tolerated
    alternative is chosen or declared): rest days until the path allows more."""

    injury_ids: tuple[str, ...]


type PlanWarning = (
    MissingLongSession
    | MissingIntenseSessions
    | CloseARaces
    | CtlTargetNotReached
    | TargetTimeUnreachable
    | RecoveryInserted
    | LongDayUnavailable
    | GoalBeyondHorizon
    | PlanHalted
    | PlanAdapted
    | NoSessionPossible
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


@dataclass(frozen=True, slots=True)
class RetestScheduled:
    """The threshold test is a session of the plan, on `day`."""

    day: date


type RetestProposal = RetestBeforePlan | RetestInUnloadWeek | RetestNotPlaceable | RetestScheduled


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
