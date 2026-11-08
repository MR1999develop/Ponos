# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""T-INF-02, the invariants of the path for any sequence of check-ins (hypothesis), and a naive
re-implementation of the written rules that `follow` must agree with."""

from __future__ import annotations

from datetime import timedelta

from hypothesis import given, settings
from hypothesis import strategies as st

from ponos_coach.injury.domain import (
    FIRST_STEP,
    LAST_STEP,
    CheckIn,
    Injury,
    PainScore,
    PathStatus,
    Step,
    Verdict,
    WarningSign,
    advance,
    defaults,
    follow,
    initial_state,
    required_pain_free_days,
)
from tests.injury.builders import DAY0, injury, questionnaire

_pain = st.integers(0, 10).map(PainScore)
_zones = st.sampled_from(list(defaults.CYCLING_FROM_STEP))


@st.composite
def _checkins(draw: st.DrawFn) -> tuple[CheckIn, ...]:
    """Up to 80 check-ins on ascending days, usually consecutive, with gaps now and then."""
    out: list[CheckIn] = []
    offset = 0
    for _ in range(draw(st.integers(0, 80))):
        offset += draw(st.sampled_from([1, 1, 1, 1, 1, 1, 2, 3, 9]))
        out.append(
            CheckIn(
                DAY0 + timedelta(days=offset),
                draw(st.one_of(st.none(), st.integers(0, 7).map(PainScore))),
                draw(st.integers(0, 4).map(PainScore)),
                draw(st.sampled_from([frozenset()] * 30 + [frozenset({WarningSign.NIGHT_PAIN})])),
            )
        )
    return tuple(out)


@st.composite
def _injuries(draw: st.DrawFn) -> Injury:
    zone = draw(_zones)
    morning = draw(st.integers(0, 4))
    q = questionnaire(zone, morning=morning, rest=draw(st.integers(0, 4)))
    return injury(q=q, checkins=draw(_checkins()))


@given(_injuries())
@settings(max_examples=120, deadline=None)
def test_the_step_moves_by_at_most_one_and_only_for_the_right_verdict(inj: Injury) -> None:
    state = initial_state(inj)
    for item in inj.checkins:
        before = state
        state = advance(before, item, inj.questionnaire)
        delta = state.step - before.step
        assert FIRST_STEP <= state.step <= LAST_STEP
        assert delta in (-1, 0, 1)
        if delta == 1:
            assert state.last_verdict is Verdict.UP
        if delta == -1:
            assert state.last_verdict is Verdict.BACK
        if item.signs:
            assert state.last_verdict is Verdict.HALT
            assert state.step == before.step
            assert state.status is PathStatus.HALTED


@given(_injuries())
@settings(max_examples=120, deadline=None)
def test_a_step_up_needs_the_good_days_the_activity_and_for_running_the_pain_free_days(
    inj: Injury,
) -> None:
    state = initial_state(inj)
    for item in inj.checkins:
        before = state
        state = advance(before, item, inj.questionnaire)
        if state.last_verdict is not Verdict.UP:
            continue
        # the run that paid for the step was in `before` plus this day
        assert before.good_days + 1 >= defaults.GOOD_DAYS_TO_STEP_UP[before.step]
        assert (
            before.active_days + (item.pain_during is not None)
            >= (defaults.ACTIVE_DAYS_TO_STEP_UP[before.step])
        )
        assert item.pain_during is None or item.pain_during.value <= 3
        assert item.pain_morning <= before.baseline
        if state.step is Step.RUN_WALK:
            q = inj.questionnaire
            assert before.pain_free_days + 1 >= required_pain_free_days(q.zone, q.onset)


@given(_injuries())
@settings(max_examples=120, deadline=None)
def test_the_base_level_never_rises_and_a_halted_path_never_wakes_by_itself(inj: Injury) -> None:
    state = initial_state(inj)
    for item in inj.checkins:
        before = state
        state = advance(before, item, inj.questionnaire)
        assert state.baseline <= before.baseline
        if before.status is PathStatus.HALTED:
            assert state.status is PathStatus.HALTED
            assert state.step == before.step


@given(_injuries())
@settings(max_examples=120, deadline=None)
def test_a_missing_day_leaves_at_most_the_run_of_the_day_itself(inj: Injury) -> None:
    state = initial_state(inj)
    last = None
    for item in inj.checkins:
        gap = last is not None and (item.day - last).days != 1
        state = advance(state, item, inj.questionnaire)
        if gap and state.last_verdict in (Verdict.GOOD, Verdict.UP):
            assert state.good_days <= 1
        last = item.day


@given(_injuries())
@settings(max_examples=100, deadline=None)
def test_follow_is_the_same_as_advancing_check_by_check_and_is_deterministic(inj: Injury) -> None:
    state = initial_state(inj)
    for item in inj.checkins:
        state = advance(state, item, inj.questionnaire)
    assert follow(inj) == state
    assert follow(inj) == follow(inj)


# --- A naive implementation of the written rules ------------------------------------------------
def naive_step(inj: Injury) -> tuple[int, bool]:
    """The step and whether the path is halted, from the rules as written in the skill: nothing
    shared with the code under test."""
    q = inj.questionnaire
    step, halted = 0, bool(q.alarms())
    baseline = q.pain.next_morning.value
    good = active = free = 0
    previous = None
    for c in inj.checkins:
        if c.signs:
            step_halted = True
            halted, good, active, free = step_halted, 0, 0, 0
            previous = c.day
            continue
        if halted:
            previous = c.day
            continue
        if previous is None or (c.day - previous).days != 1:
            good = active = free = 0
        previous = c.day
        during = None if c.pain_during is None else c.pain_during.value
        morning = c.pain_morning.value
        free = free + 1 if morning == 0 and during in (None, 0) else 0
        if (during is not None and during > 5) or morning > baseline:
            step, good, active = max(step - 1, 0), 0, 0
        elif during is not None and during > 3:
            good = active = 0
        else:
            baseline = min(baseline, morning)
            good += 1
            active += during is not None
            needed = free >= required_pain_free_days(q.zone, q.onset) or step + 1 != 2
            if (
                good >= defaults.GOOD_DAYS_TO_STEP_UP[Step(step)]
                and active >= (defaults.ACTIVE_DAYS_TO_STEP_UP[Step(step)])
                and step < 5
                and needed
            ):
                step, good, active = step + 1, 0, 0
    return step, halted


@given(_injuries())
@settings(max_examples=150, deadline=None)
def test_the_path_agrees_with_a_naive_implementation_of_the_rules(inj: Injury) -> None:
    state = follow(inj)
    step, halted = naive_step(inj)
    assert (int(state.step), state.status is PathStatus.HALTED) == (step, halted)
