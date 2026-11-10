# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""What the window knows about the plan, testable without a window.

The data thread gives the result of a rebuild (`PlanBuilt`, `PlanRefused`, `PlanNotBuilt`); here it
becomes a state of the presentation (`PlanState`) and from the state the notices (what is wrong and
what to do), the verdict on each target time and the proposal for the next threshold test. The
domain gives categories (a refusal, a warning, a class of target); the words are in `plan_texts` and
the colours are chosen by the views.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum

from ponos_coach.goals.domain import RaceGoal
from ponos_coach.goals.ports import Goal
from ponos_coach.planning.domain import (
    CloseARaces,
    CtlTargetNotReached,
    GoalBeyondHorizon,
    LimitedByHours,
    LongDayUnavailable,
    MissingIntenseSessions,
    MissingLongSession,
    NoSessionPossible,
    Plan,
    PlanAdapted,
    PlanHalted,
    PlanRefusal,
    PlanWarning,
    RefusalReason,
    RetestProposal,
    TargetAssessment,
    TargetTimeUnreachable,
)
from ponos_coach.profiles.rebuild_plan import (
    NotBuiltReason,
    PlanBuilt,
    PlanNotBuilt,
    PlanRefused,
    RebuildResult,
)
from ponos_coach.ui.pmc_figure import PmcProjection, ProjectedRace


@dataclass(frozen=True, slots=True)
class NoPlanYet:
    """The plan has not been rebuilt since the profile opened."""


@dataclass(frozen=True, slots=True)
class PlanAvailable:
    """There is a plan."""

    plan: Plan


@dataclass(frozen=True, slots=True)
class PlanRefusedNow:
    """The engine refused to make a plan, and why."""

    refusal: PlanRefusal


@dataclass(frozen=True, slots=True)
class PlanNeedsData:
    """The plan could not even be tried."""

    reason: NotBuiltReason


type PlanState = NoPlanYet | PlanAvailable | PlanRefusedNow | PlanNeedsData


def plan_state_of(result: RebuildResult) -> PlanState:
    """The presentation state of the result of a rebuild."""
    match result:
        case PlanBuilt(plan=plan):
            return PlanAvailable(plan)
        case PlanRefused(refusal=refusal):
            return PlanRefusedNow(refusal)
        case PlanNotBuilt(reason=reason):
            return PlanNeedsData(reason)


def plan_of(state: PlanState) -> Plan | None:
    """The plan of a state, if it has one."""
    return state.plan if isinstance(state, PlanAvailable) else None


class Severity(Enum):
    """How much a notice matters. Two levels only: the plan is missing, or it has a problem."""

    NO_PLAN = "no_plan"
    WARNING = "warning"


class NoticeAction(Enum):
    """The command a notice offers to put things right. `OPEN_GOALS` shows the Goals section."""

    NONE = "none"
    ADD_RACE = "add_race"
    OPEN_GOALS = "open_goals"
    TRAINING_SETTINGS = "training_settings"
    EDIT_PROFILE = "edit_profile"
    OPEN_INJURIES = "open_injuries"


type NoticeSubject = PlanRefusal | PlanWarning | NotBuiltReason


@dataclass(frozen=True, slots=True)
class Notice:
    """A thing the user should know about the plan: what it is and which command helps."""

    severity: Severity
    subject: NoticeSubject
    action: NoticeAction


_REFUSAL_ACTIONS = {
    RefusalReason.NO_GOALS: NoticeAction.ADD_RACE,
    RefusalReason.GOAL_NOT_IN_FUTURE: NoticeAction.OPEN_GOALS,
    RefusalReason.GOALS_ON_SAME_DAY: NoticeAction.OPEN_GOALS,
    RefusalReason.NO_AVAILABILITY: NoticeAction.TRAINING_SETTINGS,
    RefusalReason.NO_SESSION_TYPES: NoticeAction.TRAINING_SETTINGS,
}


