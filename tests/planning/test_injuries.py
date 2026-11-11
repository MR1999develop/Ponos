# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""T-INF-03: the plan with injuries. The zone that hurts is suspended, pain-free alternatives keep
the load, running comes back by grade with the path, the ramp is the lower one while it lasts, and
a warning sign or an illness leaves no session at all. The saved plan reads back as it was."""

from __future__ import annotations

from collections import Counter
from dataclasses import replace
from datetime import date, timedelta

import pytest

from ponos_coach.activities.domain import Sport
from ponos_coach.goals.domain import INTENSE_TYPES, SessionType
from ponos_coach.injury.domain import (
    BodyRestriction,
    BodyZone,
    HaltReason,
    IllnessKind,
    IllnessRestriction,
    InjuryId,
    PainFreeActivity,
    PlanInjury,
    Scope,
    Step,
)
from ponos_coach.planning.domain import (
    NoSessionPossible,
    Plan,
    PlanAdapted,
    PlanHalted,
    PlanInputs,
    build_plan,
    defaults,
)
from ponos_coach.planning.plan_codec import decode_plan, digest
from tests.planning.builders import BIG_WEEK, TODAY, inputs, race
from tests.planning.invariants import violations

ALL = frozenset(PainFreeActivity)
RACE_DAY = TODAY + timedelta(weeks=16)
RUNNING_TYPES = {
    SessionType.EASY,
    SessionType.LONG,
    SessionType.MEDIUM,
    SessionType.THRESHOLD,
    SessionType.VO2MAX,
    SessionType.RECOVERY,
}


def body(
    zone: BodyZone,
    step: Step,
    free: frozenset[PainFreeActivity] = ALL,
    ident: str = "i1",
    halted_by: Scope | None = None,
) -> BodyRestriction:
    return BodyRestriction(InjuryId(ident), zone, step, free, halted_by)


def base(**kwargs: object) -> PlanInputs:
    """A 16-week plan for a 10 km race, an hour a day, all types chosen."""
    return inputs([race("a", RACE_DAY)], avail=BIG_WEEK, ctl=40.0, atl=40.0, **kwargs)  # type: ignore[arg-type]


def planned(*injuries: PlanInjury, **kwargs: object) -> Plan:
    plan = build_plan(replace(base(**kwargs), injuries=tuple(injuries)), TODAY)
    assert isinstance(plan, Plan)
    return plan


def types(plan: Plan, days: int = 364) -> Counter[SessionType]:
    return Counter(d.session.session_type for d in plan.days[:days] if d.session)


# --- Without an injury nothing changes ----------------------------------------------------------
def test_without_injuries_the_plan_is_the_same_as_before() -> None:
    free = planned()
    assert not any(
        isinstance(w, PlanAdapted | PlanHalted | NoSessionPossible) for w in free.warnings
    )
    assert free.method_version == defaults.METHOD_VERSION
    assert dict(free.parameters)["max_ramp_per_week"] == defaults.MAX_RAMP_PER_WEEK
    assert violations(free) == []


# --- The zone that hurts is suspended, the alternatives keep the load --------------------------
@pytest.mark.parametrize("step", [Step.ACTIVATION, Step.WALKING])
def test_while_running_is_out_every_session_is_the_pain_free_alternative(step: Step) -> None:
    plan = planned(body(BodyZone.SHIN, step))
    kinds = set(types(plan))
    assert kinds == {SessionType.ALTERNATIVE}
    assert not kinds & RUNNING_TYPES
    sessions = [d.session for d in plan.days if d.session]
    assert all(s.sport is Sport.OTHER and s.load > 0 for s in sessions)
    assert PlanAdapted(("i1",)) in plan.warnings


def test_the_alternative_sessions_keep_the_fitness_close_to_where_it_was() -> None:
    free = planned()
    hurt = planned(body(BodyZone.SHIN, Step.ACTIVATION))
    start = 40.0
    assert hurt.projection[27].ctl >= 0.9 * start
    assert hurt.projection[27].ctl >= free.projection[27].ctl - 3.0


def test_nothing_declared_pain_free_means_rest_days_and_the_warning_that_says_so() -> None:
    plan = planned(body(BodyZone.SHIN, Step.ACTIVATION, frozenset()))
    assert types(plan, 28) == Counter()
    assert NoSessionPossible(("i1",)) in plan.warnings


def test_the_alternative_session_must_be_one_of_the_types_the_user_chose() -> None:
    chosen = [SessionType.EASY, SessionType.LONG, SessionType.THRESHOLD]  # no alternative chosen
    plan = planned(body(BodyZone.SHIN, Step.ACTIVATION), types=chosen)
    assert types(plan) == Counter()
    assert NoSessionPossible(("i1",)) in plan.warnings


def test_the_bike_is_not_a_free_alternative_for_the_knee() -> None:
    only_bike = frozenset({PainFreeActivity.CYCLING})
    knee = planned(body(BodyZone.KNEE, Step.ACTIVATION, only_bike))
    shin = planned(body(BodyZone.SHIN, Step.ACTIVATION, only_bike))
    assert types(knee, 28) == Counter()  # the bike is a step of the path for the knee
    assert types(shin, 28)[SessionType.ALTERNATIVE] > 0
    walking = planned(body(BodyZone.KNEE, Step.WALKING, only_bike))
    assert types(walking, 28)[SessionType.ALTERNATIVE] > 0


# --- Running comes back by grade ---------------------------------------------------------------
def test_run_and_walk_has_only_recovery_runs_on_alternate_days() -> None:
    plan = planned(body(BodyZone.SHIN, Step.RUN_WALK))
    runs = [d for d in plan.days if d.session and d.session.session_type is SessionType.RECOVERY]
    assert runs
    assert set(types(plan)) <= {SessionType.RECOVERY, SessionType.STRENGTH}
    assert len({d.day.toordinal() % 2 for d in plan.days if d.session}) == 1  # one parity only
    assert all(
        d.session.session_type is not SessionType.ALTERNATIVE for d in plan.days if d.session
    )


def test_continuous_easy_running_has_easy_runs_and_no_long_or_intense_ones() -> None:
    kinds = set(types(planned(body(BodyZone.SHIN, Step.CONTINUOUS_EASY))))
    assert SessionType.EASY in kinds
    assert not kinds & ({SessionType.LONG} | INTENSE_TYPES)


def test_the_volume_step_brings_back_the_long_run_but_not_the_intensity() -> None:
    kinds = set(types(planned(body(BodyZone.SHIN, Step.VOLUME))))
    assert SessionType.LONG in kinds
    assert not kinds & INTENSE_TYPES


def test_the_last_step_brings_everything_back() -> None:
    kinds = set(types(planned(body(BodyZone.SHIN, Step.INTENSITY))))
    assert kinds & INTENSE_TYPES
    assert SessionType.LONG in kinds


def test_strength_waits_for_the_first_running_step_except_for_the_upper_body() -> None:
    assert SessionType.STRENGTH not in types(planned(body(BodyZone.CALF, Step.WALKING)))
    assert SessionType.STRENGTH in types(planned(body(BodyZone.CALF, Step.RUN_WALK)))
    assert SessionType.STRENGTH in types(planned(body(BodyZone.UPPER_BODY, Step.ACTIVATION)))


def test_a_cyclist_with_a_shin_injury_keeps_cycling_and_one_with_a_knee_injury_does_not() -> None:
    def cycling_plan(zone: BodyZone) -> Plan:
        goal = race("a", RACE_DAY, sport=Sport.CYCLING, distance_m=40_000)
        raw = inputs([goal], avail=BIG_WEEK, ctl=40.0, atl=40.0, choices_for=Sport.CYCLING)
        result = build_plan(replace(raw, injuries=(body(zone, Step.ACTIVATION),)), TODAY)
        assert isinstance(result, Plan)
        return result

    assert types(cycling_plan(BodyZone.SHIN), 28)[SessionType.EASY] > 0
    assert types(cycling_plan(BodyZone.KNEE), 28)[SessionType.EASY] == 0


# --- Several problems: the most restrictive wins (CSM1 cap. 19) --------------------------------
def test_with_two_zones_the_plan_follows_the_more_restrictive_one() -> None:
    one = planned(body(BodyZone.SHIN, Step.VOLUME))
    two = planned(
        body(BodyZone.SHIN, Step.VOLUME, ident="a"), body(BodyZone.KNEE, Step.RUN_WALK, ident="b")
    )
    assert SessionType.LONG in types(one)
    assert set(types(two)) <= {SessionType.RECOVERY, SessionType.STRENGTH}
    assert PlanAdapted(("a", "b")) in two.warnings


# --- The weekly ramp ----------------------------------------------------------------------------
def test_the_ramp_is_the_lower_one_while_an_injury_is_open_and_after_the_return() -> None:
    open_injury = planned(body(BodyZone.SHIN, Step.INTENSITY))
    after = planned(after_injury=True)
    assert dict(open_injury.parameters)["max_ramp_per_week"] == 3.0
    assert dict(after.parameters)["max_ramp_per_week"] == 3.0
    assert violations(open_injury) == []
    assert violations(after) == []


# --- Illness ------------------------------------------------------------------------------------
def test_an_illness_above_the_neck_leaves_only_short_easy_recovery_runs() -> None:
    plan = planned(IllnessRestriction(InjuryId("s"), IllnessKind.NECK_UP))
    assert set(types(plan)) <= {SessionType.RECOVERY, SessionType.ALTERNATIVE}
    assert not set(types(plan)) & (INTENSE_TYPES | {SessionType.LONG})


def test_a_systemic_illness_leaves_no_session_and_says_why() -> None:
    plan = planned(IllnessRestriction(InjuryId("s"), IllnessKind.SYSTEMIC))
    assert types(plan) == Counter()
    assert PlanHalted(HaltReason.ILLNESS, ("s",)) in plan.warnings
    assert all(d.load == (d.race.load if d.race else 0.0) for d in plan.days)


def test_the_halted_plan_still_has_the_race_and_the_projection() -> None:
    plan = planned(IllnessRestriction(InjuryId("s"), IllnessKind.SYSTEMIC))
    assert [d.race.goal_id for d in plan.days if d.race] == ["a"]
    assert plan.projection[0].ctl < 40  # the fitness decays with no training


def test_the_plan_is_empty_for_a_warning_sign_but_the_reason_names_the_scope() -> None:
    zone = planned(body(BodyZone.CALF, Step.WALKING, halted_by=Scope.ZONE))
    whole = planned(body(BodyZone.CALF, Step.WALKING, halted_by=Scope.WHOLE_PLAN))
    assert types(zone) == types(whole) == Counter()
    assert PlanHalted(HaltReason.ZONE_SIGN, ("i1",)) in zone.warnings
    assert PlanHalted(HaltReason.WHOLE_PLAN_SIGN, ("i1",)) in whole.warnings


# --- Determinism and the saved plan -------------------------------------------------------------
def test_the_order_of_the_injuries_never_matters_and_the_same_input_gives_the_same_bytes() -> None:
    a = body(BodyZone.SHIN, Step.RUN_WALK, ident="a")
    b = body(BodyZone.KNEE, Step.WALKING, ident="b")
    assert planned(a, b).to_json() == planned(b, a).to_json() == planned(a, b).to_json()


def test_two_injuries_with_the_same_id_are_refused() -> None:
    with pytest.raises(ValueError, match="twice"):
        PlanInputs(
            (), (), BIG_WEEK, base().choices, base().physiology, base().fitness,
            injuries=(body(BodyZone.SHIN, Step.WALKING), body(BodyZone.KNEE, Step.WALKING)),
        )  # fmt: skip


def test_a_plan_with_injuries_is_read_back_from_its_text_exactly() -> None:
    plan = planned(
        body(BodyZone.SHIN, Step.RUN_WALK, frozenset({PainFreeActivity.CYCLING}), "a"),
        IllnessRestriction(InjuryId("s"), IllnessKind.NECK_UP),
    )
    text = plan.to_json()
    assert decode_plan(text, digest(text)).to_json() == text


def test_the_saved_plan_has_no_free_text_of_the_user() -> None:
    plan = planned(body(BodyZone.SHIN, Step.RUN_WALK))
    assert "notes" not in plan.to_json()


def test_the_plan_of_a_day_far_from_the_race_is_not_a_date_problem() -> None:
    # An injury opened long ago is only a snapshot: nothing in it depends on the date
    far = build_plan(
        replace(base(), injuries=(body(BodyZone.SHIN, Step.WALKING),)), TODAY + timedelta(days=30)
    )
    assert isinstance(far, Plan)
    assert far.generated_on == date(2026, 4, 1)
