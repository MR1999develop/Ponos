# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
# ruff: noqa: ANN201
"""Phase 6 acceptance campaign, the path back: T-INF-02 against a naive model written from the
rules of `references/infortuni.md`, the pain-free days before running, the slow-recovery reminder.

The model is built differently from the code (it keeps the history and recomputes the runs of good
days by looking back at it) so that it is an oracle, not a copy. Random sequences have gaps, signs,
medical reviews, every zone and onset.
"""

from __future__ import annotations

import itertools
from dataclasses import replace
from datetime import date, timedelta

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from ponos_coach.injury.domain import (
    BodyZone,
    CheckIn,
    Injury,
    InjuryId,
    Onset,
    Overdue,
    PainScore,
    PathStatus,
    Step,
    WarningSign,
    defaults,
    follow,
    overdue,
    required_pain_free_days,
)
from tests.injury.builders import DAY0, injury, questionnaire

DURING_TOLERATED = 5
DURING_UP = 3


def ci(day: date, during: int | None, morning: int, signs: frozenset[WarningSign] = frozenset()):  # type: ignore[no-untyped-def]
    return CheckIn(day, None if during is None else PainScore(during), PainScore(morning), signs)


# --- The oracle --------------------------------------------------------------------------------
def contiguous_tail(items: list[CheckIn]) -> list[CheckIn]:
    """The last stretch of the list made of consecutive days."""
    tail: list[CheckIn] = []
    for item in reversed(items):
        if tail and (tail[-1].day - item.day).days != 1:
            break
        tail.append(item)
    return list(reversed(tail))


def free_run(history: list[CheckIn]) -> int:
    """Consecutive days, ending with the last check-in, with no pain at all."""
    tail = contiguous_tail(history)
    count = 0
    for item in reversed(tail):
        if not item.pain_free:
            break
        count += 1
    return count


def model(inj: Injury) -> tuple[Step, PathStatus, frozenset[WarningSign]]:
    """Step, status and active signs from the rules, replaying the history with lookbacks."""
    q = inj.questionnaire
    step = 0
    halted = bool(q.alarms())
    baseline = q.pain.next_morning.value
    alarms = set(q.alarms())
    history: list[CheckIn] = []  # the check-ins of the days that counted (none while halted)
    run_start = 0  # the good run can only begin here: earlier days were bad or already used
    reviewed = inj.reviewed_on
    applied = reviewed is None
    needed_free = 28 if (q.zone is BodyZone.FOOT and q.onset is Onset.GRADUAL) else 5

    for c in inj.checkins:
        if not applied and reviewed is not None and c.day > reviewed:
            applied = True
            if halted:
                step, halted, alarms = 0, False, set()
                history.clear()
                run_start = 0
        if c.signs:
            halted = True
            alarms |= set(c.signs)
            history.clear()
            run_start = 0
            continue
        if halted:
            continue
        history.append(c)
        during = None if c.pain_during is None else c.pain_during.value
        morning = c.pain_morning.value
        if (during is not None and during > DURING_TOLERATED) or morning > baseline:
            step = max(step - 1, 0)
            run_start = len(history)
            continue
        if during is not None and during > DURING_UP:
            run_start = len(history)
            continue
        baseline = min(baseline, morning)
        run = contiguous_tail(history[run_start:])
        active = sum(1 for r in run if r.pain_during is not None)
        if (
            step < int(Step.INTENSITY)
            and len(run) >= defaults.GOOD_DAYS_TO_STEP_UP[Step(step)]
            and active >= defaults.ACTIVE_DAYS_TO_STEP_UP[Step(step)]
            and (step + 1 != Step.RUN_WALK or free_run(history) >= needed_free)
        ):
            step += 1
            run_start = len(history)
    if not applied and halted:
        step, halted, alarms = 0, False, set()
    return Step(step), (PathStatus.HALTED if halted else PathStatus.ACTIVE), frozenset(alarms)


# --- Strategies -------------------------------------------------------------------------------
@st.composite
def histories(draw: st.DrawFn) -> Injury:
    zone = draw(st.sampled_from(list(BodyZone)))
    onset = draw(st.sampled_from(list(Onset)))
    q = questionnaire(
        zone, onset=onset, morning=draw(st.integers(0, 4)), rest=draw(st.integers(0, 4))
    )
    days = draw(st.integers(1, 70))
    day = DAY0
    checkins = []
    for _ in range(days):
        day += timedelta(days=draw(st.sampled_from([1, 1, 1, 1, 1, 1, 1, 2, 4])))
        sign = draw(st.sampled_from([None] * 30 + [WarningSign.NIGHT_PAIN, WarningSign.FEVER]))
        checkins.append(
            ci(
                day,
                draw(st.sampled_from([None, 0, 0, 1, 2, 3, 3, 4, 5, 6])),
                draw(st.sampled_from([0, 0, 0, 1, 2, 3, 4])),
                frozenset([sign]) if sign else frozenset(),
            )
        )
    built = Injury(InjuryId("m1"), DAY0, q, tuple(checkins))
    if draw(st.booleans()) and checkins:
        built = replace(built, reviewed_on=draw(st.sampled_from([c.day for c in checkins])))
    return built


@settings(max_examples=300, deadline=None, derandomize=True)
@given(histories())
def test_t_inf_02_the_path_equals_the_independent_model(inj: Injury) -> None:
    state = follow(inj)

    step, status, alarms = model(inj)

    assert (state.step, state.status, state.alarms) == (step, status, alarms)


