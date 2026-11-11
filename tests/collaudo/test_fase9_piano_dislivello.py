# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""T-PIANO-11 as properties, with an oracle written again from ADR 017 (not the invariants module
of the unit tests): random goals with elevation gain on top of the scenarios of T-PIANO-02, and the
goals that cannot be reached with the hours the user has.

Rules checked on every plan: the rules of T-PIANO-02 still hold with climb; hill work only for the A
race of a running plan with a mean grade of at least 20 m/km, only in build and peak weeks, never in
an unload week; one hill-repeats session a week, with no pace target; a verdict "unreachable" is a
target more than 3% faster than the best time of the equivalent distance; the hours the plan says
are needed really reach the CTL; same inputs, same bytes.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import replace
from datetime import date, timedelta

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from ponos_coach.activities.domain import Sport
from ponos_coach.goals.domain import (
    GoalId,
    Priority,
    RaceGoal,
    SessionChoices,
    SessionType,
    WeeklyAvailability,
)
from ponos_coach.planning.domain import (
    CtlTargetNotReached,
    FitnessState,
    LimitedByHours,
    Phase,
    Plan,
    PlanInputs,
    PlanRefusal,
    TargetTimeUnreachable,
    WeekKind,
    build_plan,
)
from ponos_coach.planning.domain.target import (
    TargetClass,
    best_time_by_race_s,
    equivalent_distance_m,
)
from tests.collaudo.test_fase5_piano_proprieta import (
    PHYSIOLOGY,
    check_scenario,
    scenarios,
    week_of,
)

HILL_MIN_SLOPE_M_PER_KM = 20
HILL_PHASES = {Phase.BUILD, Phase.PEAK}
INTENSE = {SessionType.MEDIUM, SessionType.THRESHOLD, SessionType.VO2MAX}
TODAY = date(2026, 3, 2)


@st.composite
def hilly_scenarios(draw: st.DrawFn) -> tuple[PlanInputs, date]:
    inputs, today = draw(scenarios())
    grades = draw(
        st.lists(st.sampled_from([0, 5, 19, 20, 21, 35, 60, 120]), min_size=5, max_size=5)
    )
    races = tuple(
        replace(race, ascent_m=min(race.distance_m * grade // 1000, 30_000))
        for race, grade in zip(inputs.races, grades, strict=False)
    )
    return replace(inputs, races=races), today


def hill_violations(plan: Plan) -> list[str]:
    problems: list[str] = []
    steep_a = [
        r
        for r in plan.inputs.races
        if r.priority is Priority.A and r.ascent_m * 1000 >= HILL_MIN_SLOPE_M_PER_KM * r.distance_m
    ]
    kinds = {w.week_start: w.kind for w in plan.weeks}
    repeats_by_week: Counter[date] = Counter()
    for day in plan.days:
        session = day.session
        if session is None or session.ascent_m == 0:
            continue
        if not steep_a:
            problems.append(f"{day.day}: hill work without a steep A race")
        week = week_of(day.day)
        if day.phase not in HILL_PHASES:
            problems.append(f"{day.day}: hill work in the {day.phase.value} phase")
        if kinds.get(week) is WeekKind.UNLOAD:
            problems.append(f"{day.day}: hill work in an unload week")
        if session.session_type in INTENSE:
            repeats_by_week[week] += 1
            if any(b.pace_slow_s_per_km is not None for b in session.blocks if b.zone >= 4):
                problems.append(f"{day.day}: hill repeats with a pace target")
        elif session.session_type is not SessionType.LONG:
            problems.append(f"{day.day}: hill work in a {session.session_type.value} session")
    problems += [
        f"week {w}: {n} hill-repeats sessions" for w, n in repeats_by_week.items() if n > 1
    ]
    return problems


def unreachable_violations(plan: Plan) -> list[str]:
    problems: list[str] = []
    pace = plan.inputs.threshold_pace_s_per_km
    races = {r.goal_id.value: r for r in plan.inputs.races}
    unreachable = {w.goal_id for w in plan.warnings if isinstance(w, TargetTimeUnreachable)}
    for target in plan.targets:
        race = races[target.goal_id]
        if target.classification is TargetClass.UNREACHABLE:
            if target.goal_id not in unreachable:
                problems.append(f"{target.goal_id}: unreachable without a warning")
            if pace is None or race.target_time_s is None or target.best_time_s is None:
                problems.append(f"{target.goal_id}: a verdict without the data for it")
                continue
            days = max((race.day - plan.generated_on).days, 0)
            best = best_time_by_race_s(
                pace, equivalent_distance_m(race.distance_m, race.ascent_m), days
            )
            if not race.target_time_s * 100 < best * 97:
                problems.append(f"{target.goal_id}: unreachable but within 3% of {best:.0f} s")
        elif target.goal_id in unreachable:
            problems.append(f"{target.goal_id}: a warning without an unreachable verdict")
    return problems


def check_hilly(inputs: PlanInputs, today: date) -> None:
    check_scenario(inputs, today)  # T-PIANO-02 rules, determinism, order of goals, write and read
    result = build_plan(inputs, today)
    if isinstance(result, PlanRefusal):
        return
    assert hill_violations(result) == []
    assert unreachable_violations(result) == []
    flat = build_plan(
        replace(inputs, races=tuple(replace(r, ascent_m=0) for r in inputs.races)), today
    )
    if isinstance(flat, Plan):
        assert not [d for d in flat.days if d.session is not None and d.session.ascent_m]


@given(hilly_scenarios())
@settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.too_slow])
def test_t_piano_11_every_plan_with_climb_respects_the_rules_of_the_adr(
    scenario: tuple[PlanInputs, date],
) -> None:
    check_hilly(*scenario)