def _warning_action(warning: PlanWarning) -> NoticeAction:
    """Choices and the long day are put right in the training settings; the goals in the goals; what
    comes from an injury or an illness is looked at in the Injuries section."""
    if isinstance(warning, PlanHalted | PlanAdapted | NoSessionPossible):
        return NoticeAction.OPEN_INJURIES
    if isinstance(warning, MissingLongSession | MissingIntenseSessions | LongDayUnavailable):
        return NoticeAction.TRAINING_SETTINGS
    if isinstance(warning, CtlTargetNotReached) and isinstance(warning.limit, LimitedByHours):
        return NoticeAction.TRAINING_SETTINGS  # the hours are what stops it
    if isinstance(
        warning, CloseARaces | CtlTargetNotReached | TargetTimeUnreachable | GoalBeyondHorizon
    ):
        return NoticeAction.OPEN_GOALS
    return NoticeAction.NONE


def notices_of(state: PlanState) -> tuple[Notice, ...]:
    """What is wrong with the plan (or why there is none), most important first."""
    match state:
        case NoPlanYet():
            return ()
        case PlanNeedsData(reason=reason):
            return (Notice(Severity.NO_PLAN, reason, NoticeAction.EDIT_PROFILE),)
        case PlanRefusedNow(refusal=refusal):
            return (Notice(Severity.NO_PLAN, refusal, _REFUSAL_ACTIONS[refusal.reason]),)
        case PlanAvailable(plan=plan):
            return tuple(
                Notice(Severity.WARNING, warning, _warning_action(warning))
                for warning in plan.warnings
            )


@dataclass(frozen=True, slots=True)
class PlanAlert:
    """The strip over the calendar: `count` goals whose fitness target is out of reach, and the
    command that helps. If the weekly hours are what stops every one of them (`all_by_hours`) the
    command is the training settings; otherwise the goals, where each has its own remedy."""

    count: int
    all_by_hours: bool

    @property
    def action(self) -> NoticeAction:
        """The command the strip offers."""
        return NoticeAction.TRAINING_SETTINGS if self.all_by_hours else NoticeAction.OPEN_GOALS


def out_of_reach_of(state: PlanState) -> Mapping[str, CtlTargetNotReached]:
    """The goals whose fitness target the plan cannot reach, by goal id (empty without a plan)."""
    plan = plan_of(state)
    if plan is None:
        return {}
    return {w.goal_id: w for w in plan.warnings if isinstance(w, CtlTargetNotReached)}


def plan_alert_of(state: PlanState) -> PlanAlert | None:
    """The strip to show over the calendar, `None` when no goal is out of reach (so the strip
    goes away by itself when the warnings do)."""
    missed = out_of_reach_of(state)
    if not missed:
        return None
    by_hours = all(isinstance(w.limit, LimitedByHours) for w in missed.values())
    return PlanAlert(len(missed), by_hours)


def assessments_of(state: PlanState) -> Mapping[str, TargetAssessment]:
    """The verdict on each race's target time, by goal id (empty without a plan)."""
    plan = plan_of(state)
    return {} if plan is None else {item.goal_id: item for item in plan.targets}


def retest_proposal_of(state: PlanState) -> RetestProposal | None:
    """Where the plan proposes the next threshold test; `None` without a plan."""
    plan = plan_of(state)
    return None if plan is None else plan.retest_proposal


def projection_of(
    state: PlanState,
    goals: Sequence[Goal],
    race_name: Callable[[RaceGoal], str],
) -> PmcProjection | None:
    """The future part of the PMC for the chart, with the races as markers; `None` without a plan
    (or with a plan that projects nothing)."""
    plan = plan_of(state)
    if plan is None or not plan.projection:
        return None
    last_projected = plan.projection[-1].day
    races = tuple(
        ProjectedRace(goal.day, goal.priority, race_name(goal))
        for goal in goals
        if isinstance(goal, RaceGoal) and plan.generated_on < goal.day <= last_projected
    )
    return PmcProjection(plan.projection, races)
