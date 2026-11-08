# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""The path back, step by step: `follow` replays the check-ins and says where an injury stands.

The rules of one day (`advance`), all numbers in `defaults`:

- a warning sign halts the path; nothing moves until a medical review, then it starts again from
  the first step;
- pain above the tolerated limit while active, or this morning's pain above the base level,
  steps back to the last completed step (one below), never to an arbitrary one;
- pain between the up-limit and the tolerated limit holds the step;
- otherwise the day is good; enough consecutive good days, with activity on some of them, step up,
  by one, never more. A day with no check-in breaks the run: no check-in, no step up.
- the first running step also needs the days with no pain at all (`required_pain_free_days`).

The base level is the lowest pain the morning after seen so far (the questionnaire's, to begin
with): it only goes down. Pure and deterministic: same check-ins, same state.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
from enum import Enum

from ponos_coach.injury.domain import defaults
from ponos_coach.injury.domain.records import CheckIn, Injury, Questionnaire
from ponos_coach.injury.domain.vocabulary import (
    FIRST_STEP,
    LAST_STEP,
    BodyZone,
    Onset,
    PainScore,
    Scope,
    Step,
    WarningSign,
    scope_of,
)


class PathStatus(Enum):
    """The path moves, or it is halted by a warning sign (see a doctor)."""

    ACTIVE = "active"
    HALTED = "halted"


class Verdict(Enum):
    """What a check-in meant for the path. The value is a stable identifier."""

    GOOD = "good"  # tolerated: counts towards the next step
    HOLD = "hold"  # a little too much: the run of good days starts again
    BACK = "back"  # worse: one step down (or the same, on the first step)
    UP = "up"  # the good days are enough: one step up
    HALT = "halt"  # a warning sign


class Overdue(Enum):
    """The recovery is slower than expected: time for a doctor, not for more of the same."""

    STEP = "step"  # too long on the same step
    TOTAL = "total"  # too long since the opening


@dataclass(frozen=True, slots=True)
class PathState:
    """Where an injury stands.

    `good_days` and `active_days` are the run of consecutive good days and how many had an
    activity; `pain_free_days` the run of consecutive days without any pain. `entered_on` is the
    day the step began.
    """

    step: Step
    status: PathStatus
    alarms: frozenset[WarningSign]
    baseline: PainScore
    entered_on: date
    good_days: int = 0
    active_days: int = 0
    pain_free_days: int = 0
    last_checkin: date | None = None
    last_verdict: Verdict | None = None

    @property
    def see_doctor(self) -> bool:
        """True while a warning sign is unreviewed: the app invites a visit, nothing else."""
        return self.status is PathStatus.HALTED

    @property
    def scope(self) -> Scope | None:
        """What the active signs stop: everything if any of them is a whole-plan one."""
        scopes = {scope_of(sign) for sign in self.alarms}
        if Scope.WHOLE_PLAN in scopes:
            return Scope.WHOLE_PLAN
        return Scope.ZONE if scopes else None

    @property
    def ready_to_close(self) -> bool:
        """On the last step with enough good days: the user (and the doctor) may call it done.
        The app never decides the return."""
        return (
            self.status is PathStatus.ACTIVE
            and self.step is LAST_STEP
            and self.good_days >= defaults.GOOD_DAYS_TO_STEP_UP[LAST_STEP]
        )


def required_pain_free_days(zone: BodyZone, onset: Onset) -> int:
    """The days with no pain at all before the first running step: 5 (CSM1), 28 for a gradual pain
    of the heel."""
    if zone is defaults.HEEL_ZONE and onset is defaults.HEEL_ONSET:
        return defaults.PAIN_FREE_DAYS_BEFORE_RUNNING_HEEL
    return defaults.PAIN_FREE_DAYS_BEFORE_RUNNING


def initial_state(injury: Injury) -> PathState:
    """The state on the day of the opening: the first step, halted if the questionnaire has a
    warning sign, the base level the morning pain of the questionnaire."""
    alarms = injury.questionnaire.alarms()
    return PathState(
        FIRST_STEP,
        PathStatus.HALTED if alarms else PathStatus.ACTIVE,
        alarms,
        injury.questionnaire.pain.next_morning,
        injury.opened_on,
    )


def _worse(state: PathState, checkin: CheckIn) -> bool:
    during = checkin.pain_during
    too_much = during is not None and during.value > defaults.TOLERATED_MAX_PAIN_DURING
    return too_much or checkin.pain_morning > state.baseline


def _holds(checkin: CheckIn) -> bool:
    during = checkin.pain_during
    return during is not None and during.value > defaults.STEP_UP_MAX_PAIN_DURING


