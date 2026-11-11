# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""What the plan may ask of a body that is recovering.

`plan_injury` turns an open injury or illness into a small value without free text (it goes into
the saved plan); `restriction_for` combines them into what the plan engine needs: stop everything,
or how much running, strength and which pain-free alternatives. With several problems the most
restrictive rule of each kind wins (CSM1 cap. 19). The plan engine maps these to its session
types; this module knows nothing of sessions.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from enum import IntEnum

from ponos_coach.injury.domain import defaults
from ponos_coach.injury.domain.path import PathStatus, follow
from ponos_coach.injury.domain.records import Illness, Problem, problem_end
from ponos_coach.injury.domain.vocabulary import (
    LAST_STEP,
    BodyZone,
    HaltReason,
    IllnessKind,
    InjuryId,
    PainFreeActivity,
    Scope,
    Step,
)


class RunAllowance(IntEnum):
    """How much running the path allows, from none to all of it. Ordered: lower is stricter."""

    NONE = 0
    RECOVERY = 1  # Z1, short: the run-and-walk step
    EASY = 2  # continuous and easy
    EASY_LONG = 3  # with the volume
    FULL = 4  # with the intensity


RUN_ALLOWANCE_BY_STEP: dict[Step, RunAllowance] = {
    Step.ACTIVATION: RunAllowance.NONE,
    Step.WALKING: RunAllowance.NONE,
    Step.RUN_WALK: RunAllowance.RECOVERY,
    Step.CONTINUOUS_EASY: RunAllowance.EASY,
    Step.VOLUME: RunAllowance.EASY_LONG,
    Step.INTENSITY: RunAllowance.FULL,
}


@dataclass(frozen=True, slots=True)
class BodyRestriction:
    """An open injury as the plan sees it: zone, step, what does not hurt, and the scope of the
    warning sign that halts it (`None` when none does)."""

    injury_id: InjuryId
    zone: BodyZone
    step: Step
    pain_free: frozenset[PainFreeActivity]
    halted_by: Scope | None = None


@dataclass(frozen=True, slots=True)
class IllnessRestriction:
    """An illness that has not passed."""

    injury_id: InjuryId
    kind: IllnessKind


type PlanInjury = BodyRestriction | IllnessRestriction


def plan_injury(problem: Problem) -> PlanInjury | None:
    """The problem as the plan sees it now, or `None` if it is over."""
    if isinstance(problem, Illness):
        return IllnessRestriction(problem.illness_id, problem.kind) if problem.is_open else None
    if not problem.is_open:
        return None
    state = follow(problem)
    halted = state.scope if state.status is PathStatus.HALTED else None
    return BodyRestriction(
        problem.injury_id,
        problem.questionnaire.zone,
        state.step,
        problem.questionnaire.pain_free,
        halted,
    )


def plan_injuries(problems: Iterable[Problem]) -> tuple[PlanInjury, ...]:
    """The open problems for the plan, by id (a stable order)."""
    found = (plan_injury(problem) for problem in problems)
    return tuple(sorted((p for p in found if p is not None), key=lambda p: p.injury_id.value))


def recently_returned(problems: Iterable[Problem], today: date) -> bool:
    """True if a problem was closed within the window after which the weekly ramp is lower again
    (CSM1 cap. 12). A closing dated after `today` still counts: until that day the problem was
    open, so the ramp stays low (D-F6-01)."""
    for problem in problems:
        end = problem_end(problem)
        if end is not None and (today - end).days <= defaults.RAMP_AFTER_RETURN_DAYS:
            return True
    return False


def compatible_activities(
    zone: BodyZone, step: Step, declared: frozenset[PainFreeActivity]
) -> frozenset[PainFreeActivity]:
    """The declared pain-free activities that this zone allows at this step: swimming and water
    running always, walking from the walking step, cycling from the step the zone gives (the knee
    and the back take it as a step of the path, not as a free alternative)."""
    allowed = {PainFreeActivity.SWIMMING, PainFreeActivity.WATER_RUNNING}
    if step >= defaults.WALKING_FROM_STEP:
        allowed.add(PainFreeActivity.WALKING)
    if step >= defaults.CYCLING_FROM_STEP[zone]:
        allowed.add(PainFreeActivity.CYCLING)
    return declared & allowed


@dataclass(frozen=True, slots=True)
class PlanRestriction:
    """What the plan may schedule.

    `halt` is the reason when nothing may be scheduled, with the ids concerned. Otherwise:
    `running` is how much running is allowed; `cycling` and `walking` whether those sports are;
    `strength` whether strength sessions are; `intense` whether intensity is; `alternatives` the
    pain-free activities every limiting problem tolerates (an alternative session needs at least
    one); `alternate_days` whether sessions go on alternate days only. `limited_by` are the ids of
    the problems that limit anything.
    """

    halt: HaltReason | None
    halted_ids: tuple[str, ...]
    running: RunAllowance
    cycling: bool
    walking: bool
    strength: bool
    intense: bool
    alternatives: frozenset[PainFreeActivity]
    alternate_days: bool
    limited_by: tuple[str, ...]

    @property
    def limits_nothing(self) -> bool:
        """True when the plan is free."""
        return self.halt is None and not self.limited_by


FREE = PlanRestriction(
    None, (), RunAllowance.FULL, True, True, True, True, frozenset(PainFreeActivity), False, ()
)


def _halts(injury: PlanInjury) -> HaltReason | None:
    if isinstance(injury, IllnessRestriction):
        return HaltReason.ILLNESS if injury.kind is IllnessKind.SYSTEMIC else None
    if injury.halted_by is Scope.WHOLE_PLAN:
        return HaltReason.WHOLE_PLAN_SIGN
    return HaltReason.ZONE_SIGN if injury.halted_by is Scope.ZONE else None


def _halt(injuries: Sequence[PlanInjury]) -> tuple[HaltReason, tuple[str, ...]] | None:
    """The most serious reason to stop everything, with the ids that give it."""
    for reason in (HaltReason.ILLNESS, HaltReason.WHOLE_PLAN_SIGN, HaltReason.ZONE_SIGN):
        ids = tuple(sorted(p.injury_id.value for p in injuries if _halts(p) is reason))
        if ids:
            return reason, ids
    return None


def restriction_for(injuries: Sequence[PlanInjury]) -> PlanRestriction:
    """What the plan may do given the open problems: the most restrictive rule of each kind."""
    if not injuries:
        return FREE
    free = FREE
    running, cycling, walking = free.running, True, True
    strength, intense, alternate = True, True, False
    alternatives = free.alternatives
    for injury in injuries:
        if isinstance(injury, IllnessRestriction):  # above the neck: the shortest easy session
            running = min(running, RunAllowance.RECOVERY)
            strength = False
            intense = False
            continue
        running = min(running, RUN_ALLOWANCE_BY_STEP[injury.step])
        cycling = cycling and injury.step >= defaults.CYCLING_FROM_STEP[injury.zone]
        walking = walking and injury.step >= defaults.WALKING_FROM_STEP
        strength = strength and injury.step >= defaults.STRENGTH_FROM_STEP[injury.zone]
        intense = intense and injury.step is LAST_STEP
        alternatives &= compatible_activities(injury.zone, injury.step, injury.pain_free)
        alternate = alternate or injury.step in defaults.ALTERNATE_DAY_STEPS
    halted = _halt(injuries)
    reason, ids = halted if halted else (None, ())
    limited = tuple(sorted(p.injury_id.value for p in injuries))
    return PlanRestriction(
        reason, ids, running, cycling, walking, strength, intense, alternatives, alternate, limited
    )
