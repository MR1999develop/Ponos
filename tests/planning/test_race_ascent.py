# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""A race with elevation gain: its time and pace are computed on the flat-equivalent distance, and
a steep A race has hill repeats and hilly long runs in its build and peak, never in the base, the
taper or an unload week. Without climb the plan is the one there always was."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from ponos_coach.activities.domain import Sport
from ponos_coach.goals.domain import Priority, RaceGoal, SessionType
from ponos_coach.injury.domain import BodyRestriction, BodyZone, InjuryId, PainFreeActivity, Step
from ponos_coach.planning.domain import (
    BlockRole,
    Phase,
    Plan,
    PlanDay,
    WeekKind,
    build_plan,
    defaults,
)
from ponos_coach.planning.domain.target import best_time_by_race_s, equivalent_distance_m
from tests.planning.builders import BIG_WEEK, TODAY, availability, inputs, race
from tests.planning.invariants import violations

RACE_DAY = TODAY + timedelta(weeks=20)
HILL_PHASES = (Phase.BUILD, Phase.PEAK)


def planned(*races: RaceGoal, **kwargs: object) -> Plan:
    plan = build_plan(inputs(races, avail=BIG_WEEK, ctl=40.0, atl=40.0, **kwargs), TODAY)  # type: ignore[arg-type]
    assert isinstance(plan, Plan)
    return plan


def trail_plan() -> Plan:
    """A 30 km race with 1500 m of climb (50 m/km), 20 weeks away."""
    return planned(race("a", RACE_DAY, distance_m=30_000, ascent_m=1_500))


def hill_days(plan: Plan) -> list[PlanDay]:
    return [d for d in plan.days if d.session is not None and d.session.ascent_m > 0]


def unload_days(plan: Plan) -> set[date]:
    return {
        d.day
        for w in plan.weeks
        if w.kind is WeekKind.UNLOAD
        for d in plan.days
        if w.week_start <= d.day < w.week_start + timedelta(days=7)
    }


def races_of(plan: Plan) -> list[PlanDay]:
    return [d for d in plan.days if d.race is not None]


# --- Time, pace and load on the flat-equivalent distance ---------------------------------------
def test_the_flat_equivalent_distance_counts_a_hundred_metres_of_climb_as_a_kilometre() -> None:
    assert defaults.ASCENT_EQUIVALENT_M_PER_M == 10
    assert equivalent_distance_m(10_000, 100) == 11_000
    assert equivalent_distance_m(10_000, 0) == 10_000


def test_a_race_with_climb_is_expected_to_take_longer_than_the_flat_one() -> None:
    flat = races_of(planned(race("a", RACE_DAY, distance_m=10_000)))[0].race
    hilly = races_of(planned(race("a", RACE_DAY, distance_m=10_000, ascent_m=150)))[0].race
    days = (RACE_DAY - TODAY).days
    assert flat is not None
    assert hilly is not None
    assert flat.expected_time_s == round(best_time_by_race_s(300, 10_000, days))
    assert hilly.expected_time_s == round(best_time_by_race_s(300, 11_500, days))
    assert hilly.expected_time_s > flat.expected_time_s
    assert hilly.load > flat.load
    assert (hilly.ascent_m, flat.ascent_m) == (150, 0)


def test_without_a_threshold_pace_the_default_pace_runs_on_the_equivalent_distance() -> None:
    plan = planned(race("a", RACE_DAY, distance_m=10_000, ascent_m=150), pace=None)
    expected = races_of(plan)[0].race
    assert expected is not None
    assert expected.expected_time_s == 11_500 * defaults.DEFAULT_RACE_PACE_S_PER_KM // 1000


def _race_paces(plan: Plan) -> set[int]:
    return {
        b.race_pace_s_per_km
        for d in plan.days
        if d.session
        for b in d.session.blocks
        if b.race_pace_s_per_km is not None
    }


def test_the_race_pace_is_the_target_time_over_the_equivalent_distance() -> None:
    # 10 km in 1:00:00 is 6:00/km flat; with 150 m of climb (11.5 km equivalent) it is 5:13/km
    flat = planned(race("a", RACE_DAY, distance_m=10_000, target_time_s=3600))
    hilly = planned(race("a", RACE_DAY, distance_m=10_000, target_time_s=3600, ascent_m=150))
    assert _race_paces(flat) == {360}
    assert _race_paces(hilly) == {313}


# --- Hill work in a steep race -----------------------------------------------------------------
def test_a_steep_a_race_has_hill_repeats_and_hilly_long_runs() -> None:
    plan = trail_plan()
    sessions = [d.session for d in hill_days(plan) if d.session]
    assert any(s.is_hill_repeats for s in sessions)
    assert any(s.is_hilly_long for s in sessions)
    assert violations(plan) == []


def test_hill_work_is_only_in_the_build_and_the_peak_and_never_in_an_unload_week() -> None:
    plan = trail_plan()
    unload = unload_days(plan)
    assert unload  # the plan is long enough to have unload weeks
    for item in hill_days(plan):
        assert item.phase in HILL_PHASES, item.day
        assert item.day not in unload, item.day


def test_every_long_run_of_a_loaded_build_or_peak_week_is_hilly_and_the_base_one_is_not() -> None:
    plan = trail_plan()
    unload = unload_days(plan)
    longs = [d for d in plan.days if d.session and d.session.session_type is SessionType.LONG]
    in_hill_phases = [d for d in longs if d.phase in HILL_PHASES]
    assert in_hill_phases
    for item in in_hill_phases:
        assert item.session is not None
        assert item.session.is_hilly_long == (item.day not in unload), item.day
    others = [d for d in longs if d.phase not in HILL_PHASES]
    assert others
    assert not any(d.session and d.session.ascent_m for d in others)


