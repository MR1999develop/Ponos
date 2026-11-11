# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""A cycling race: no target time; its time comes from distance and elevation gain at a cycling
speed, and the rules written for running distances (class and taper, recovery, CTL to reach, long
session) see it as the run of equal duration. The plan trains on the bike with sessions by time and
heart rate, never by pace."""

from __future__ import annotations

from datetime import timedelta

import pytest

from ponos_coach.activities.domain import Sport
from ponos_coach.goals.domain import RaceGoal, SessionType, takes_target_time
from ponos_coach.planning.domain import (
    MissingLongSession,
    Phase,
    Plan,
    PlannedRace,
    build_plan,
    defaults,
)
from ponos_coach.planning.domain.target import cycling_time_s, effort_distance_m
from tests.planning.builders import BIG_WEEK, TODAY, inputs, race
from tests.planning.invariants import violations

RACE_DAY = TODAY + timedelta(weeks=20)
NO_LONG = tuple(t for t in SessionType if t is not SessionType.LONG)


def planned(*races: RaceGoal, **kwargs: object) -> Plan:
    plan = build_plan(
        inputs(races, avail=BIG_WEEK, choices_for=Sport.CYCLING, **kwargs),  # type: ignore[arg-type]
        TODAY,
    )
    assert isinstance(plan, Plan)
    return plan


def bike_race(distance_m: int, ascent_m: int = 0, target_time_s: int | None = None) -> RaceGoal:
    return race(
        "a", RACE_DAY, distance_m=distance_m, ascent_m=ascent_m,
        target_time_s=target_time_s, sport=Sport.CYCLING,
    )  # fmt: skip


def planned_race(plan: Plan) -> PlannedRace:
    found = [d.race for d in plan.days if d.race is not None]
    assert len(found) == 1
    assert found[0] is not None
    return found[0]


def taper_days(plan: Plan) -> int:
    return sum(1 for d in plan.days if d.phase is Phase.TAPER)


# --- The estimate ------------------------------------------------------------------------------
def test_a_thousand_metres_of_climb_add_an_hour_at_twenty_five_km_per_hour() -> None:
    assert cycling_time_s(100_000, 0) == 4 * 3600
    assert cycling_time_s(100_000, 1_000) == 5 * 3600
    assert cycling_time_s(400, 0) == round(400 * 3600 / 25_000)


def test_an_hour_of_riding_is_seen_as_a_ten_km_run() -> None:
    assert effort_distance_m(3600) == 10_000
    assert effort_distance_m(1) == 3  # never zero


def test_only_cycling_races_go_without_target_time() -> None:
    assert not takes_target_time(Sport.CYCLING)
    assert all(takes_target_time(s) for s in (Sport.RUNNING, Sport.TRAIL, Sport.HIKING))


def test_a_cycling_race_saved_with_a_time_keeps_it_but_the_plan_ignores_it() -> None:
    goal = bike_race(80_000, 600, target_time_s=3 * 3600)
    assert goal.target_time_s == 3 * 3600
    assert goal.planned_target_time_s is None
    assert race("r", RACE_DAY, target_time_s=2400).planned_target_time_s == 2400


# --- The race in the plan ----------------------------------------------------------------------
def test_the_plan_expects_the_estimated_time_and_never_the_saved_target() -> None:
    plan = planned(bike_race(100_000, 1_000, target_time_s=3 * 3600))

    expected = planned_race(plan)
    assert expected.sport is Sport.CYCLING
    assert expected.expected_time_s == 5 * 3600
    assert plan.targets == ()  # no verdict on a target time


@pytest.mark.parametrize(
    ("distance_m", "ascent_m", "taper"),
    [
        (20_000, 0, defaults.TAPER_DAYS_SHORT),  # 48 min: a 8 km run
        (40_000, 0, defaults.TAPER_DAYS_HALF),  # 1 h 36: a 16 km run
        (50_000, 500, defaults.TAPER_DAYS_LONG),  # 2 h 30: a 25 km run, beyond the half
        (150_000, 2_000, defaults.TAPER_DAYS_LONG),
    ],
    ids=["short", "half", "long-by-climb", "gran-fondo"],
)
def test_the_taper_goes_by_the_duration_of_the_ride_not_its_kilometres(
    distance_m: int, ascent_m: int, taper: int
) -> None:
    assert taper_days(planned(bike_race(distance_m, ascent_m))) == taper


def test_forty_km_on_the_bike_are_not_a_marathon() -> None:
    run = build_plan(inputs([race("a", RACE_DAY, distance_m=40_000)], avail=BIG_WEEK), TODAY)
    ride = planned(bike_race(40_000))
    assert isinstance(run, Plan)
    assert taper_days(run) == defaults.TAPER_DAYS_LONG
    assert taper_days(ride) == defaults.TAPER_DAYS_HALF


def test_a_short_ride_needs_no_long_session_and_a_long_one_does() -> None:
    short = planned(bike_race(40_000), types=NO_LONG)
    long = planned(bike_race(60_000, 800), types=NO_LONG)

    assert not any(isinstance(w, MissingLongSession) for w in short.warnings)
    assert any(isinstance(w, MissingLongSession) for w in long.warnings)


def test_the_training_is_on_the_bike_by_time_and_heart_rate_never_by_pace() -> None:
    plan = planned(bike_race(120_000, 1_500))

    sessions = [d.session for d in plan.days if d.session is not None]
    training = [
        s for s in sessions if s.session_type not in (SessionType.STRENGTH, SessionType.ALTERNATIVE)
    ]
    assert training
    assert all(s.sport is Sport.CYCLING for s in training)
    assert all(s.minutes > 0 for s in training)
    for session in training:
        for block in session.blocks:
            assert block.hr_low_bpm <= block.hr_high_bpm
            assert block.pace_slow_s_per_km is None
            assert block.pace_fast_s_per_km is None
            assert block.race_pace_s_per_km is None
    assert all(s.ascent_m == 0 for s in training)  # no hill work on the bike


def test_a_cycling_plan_respects_every_constraint() -> None:
    for goal in (bike_race(20_000), bike_race(90_000, 1_200), bike_race(200_000, 3_500)):
        plan = planned(goal)
        assert violations(plan) == []


def test_the_cycling_plan_is_the_same_every_time() -> None:
    goal = bike_race(90_000, 1_200)
    assert planned(goal) == planned(goal)


def test_a_running_race_is_planned_as_before() -> None:
    goal = race("a", RACE_DAY, distance_m=21_097, target_time_s=5400)
    plan = build_plan(inputs([goal], avail=BIG_WEEK), TODAY)
    assert isinstance(plan, Plan)
    assert planned_race(plan).expected_time_s == 5400
    assert taper_days(plan) == defaults.TAPER_DAYS_HALF
