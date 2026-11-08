# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""T-INF-01: every warning sign, one test each, stops the plan and invites a doctor.

The app never says what a sign means: it records it, stops, and shows the invitation (the state
says `see_doctor`). The sign can come from the questionnaire, from a later check-in, or (strong rest
pain) from the pain scale itself.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

import pytest

from ponos_coach.injury.domain import (
    BodyZone,
    HaltReason,
    PathStatus,
    Scope,
    Step,
    WarningSign,
    defaults,
    follow,
    plan_injuries,
    restriction_for,
    scope_of,
)
from ponos_coach.planning.domain import Plan, PlanHalted, build_plan
from tests.injury.builders import DAY0, checkin, climb, fold, good_days, injury, questionnaire
from tests.planning.builders import BIG_WEEK, TODAY, inputs, race

SIGNS = list(WarningSign)
RACE_DAY = TODAY + timedelta(weeks=16)
WHOLE_PLAN = {
    WarningSign.FEVER,
    WarningSign.GENERAL_MALAISE,
    WarningSign.CHEST_PAIN,
    WarningSign.PALPITATIONS,
    WarningSign.FAINTING,
    WarningSign.DISPROPORTIONATE_BREATHLESSNESS,
}


def test_the_list_of_signs_has_the_ones_of_the_plan_and_of_the_sources() -> None:
    assert len(SIGNS) == 15  # a new sign is a decision: add its test here
    assert {s for s in SIGNS if scope_of(s) is Scope.WHOLE_PLAN} == WHOLE_PLAN


@pytest.mark.parametrize("sign", SIGNS)
def test_a_sign_in_the_questionnaire_halts_the_path_before_it_starts(sign: WarningSign) -> None:
    inj = injury(q=questionnaire(signs=[sign]))
    state = follow(inj)
    assert state.status is PathStatus.HALTED
    assert state.see_doctor
    assert state.alarms == {sign}
    assert state.scope is scope_of(sign)
    assert state.step is Step.ACTIVATION


@pytest.mark.parametrize("sign", SIGNS)
def test_a_sign_reported_on_a_check_in_halts_the_path_on_the_spot(sign: WarningSign) -> None:
    inj = injury()
    days = climb(inj, Step.WALKING)
    state = fold(injury(checkins=days), [checkin(len(days) + 1, 0, 0, signs=[sign])])
    assert (state.status, state.see_doctor) == (PathStatus.HALTED, True)
    assert state.alarms == {sign}


@pytest.mark.parametrize("sign", SIGNS)
def test_a_halted_path_does_not_move_whatever_the_days_say(sign: WarningSign) -> None:
    inj = injury(q=questionnaire(signs=[sign]))
    state = fold(inj, good_days(1, 40, during=0))  # forty perfect days
    assert state.status is PathStatus.HALTED
    assert state.step is Step.ACTIVATION


@pytest.mark.parametrize("sign", SIGNS)
def test_a_sign_stops_the_plan_with_no_session_and_says_why(sign: WarningSign) -> None:
    free = inputs([race("a", RACE_DAY)], avail=BIG_WEEK)
    plan = build_plan(free, TODAY)
    assert isinstance(plan, Plan)
    assert any(d.session for d in plan.days)  # without the injury there is training

    hurt = plan_injuries([injury("hurt", questionnaire(signs=[sign]))])
    halted = build_plan(replace(free, injuries=hurt), TODAY)

    assert isinstance(halted, Plan)
    assert not any(d.session for d in halted.days)
    reason = HaltReason.WHOLE_PLAN_SIGN if sign in WHOLE_PLAN else HaltReason.ZONE_SIGN
    assert PlanHalted(reason, ("hurt",)) in halted.warnings


def test_strong_rest_pain_alone_is_a_sign_from_the_limit_up() -> None:
    at = injury(q=questionnaire(rest=defaults.STRONG_REST_PAIN))
    below = injury(q=questionnaire(rest=defaults.STRONG_REST_PAIN - 1))
    assert follow(at).see_doctor
    assert follow(at).alarms == {WarningSign.STRONG_REST_PAIN}
    assert not follow(below).see_doctor


def test_night_pain_is_a_sign_even_with_a_small_pain_everywhere_else() -> None:
    q = questionnaire(rest=0, walking=0, running=None, morning=0, signs=[WarningSign.NIGHT_PAIN])
    assert follow(injury(q=q)).see_doctor


def test_a_whole_plan_sign_wins_over_a_zone_one_in_the_scope() -> None:
    inj = injury(q=questionnaire(signs=[WarningSign.NIGHT_PAIN, WarningSign.CHEST_PAIN]))
    assert follow(inj).scope is Scope.WHOLE_PLAN


# --- After a doctor has seen it -----------------------------------------------------------------
def test_a_medical_review_lets_the_path_start_again_from_the_first_step() -> None:
    inj = injury()
    days = climb(inj, Step.CONTINUOUS_EASY)
    sign_day = len(days) + 1
    halted = injury(checkins=[*days, checkin(sign_day, signs=[WarningSign.CALF_SWOLLEN_AND_WARM])])
    assert follow(halted).see_doctor
    reviewed = halted.with_review(DAY0 + timedelta(days=sign_day))
    state = follow(reviewed)
    assert (state.status, state.step, state.alarms) == (
        PathStatus.ACTIVE,
        Step.ACTIVATION,
        frozenset(),
    )
    assert state.entered_on == DAY0 + timedelta(days=sign_day)


def test_a_sign_after_the_review_halts_again() -> None:
    inj = injury(q=questionnaire(signs=[WarningSign.NIGHT_PAIN])).with_review(DAY0)
    assert not follow(inj).see_doctor
    again = inj.with_checkin(checkin(2, signs=[WarningSign.FAINTING]))
    assert follow(again).see_doctor
    assert follow(again).alarms == {WarningSign.FAINTING}


def test_the_days_after_a_review_count_towards_the_new_path() -> None:
    inj = injury(q=questionnaire(signs=[WarningSign.NIGHT_PAIN])).with_review(DAY0)
    days = good_days(1, defaults.GOOD_DAYS_TO_STEP_UP[Step.ACTIVATION])
    assert follow(injury(q=inj.questionnaire, checkins=days).with_review(DAY0)).step is Step.WALKING


def test_two_zones_one_with_a_sign_the_restriction_is_the_halt() -> None:
    well = injury("a", questionnaire(BodyZone.KNEE))
    hurt = injury("b", questionnaire(BodyZone.CALF, signs=[WarningSign.NIGHT_PAIN]))
    restriction = restriction_for(plan_injuries([well, hurt]))
    assert restriction.halt is HaltReason.ZONE_SIGN
    assert restriction.halted_ids == ("b",)
    assert restriction.limited_by == ("a", "b")