@pytest.mark.collaudo
@given(hilly_scenarios())
@settings(max_examples=2000, deadline=None, suppress_health_check=[HealthCheck.too_slow])
def test_t_piano_11_every_plan_with_climb_respects_the_rules_of_the_adr_long(
    scenario: tuple[PlanInputs, date],
) -> None:
    check_hilly(*scenario)


# --- Goals that cannot be reached ---------------------------------------------------------------


def a_race(distance_m: int, *, ascent_m: int = 0, target_s: int | None = None) -> RaceGoal:
    return RaceGoal(
        GoalId("a"),
        TODAY + timedelta(days=112),
        Priority.A,
        Sport.RUNNING,
        distance_m,
        target_s,
        ascent_m,
    )


def inputs_for(race: RaceGoal, *, minutes: int = 600, ctl: float = 30.0) -> PlanInputs:
    return PlanInputs(
        (race,),
        (),
        WeeklyAvailability(frozenset(range(7)), 6, 6, minutes),
        SessionChoices.of({Sport.RUNNING: frozenset(SessionType)}),
        PHYSIOLOGY,
        FitnessState(ctl, ctl),
        threshold_pace_s_per_km=300,
    )


def test_t_piano_11_a_target_time_unreachable_with_the_climb_is_flagged_and_the_plan_built() -> (
    None
):
    flat = build_plan(inputs_for(a_race(10_000, target_s=2_700)), TODAY)
    hilly = build_plan(inputs_for(a_race(10_000, ascent_m=800, target_s=2_700)), TODAY)

    assert isinstance(flat, Plan) and isinstance(hilly, Plan)
    assert not [w for w in flat.warnings if isinstance(w, TargetTimeUnreachable)]
    assert [w.goal_id for w in hilly.warnings if isinstance(w, TargetTimeUnreachable)] == ["a"]
    assert unreachable_violations(hilly) == []
    assert hilly.days  # a warning, not a refusal


def test_t_piano_11_an_absurd_target_time_is_unreachable_with_or_without_climb() -> None:
    for ascent in (0, 500, 3_000):
        plan = build_plan(inputs_for(a_race(42_195, ascent_m=ascent, target_s=600)), TODAY)
        assert isinstance(plan, Plan)
        assert [t.classification for t in plan.targets] == [TargetClass.UNREACHABLE]
        assert unreachable_violations(plan) == []


@pytest.mark.collaudo
@settings(max_examples=60, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    st.sampled_from([21_097, 42_195, 80_000, 160_000]),
    st.sampled_from([0, 20, 40, 60]),
    st.sampled_from([60, 120, 180, 300]),
    st.floats(0, 60),
)
def test_t_piano_11_the_hours_the_plan_says_are_needed_really_reach_the_ctl(
    distance_m: int, grade: int, minutes: int, ctl: float
) -> None:
    race = a_race(distance_m, ascent_m=min(distance_m * grade // 1000, 30_000))
    plan = build_plan(inputs_for(race, minutes=minutes, ctl=ctl), TODAY)
    if not isinstance(plan, Plan):
        return
    limited = [
        w
        for w in plan.warnings
        if isinstance(w, CtlTargetNotReached) and isinstance(w.limit, LimitedByHours)
    ]
    for warning in limited:
        assert isinstance(warning.limit, LimitedByHours)
        more = inputs_for(race, minutes=warning.limit.minutes_needed, ctl=ctl)
        better = build_plan(more, TODAY)
        assert isinstance(better, Plan)
        assert not [
            w
            for w in better.warnings
            if isinstance(w, CtlTargetNotReached) and w.goal_id == warning.goal_id
        ], f"{warning.limit.minutes_needed} min a week do not reach the CTL"