def test_one_hill_repeats_session_a_week_replaces_a_quality_session() -> None:
    plan = trail_plan()
    per_week: dict[date, int] = {}
    for item in hill_days(plan):
        assert item.session is not None
        if item.session.is_hill_repeats:
            monday = item.day - timedelta(days=item.day.weekday())
            per_week[monday] = per_week.get(monday, 0) + 1
    assert per_week
    assert max(per_week.values()) == 1


def test_hill_repeats_have_no_pace_target_and_the_work_is_in_a_high_zone() -> None:
    plan = trail_plan()
    repeats = [d.session for d in plan.days if d.session and d.session.is_hill_repeats]
    assert repeats
    for session in repeats:
        blocks = session.blocks
        assert all(b.pace_slow_s_per_km is None and b.pace_fast_s_per_km is None for b in blocks)
        assert all(b.race_pace_s_per_km is None for b in blocks)
        work = [b for b in blocks if b.role is BlockRole.WORK]
        assert work
        assert all(b.zone >= 4 for b in work)
        assert not session.race_pace


def test_the_hilly_long_run_has_the_zones_of_the_long_run_and_an_indicative_climb() -> None:
    plan = trail_plan()
    hilly = next(d.session for d in plan.days if d.session and d.session.is_hilly_long)
    # 7 km/h at 50 m/km: 350 m of climb an hour, to the nearest 10 m
    assert hilly.ascent_m == round(hilly.minutes * 7 * 50 / 60 / 10) * 10
    assert {b.zone for b in hilly.blocks} == {2, 3}
    assert all(b.pace_slow_s_per_km is None for b in hilly.blocks)


def test_the_same_race_without_climb_has_no_hill_work() -> None:
    plan = planned(race("a", RACE_DAY, distance_m=30_000))
    assert hill_days(plan) == []
    assert violations(plan) == []


@pytest.mark.parametrize(
    ("ascent_m", "hilly"), [(0, False), (199, False), (200, True), (201, True)]
)
def test_the_slope_threshold_has_its_on_and_off_points(ascent_m: int, hilly: bool) -> None:
    assert defaults.HILL_MIN_SLOPE_M_PER_KM == 20
    plan = planned(race("a", RACE_DAY, distance_m=10_000, ascent_m=ascent_m))
    assert bool(hill_days(plan)) is hilly


def test_a_steep_b_race_beside_a_flat_a_race_brings_no_hill_work() -> None:
    plan = planned(
        race("a", RACE_DAY, distance_m=10_000),
        race("b", RACE_DAY - timedelta(weeks=6), Priority.B, distance_m=10_000, ascent_m=800),
    )
    assert hill_days(plan) == []


def test_a_steep_race_that_is_not_a_run_has_no_hill_work() -> None:
    plan = planned(
        race("a", RACE_DAY, distance_m=60_000, ascent_m=2_000, sport=Sport.CYCLING),
        choices_for=Sport.CYCLING,
    )
    assert hill_days(plan) == []


# --- Injuries ------------------------------------------------------------------------------------
def _injured(step: Step) -> Plan:
    problem = BodyRestriction(
        InjuryId("i1"), BodyZone.SHIN, step, frozenset(PainFreeActivity), None
    )
    base = inputs(
        [race("a", RACE_DAY, distance_m=30_000, ascent_m=1_500)], avail=BIG_WEEK, ctl=40.0, atl=40.0
    )
    plan = build_plan(replace(base, injuries=(problem,)), TODAY)
    assert isinstance(plan, Plan)
    return plan


@pytest.mark.parametrize(
    "step", [Step.ACTIVATION, Step.RUN_WALK, Step.CONTINUOUS_EASY, Step.VOLUME]
)
def test_a_zone_that_limits_the_running_or_the_intensity_forbids_the_hills_too(step: Step) -> None:
    plan = _injured(step)
    assert hill_days(plan) == []
    assert violations(plan) == []


def test_at_the_last_step_of_the_path_the_hills_come_back() -> None:
    plan = _injured(Step.INTENSITY)
    assert hill_days(plan)
    assert violations(plan) == []


# --- Determinism ----------------------------------------------------------------------------------
def test_the_plan_with_hills_is_the_same_every_time() -> None:
    assert trail_plan().to_json() == trail_plan().to_json()
    assert defaults.METHOD_VERSION >= 8


# --- The invariants hold with hills too ---
@given(
    distance=st.sampled_from([5_000, 10_000, 21_097, 42_195, 80_000]),
    slope=st.integers(0, 120),
    weeks=st.integers(3, 40),
    types=st.sets(st.sampled_from(list(SessionType)), min_size=1),
    days=st.sets(st.integers(0, 6), min_size=1),
    ctl=st.floats(0, 100),
)
@settings(max_examples=120, deadline=None, suppress_health_check=[HealthCheck.too_slow])
def test_plans_with_a_steep_race_respect_every_constraint(
    distance: int, slope: int, weeks: int, types: set[SessionType], days: set[int], ctl: float
) -> None:
    ascent = min(distance * slope // 1000, distance // 2)
    avail = availability(days, long_day=min(days), max_week_minutes=900)
    goal = race("a", TODAY + timedelta(weeks=weeks), distance_m=distance, ascent_m=ascent)
    result = build_plan(inputs([goal], avail=avail, ctl=ctl, atl=ctl, types=types), TODAY)
    assert isinstance(result, Plan)
    assert violations(result) == []