@settings(max_examples=200, deadline=None, derandomize=True)
@given(histories())
def test_t_inf_02_invariants_hold_on_every_prefix(inj: Injury) -> None:
    """One step at a time, no climb on a bad or missing day, no climb while halted."""
    previous = follow(replace(inj, checkins=(), reviewed_on=None))
    for n in range(1, len(inj.checkins) + 1):
        cut = replace(inj, checkins=inj.checkins[:n], reviewed_on=None)
        state = follow(cut)
        assert abs(state.step - previous.step) <= 1  # never a jump (a review resets from outside)
        last = inj.checkins[n - 1]
        if state.step > previous.step:
            assert previous.status is PathStatus.ACTIVE and not last.signs
            during = None if last.pain_during is None else last.pain_during.value
            assert during is None or during <= DURING_UP
            needed = defaults.GOOD_DAYS_TO_STEP_UP[previous.step]
            tail = inj.checkins[max(0, n - needed) : n]
            assert len(tail) == needed
            assert all((b.day - a.day).days == 1 for a, b in itertools.pairwise(tail))
            assert all(
                not c.signs and (c.pain_during is None or c.pain_during.value <= DURING_UP)
                for c in tail
            )
        if previous.status is PathStatus.HALTED:
            assert state.step == previous.step
        previous = state


# --- The pain-free days before the first running step -------------------------------------------
def walking_days(count: int, *, start: date = DAY0 + timedelta(days=1), morning: int = 0):  # type: ignore[no-untyped-def]
    return [ci(start + timedelta(days=i), 0, morning) for i in range(count)]


@pytest.mark.parametrize(
    ("zone", "onset", "days"),
    [
        (BodyZone.SHIN, Onset.GRADUAL, 5),
        (BodyZone.FOOT, Onset.SUDDEN, 5),
        (BodyZone.KNEE, Onset.GRADUAL, 5),
        (BodyZone.FOOT, Onset.GRADUAL, 28),  # the heel
    ],
)
def test_t_inf_02_running_starts_on_the_first_day_with_enough_pain_free_days_and_not_before(
    zone: BodyZone, onset: Onset, days: int
) -> None:
    assert required_pain_free_days(zone, onset) == days
    base = injury(q=questionnaire(zone, onset=onset, morning=1))
    # Pain of 1 in the morning for a week: good days, but not pain free (the base level is 1).
    low = walking_days(10, morning=1)
    after_noise = follow(replace(base, checkins=tuple(low)))
    assert after_noise.step is Step.WALKING  # activation (3 days) -> walking, and waits there

    start = low[-1].day + timedelta(days=1)
    free = [ci(start + timedelta(days=i), 0, 0) for i in range(days + 2)]
    for n in range(1, len(free) + 1):
        state = follow(replace(base, checkins=tuple(low + free[:n])))
        expected = Step.RUN_WALK if n >= days else Step.WALKING
        assert state.step is expected, f"{n} pain-free days"


def test_t_inf_02_one_day_of_pain_in_the_run_of_free_days_starts_the_count_again() -> None:
    base = injury(q=questionnaire(BodyZone.SHIN, morning=1))
    low = walking_days(10, morning=1)
    start = low[-1].day + timedelta(days=1)
    free = [ci(start + timedelta(days=i), 0, 0) for i in range(4)]
    blip = [ci(start + timedelta(days=4), 1, 0)]  # 1/10 while active: tolerated, not pain free
    again = [ci(start + timedelta(days=5 + i), 0, 0) for i in range(5)]

    after_four = follow(replace(base, checkins=tuple(low + free)))
    after_blip = follow(replace(base, checkins=tuple(low + free + blip)))
    after_again = follow(replace(base, checkins=tuple(low + free + blip + again[:4])))
    entered = follow(replace(base, checkins=tuple(low + free + blip + again)))

    assert (after_four.step, after_blip.step, after_again.step) == (Step.WALKING,) * 3
    assert entered.step is Step.RUN_WALK


def test_t_inf_02_a_missing_day_breaks_the_pain_free_run_too() -> None:
    base = injury(q=questionnaire(BodyZone.SHIN, morning=1))
    low = walking_days(10, morning=1)
    start = low[-1].day + timedelta(days=1)
    days = [ci(start + timedelta(days=i), 0, 0) for i in range(3)]
    days += [ci(start + timedelta(days=4 + i), 0, 0) for i in range(3)]  # one day missing

    state = follow(replace(base, checkins=tuple(low + days)))

    assert state.step is Step.WALKING  # 3 + 3 free days with a hole: no five in a row


# --- Slow recovery (the reminder to see a doctor) ------------------------------------------------
@pytest.mark.parametrize(
    ("elapsed", "expected"), [(20, None), (21, Overdue.STEP), (22, Overdue.STEP)]
)
def test_the_step_reminder_comes_on_the_21st_day_on_the_same_step(
    elapsed: int, expected: Overdue | None
) -> None:
    inj = injury()
    state = follow(inj)

    assert overdue(inj, state, DAY0 + timedelta(days=elapsed)) is expected


@pytest.mark.parametrize(("elapsed", "expected"), [(83, Overdue.STEP), (84, Overdue.TOTAL)])
def test_the_total_reminder_comes_on_the_84th_day_and_wins_over_the_step(
    elapsed: int, expected: Overdue
) -> None:
    inj = injury()

    assert overdue(inj, follow(inj), DAY0 + timedelta(days=elapsed)) is expected


def test_a_halted_path_has_no_reminder_it_is_already_with_the_doctor() -> None:
    inj = injury(q=questionnaire(signs=[WarningSign.NIGHT_PAIN]))

    assert overdue(inj, follow(inj), DAY0 + timedelta(days=200)) is None
