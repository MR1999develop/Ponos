# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""T-INF-02: the path back. Up with pain at most 3/10 and a normal morning, down when it gets
worse, one step at a time, and a missing check-in means no step up. One test per rule."""

from __future__ import annotations

from datetime import timedelta

import pytest

from ponos_coach.injury.domain import (
    BodyZone,
    Onset,
    Overdue,
    PathStatus,
    Step,
    Verdict,
    WarningSign,
    advance,
    checkin_due,
    defaults,
    follow,
    initial_state,
    overdue,
    required_pain_free_days,
)
from tests.injury.builders import (
    DAY0,
    checkin,
    climb,
    fold,
    good_days,
    injury,
    pain,
    questionnaire,
)


def test_a_new_injury_starts_on_the_first_step_at_the_morning_pain_of_the_questionnaire() -> None:
    state = initial_state(injury(q=questionnaire(morning=3)))
    assert state.step is Step.ACTIVATION
    assert state.status is PathStatus.ACTIVE
    assert state.baseline == pain(3)
    assert state.entered_on == DAY0


# --- Up -----------------------------------------------------------------------------------------
def test_enough_good_days_with_activity_step_up_by_exactly_one() -> None:
    needed = defaults.GOOD_DAYS_TO_STEP_UP[Step.ACTIVATION]
    inj = injury()
    before = fold(inj, good_days(1, needed - 1))
    after = fold(inj, good_days(1, needed))
    assert before.step is Step.ACTIVATION
    assert (after.step, after.last_verdict) == (Step.WALKING, Verdict.UP)
    assert after.good_days == 0  # the run starts again on the new step


def test_pain_of_exactly_three_during_the_activity_is_still_a_good_day() -> None:
    days = [checkin(i, during=defaults.STEP_UP_MAX_PAIN_DURING, morning=2) for i in range(1, 4)]
    assert fold(injury(), days).step is Step.WALKING


def test_good_days_without_activity_do_not_step_up() -> None:
    state = fold(injury(), good_days(1, 10, active=False))
    assert state.step is Step.ACTIVATION
    assert state.good_days == 10  # they count, but the body was not asked for anything


def test_the_morning_at_the_base_level_is_normal_and_below_it_too() -> None:
    q = questionnaire(morning=2)
    assert fold(injury(q=q), good_days(1, 3, morning=2)).step is Step.WALKING
    assert fold(injury(q=q), good_days(1, 3, morning=0)).step is Step.WALKING


# --- Hold and down ------------------------------------------------------------------------------
@pytest.mark.parametrize("during", [4, 5])
def test_pain_between_the_up_limit_and_the_tolerated_one_holds_the_step(during: int) -> None:
    state = fold(injury(), [*good_days(1, 2), checkin(3, during=during, morning=0)])
    assert (state.step, state.good_days, state.last_verdict) == (Step.ACTIVATION, 0, Verdict.HOLD)


@pytest.mark.parametrize("during", [6, 10])
def test_pain_above_the_tolerated_limit_steps_back(during: int) -> None:
    days = climb(injury(), Step.RUN_WALK)
    state = fold(injury(checkins=days), [checkin(len(days) + 1, during=during, morning=0)])
    assert (state.step, state.last_verdict) == (Step.WALKING, Verdict.BACK)


def test_a_morning_above_the_base_level_steps_back_even_if_the_activity_did_not_hurt() -> None:
    days = climb(injury(), Step.RUN_WALK)  # the base level is 0 by now
    state = fold(injury(checkins=days), [checkin(len(days) + 1, 0, 1)])
    assert state.step is Step.WALKING
    assert state.last_verdict is Verdict.BACK


def test_the_base_level_also_follows_the_mornings_of_hold_and_back_days() -> None:
    q = questionnaire(morning=4)
    held = fold(injury(q=q), [checkin(1, 4, 0)])  # a hold day with a calm morning
    assert held.last_verdict is Verdict.HOLD
    assert held.baseline == pain(0)
    state = fold(injury(q=q), [checkin(1, 4, 0), checkin(2, 0, 1)])
    assert state.last_verdict is Verdict.BACK  # not a false worsening: 1 is above the 0 seen

    stepped_back = fold(injury(q=q), [checkin(1, 8, 0)])
    assert stepped_back.baseline == pain(0)


def test_stepping_back_goes_to_the_last_completed_step_not_further() -> None:
    days = climb(injury(), Step.CONTINUOUS_EASY)
    state = fold(injury(checkins=days), [checkin(len(days) + 1, during=7)])
    assert state.step is Step.RUN_WALK


def test_stepping_back_on_the_first_step_stays_there() -> None:
    state = fold(injury(), [checkin(1, during=8)])
    assert (state.step, state.last_verdict) == (Step.ACTIVATION, Verdict.BACK)


def test_the_base_level_only_goes_down() -> None:
    q = questionnaire(morning=4)
    state = fold(injury(q=q), [checkin(1, 1, 3), checkin(2, 1, 1), checkin(3, 1, 2)])
    assert state.baseline == pain(1)  # the third day was above it: back, and the base stays at 1
    assert state.last_verdict is Verdict.BACK


# --- Never a jump, never without a check-in -----------------------------------------------------
def test_a_long_run_of_perfect_days_climbs_one_step_at_a_time() -> None:
    inj = injury()
    state = follow(inj)
    seen = [state.step]
    for offset in range(1, 90):
        before = state.step
        state = advance(state, checkin(offset, 0, 0), inj.questionnaire)
        assert abs(state.step - before) <= 1
        if state.step != seen[-1]:
            seen.append(state.step)
    assert seen == list(Step)  # all six, in order, none skipped


def test_a_missing_check_in_breaks_the_run_and_no_step_is_taken() -> None:
    needed = defaults.GOOD_DAYS_TO_STEP_UP[Step.ACTIVATION]
    days = [*good_days(1, needed - 1), *good_days(needed + 1, 1)]  # one day is missing
    assert fold(injury(), days).step is Step.ACTIVATION
    assert fold(injury(), good_days(1, needed)).step is Step.WALKING


def test_without_any_check_in_the_path_does_not_move() -> None:
    assert follow(injury()).step is Step.ACTIVATION


# --- Pain-free days before running --------------------------------------------------------------
def test_the_first_running_step_needs_five_days_without_pain() -> None:
    assert required_pain_free_days(BodyZone.SHIN, Onset.GRADUAL) == 5
    inj = injury(q=questionnaire(morning=2))
    days = good_days(1, 30, morning=1)  # tolerated every day, but never pain-free
    state = fold(inj, days)
    assert state.step is Step.WALKING
    assert state.pain_free_days == 0
    free = good_days(31, defaults.PAIN_FREE_DAYS_BEFORE_RUNNING, during=0)
    assert fold(inj, [*days, *free]).step is Step.RUN_WALK
    assert fold(inj, [*days, *free[:-1]]).step is Step.WALKING


def test_a_gradual_pain_of_the_heel_needs_four_weeks() -> None:
    needed = required_pain_free_days(BodyZone.FOOT, Onset.GRADUAL)
    assert needed == defaults.PAIN_FREE_DAYS_BEFORE_RUNNING_HEEL == 28
    assert required_pain_free_days(BodyZone.FOOT, Onset.SUDDEN) == 5
    inj = injury(q=questionnaire(BodyZone.FOOT, onset=Onset.GRADUAL, morning=0))
    assert fold(inj, good_days(1, needed - 1, during=0)).step is Step.WALKING
    assert fold(inj, good_days(1, needed, during=0)).step is Step.RUN_WALK


def test_a_day_with_pain_restarts_the_count_of_pain_free_days() -> None:
    inj = injury(q=questionnaire(morning=0))
    days = [*good_days(1, 4, during=0), checkin(5, during=1), *good_days(6, 4, during=0)]
    state = fold(inj, days)
    assert state.step is Step.WALKING
    assert state.pain_free_days == 4


# --- The end of the path and the slow recovery --------------------------------------------------
def test_on_the_last_step_the_path_does_not_go_on_and_says_it_can_be_closed() -> None:
    days = climb(injury(), Step.INTENSITY)
    assert not fold(injury(checkins=days), []).ready_to_close
    more = good_days(len(days) + 1, defaults.GOOD_DAYS_TO_STEP_UP[Step.INTENSITY])
    final = fold(injury(checkins=days), more)
    assert final.step is Step.INTENSITY
    assert final.ready_to_close  # the app says so, it does not close anything itself


def test_three_weeks_on_the_same_step_is_slower_than_expected() -> None:
    inj = injury()
    state = follow(inj)
    just_before = DAY0 + timedelta(days=defaults.STEP_EXPECTED_DAYS - 1)
    on_time = DAY0 + timedelta(days=defaults.STEP_EXPECTED_DAYS)
    assert overdue(inj, state, just_before) is None
    assert overdue(inj, state, on_time) is Overdue.STEP


def test_twelve_weeks_in_all_is_slower_than_expected_whatever_the_step() -> None:
    inj = injury()
    late = DAY0 + timedelta(days=defaults.TOTAL_EXPECTED_DAYS)
    assert overdue(inj, follow(inj), late) is Overdue.TOTAL


def test_a_halted_path_is_already_with_the_doctor_so_it_is_not_slow() -> None:
    inj = injury(checkins=[checkin(1, signs=[WarningSign.FEVER])])
    assert overdue(inj, follow(inj), DAY0 + timedelta(days=200)) is None


def test_the_check_in_is_due_until_the_day_has_one_and_not_after_closing() -> None:
    inj = injury()
    today = DAY0 + timedelta(days=1)
    assert checkin_due(inj, today)
    assert not checkin_due(inj.with_checkin(checkin(1)), today)
    assert not checkin_due(inj.closed(DAY0), today)
    assert not checkin_due(inj, DAY0 - timedelta(days=1))  # not opened yet
