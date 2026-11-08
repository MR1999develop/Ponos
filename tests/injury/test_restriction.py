# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""What the plan may ask of a recovering body: the zone table, the grade of running, the most
restrictive rule when there are several problems, the window of the lower ramp."""

from __future__ import annotations

from datetime import timedelta

import pytest

from ponos_coach.injury.domain import (
    FREE,
    BodyRestriction,
    BodyZone,
    HaltReason,
    IllnessKind,
    IllnessRestriction,
    InjuryId,
    PainFreeActivity,
    RunAllowance,
    Scope,
    Step,
    compatible_activities,
    defaults,
    plan_injuries,
    plan_injury,
    recently_returned,
    restriction_for,
)
from tests.injury.builders import DAY0, checkin, illness, injury, questionnaire

ALL = frozenset(PainFreeActivity)
CYCLING = PainFreeActivity.CYCLING
WALKING = PainFreeActivity.WALKING


def body(
    zone: BodyZone,
    step: Step,
    free: frozenset[PainFreeActivity] = ALL,
    ident: str = "i1",
    halted_by: Scope | None = None,
) -> BodyRestriction:
    return BodyRestriction(InjuryId(ident), zone, step, free, halted_by)


# --- The bike is not neutral for the knee and the back (CSM1) -----------------------------------
@pytest.mark.parametrize("zone", [BodyZone.KNEE, BodyZone.LOWER_BACK])
def test_the_bike_is_a_step_of_the_path_for_the_knee_and_the_back_not_a_free_alternative(
    zone: BodyZone,
) -> None:
    assert CYCLING not in compatible_activities(zone, Step.ACTIVATION, ALL)
    assert CYCLING in compatible_activities(zone, Step.WALKING, ALL)


@pytest.mark.parametrize("zone", [BodyZone.SHIN, BodyZone.FOOT, BodyZone.ACHILLES])
def test_the_bike_is_a_free_alternative_for_the_shin_the_foot_and_the_achilles(
    zone: BodyZone,
) -> None:
    assert CYCLING in compatible_activities(zone, Step.ACTIVATION, ALL)


def test_swimming_and_water_running_are_always_compatible_walking_from_its_step() -> None:
    at_start = compatible_activities(BodyZone.KNEE, Step.ACTIVATION, ALL)
    assert {PainFreeActivity.SWIMMING, PainFreeActivity.WATER_RUNNING} <= at_start
    assert WALKING not in at_start
    assert WALKING in compatible_activities(BodyZone.KNEE, Step.WALKING, ALL)


def test_only_what_the_user_declared_does_not_hurt_is_ever_proposed() -> None:
    declared = frozenset({PainFreeActivity.SWIMMING})
    assert compatible_activities(BodyZone.SHIN, Step.INTENSITY, declared) == declared
    assert compatible_activities(BodyZone.SHIN, Step.INTENSITY, frozenset()) == frozenset()


# --- Running by grade ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("step", "running"),
    [
        (Step.ACTIVATION, RunAllowance.NONE),
        (Step.WALKING, RunAllowance.NONE),
        (Step.RUN_WALK, RunAllowance.RECOVERY),
        (Step.CONTINUOUS_EASY, RunAllowance.EASY),
        (Step.VOLUME, RunAllowance.EASY_LONG),
        (Step.INTENSITY, RunAllowance.FULL),
    ],
)
def test_running_comes_back_by_grade_with_the_step(step: Step, running: RunAllowance) -> None:
    assert restriction_for([body(BodyZone.SHIN, step)]).running is running


def test_intensity_only_on_the_last_step() -> None:
    assert not restriction_for([body(BodyZone.SHIN, Step.VOLUME)]).intense
    assert restriction_for([body(BodyZone.SHIN, Step.INTENSITY)]).intense


def test_sessions_on_alternate_days_in_the_first_running_steps_only() -> None:
    for step, alternate in [(Step.WALKING, False), (Step.RUN_WALK, True),
                            (Step.CONTINUOUS_EASY, True), (Step.VOLUME, False)]:  # fmt: skip
        assert restriction_for([body(BodyZone.SHIN, step)]).alternate_days is alternate


def test_strength_comes_back_with_the_first_running_step_except_for_the_upper_body() -> None:
    assert not restriction_for([body(BodyZone.CALF, Step.WALKING)]).strength
    assert restriction_for([body(BodyZone.CALF, Step.RUN_WALK)]).strength
    assert restriction_for([body(BodyZone.UPPER_BODY, Step.ACTIVATION)]).strength


# --- Several problems: the most restrictive rule of each kind (CSM1) ----------------------------
def test_with_two_zones_the_most_restrictive_running_wins() -> None:
    restriction = restriction_for(
        [body(BodyZone.KNEE, Step.VOLUME, ident="a"), body(BodyZone.SHIN, Step.RUN_WALK, ident="b")]
    )
    assert restriction.running is RunAllowance.RECOVERY
    assert restriction.limited_by == ("a", "b")
    assert restriction.alternate_days


def test_with_two_zones_only_an_activity_both_tolerate_is_an_alternative() -> None:
    shin = body(BodyZone.SHIN, Step.ACTIVATION, frozenset({CYCLING}), "a")
    knee = body(BodyZone.KNEE, Step.ACTIVATION, frozenset({PainFreeActivity.SWIMMING}), "b")
    assert restriction_for([shin, knee]).alternatives == frozenset()
    both = body(
        BodyZone.KNEE, Step.ACTIVATION, frozenset({CYCLING, PainFreeActivity.SWIMMING}), "b"
    )
    assert restriction_for([shin, both]).alternatives == frozenset()  # the bike hurts the knee
    swimmer = body(BodyZone.SHIN, Step.ACTIVATION, frozenset({PainFreeActivity.SWIMMING}), "a")
    assert restriction_for([swimmer, both]).alternatives == {PainFreeActivity.SWIMMING}


def test_nothing_open_nothing_limited() -> None:
    assert restriction_for([]) == FREE
    assert FREE.limits_nothing


# --- Illness (CSM2) -----------------------------------------------------------------------------
def test_an_illness_above_the_neck_allows_only_the_shortest_easy_session_and_no_intensity() -> None:
    restriction = restriction_for([IllnessRestriction(InjuryId("s"), IllnessKind.NECK_UP)])
    assert restriction.halt is None
    assert restriction.running is RunAllowance.RECOVERY
    assert not restriction.intense
    assert not restriction.strength


def test_a_systemic_illness_stops_everything_and_wins_over_the_other_reasons() -> None:
    sick = IllnessRestriction(InjuryId("s"), IllnessKind.SYSTEMIC)
    signed = body(BodyZone.KNEE, Step.WALKING, ident="k", halted_by=Scope.WHOLE_PLAN)
    restriction = restriction_for([signed, sick])
    assert (restriction.halt, restriction.halted_ids) == (HaltReason.ILLNESS, ("s",))


def test_the_zone_sign_and_the_whole_plan_sign_have_their_own_reasons() -> None:
    zone = body(BodyZone.KNEE, Step.WALKING, ident="a", halted_by=Scope.ZONE)
    whole = body(BodyZone.CALF, Step.WALKING, ident="b", halted_by=Scope.WHOLE_PLAN)
    assert restriction_for([zone]).halt is HaltReason.ZONE_SIGN
    assert restriction_for([zone, whole]).halt is HaltReason.WHOLE_PLAN_SIGN
    assert restriction_for([zone, whole]).halted_ids == ("b",)


# --- What the plan is given ---------------------------------------------------------------------
def test_only_open_problems_reach_the_plan_ordered_by_id() -> None:
    closed = injury("c", questionnaire()).closed(DAY0)
    open_b = injury("b", questionnaire(BodyZone.KNEE))
    open_a = illness(IllnessKind.NECK_UP, "a")
    given = plan_injuries([closed, open_b, open_a])
    assert [p.injury_id.value for p in given] == ["a", "b"]
    assert plan_injury(closed) is None
    assert plan_injury(open_a.recovered(DAY0)) is None


def test_the_snapshot_for_the_plan_carries_no_free_text() -> None:
    inj = injury("b", questionnaire(notes="doctor says: secret words"))
    snapshot = plan_injury(inj)
    assert snapshot is not None
    assert "secret" not in repr(snapshot)


def test_the_snapshot_follows_the_check_ins_and_the_signs() -> None:
    inj = injury("b", questionnaire(BodyZone.CALF), [checkin(1, during=0, morning=0)])
    snapshot = plan_injury(inj)
    assert isinstance(snapshot, BodyRestriction)
    assert (snapshot.zone, snapshot.step, snapshot.halted_by) == (
        BodyZone.CALF,
        Step.ACTIVATION,
        None,
    )


# --- The lower ramp after the return ------------------------------------------------------------
def test_the_ramp_is_lower_for_four_weeks_after_closing_and_not_before_or_after() -> None:
    closed = injury().closed(DAY0)
    window = defaults.RAMP_AFTER_RETURN_DAYS
    assert recently_returned([closed], DAY0)
    assert recently_returned([closed], DAY0 + timedelta(days=window))
    assert not recently_returned([closed], DAY0 + timedelta(days=window + 1))
    assert not recently_returned([injury()], DAY0)  # still open: that is the open injury's own rule
    assert recently_returned([closed], DAY0 - timedelta(days=1))  # closing dated ahead: D-F6-01
    assert recently_returned([illness().recovered(DAY0)], DAY0 + timedelta(days=3))