def _may_enter(step: Step, state: PathState, questionnaire: Questionnaire) -> bool:
    """The run of good days is not everything: the first running step needs pain-free days."""
    if step is not Step.RUN_WALK:
        return True
    needed = required_pain_free_days(questionnaire.zone, questionnaire.onset)
    return state.pain_free_days >= needed


def _runs(state: PathState, checkin: CheckIn) -> PathState:
    """The state with the runs of days updated for this check-in (a gap breaks them all)."""
    consecutive = state.last_checkin is not None and (checkin.day - state.last_checkin).days == 1
    good = state.good_days if consecutive else 0
    active = state.active_days if consecutive else 0
    free = state.pain_free_days if consecutive else 0
    return replace(
        state,
        good_days=good,
        active_days=active,
        pain_free_days=free + 1 if checkin.pain_free else 0,
        last_checkin=checkin.day,
    )


def advance(state: PathState, checkin: CheckIn, questionnaire: Questionnaire) -> PathState:
    """The state after one more check-in (days in ascending order).

    The step changes by at most one, and only up after enough good days. A halted path stays on
    its step whatever the check-in says (the days still count as records).
    """
    if checkin.signs:
        return replace(
            state,
            status=PathStatus.HALTED,
            alarms=state.alarms | checkin.signs,
            good_days=0,
            active_days=0,
            pain_free_days=0,
            last_checkin=checkin.day,
            last_verdict=Verdict.HALT,
        )
    if state.status is PathStatus.HALTED:
        return replace(state, last_checkin=checkin.day, last_verdict=Verdict.HALT)
    state = _runs(state, checkin)
    if _worse(state, checkin):
        step = Step(max(state.step - 1, FIRST_STEP))
        entered = checkin.day if step != state.step else state.entered_on
        return replace(
            state,
            step=step,
            entered_on=entered,
            good_days=0,
            active_days=0,
            last_verdict=Verdict.BACK,
        )
    if _holds(checkin):
        return replace(state, good_days=0, active_days=0, last_verdict=Verdict.HOLD)
    good = replace(
        state,
        baseline=min(state.baseline, checkin.pain_morning),
        good_days=state.good_days + 1,
        active_days=state.active_days + (checkin.pain_during is not None),
        last_verdict=Verdict.GOOD,
    )
    return _step_up(good, checkin, questionnaire)


def _step_up(state: PathState, checkin: CheckIn, questionnaire: Questionnaire) -> PathState:
    step = state.step
    enough = (
        state.good_days >= defaults.GOOD_DAYS_TO_STEP_UP[step]
        and state.active_days >= defaults.ACTIVE_DAYS_TO_STEP_UP[step]
    )
    if step is LAST_STEP or not enough:
        return state
    higher = Step(step + 1)
    if not _may_enter(higher, state, questionnaire):
        return state
    return replace(
        state,
        step=higher,
        entered_on=checkin.day,
        good_days=0,
        active_days=0,
        last_verdict=Verdict.UP,
    )


def _reviewed(state: PathState, day: date) -> PathState:
    """After a medical review: the path starts again from the first step, with fresh runs."""
    if state.status is not PathStatus.HALTED:
        return state
    return replace(
        state,
        step=defaults.STEP_AFTER_REVIEW,
        status=PathStatus.ACTIVE,
        alarms=frozenset(),
        entered_on=day,
        good_days=0,
        active_days=0,
        pain_free_days=0,
        last_verdict=None,
    )


def follow(injury: Injury) -> PathState:
    """Where the injury stands: the opening, then every check-in in order, with the medical review
    (if any) applied after the check-ins of its day and before the later ones."""
    state = initial_state(injury)
    reviewed = injury.reviewed_on
    applied = reviewed is None
    for checkin in injury.checkins:
        if not applied and reviewed is not None and checkin.day > reviewed:
            state = _reviewed(state, reviewed)
            applied = True
        state = advance(state, checkin, injury.questionnaire)
    if not applied and reviewed is not None:
        state = _reviewed(state, reviewed)
    return state


def overdue(injury: Injury, state: PathState, today: date) -> Overdue | None:
    """Whether the recovery is slower than expected today: three weeks on one step, or twelve weeks
    in all. A halted path is already with the doctor; the answer is then `None`."""
    if state.status is PathStatus.HALTED:
        return None
    if (today - injury.opened_on).days >= defaults.TOTAL_EXPECTED_DAYS:
        return Overdue.TOTAL
    if (today - state.entered_on).days >= defaults.STEP_EXPECTED_DAYS:
        return Overdue.STEP
    return None


def checkin_due(injury: Injury, today: date) -> bool:
    """True if the injury is open, has begun by `today` and has no check-in for `today` yet."""
    return (
        injury.is_open
        and injury.opened_on <= today
        and all(c.day != today for c in injury.checkins)
    )
