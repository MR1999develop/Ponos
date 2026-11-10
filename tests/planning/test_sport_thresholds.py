# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""The plan builds the zones of each session on the threshold of the session's sport."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

from ponos_coach.activities.domain import Sport
from ponos_coach.load.domain import HeartRateLimits, Physiology, Sex, ThresholdHeartRate
from ponos_coach.planning.domain import Plan, PlanInputs, build_plan
from ponos_coach.planning.domain.sessions import zone_ranges, zone_rates
from tests.planning.builders import BIG_WEEK, TODAY, inputs, physiology, race

RACE_DAY = TODAY + timedelta(weeks=16)


def athlete(other: int | None = None) -> Physiology:
    return Physiology(
        HeartRateLimits(50, 190),
        ThresholdHeartRate(170),
        Sex.MALE,
        other_threshold=None if other is None else ThresholdHeartRate(other),
    )


def bike_plan(physiology_of_athlete: Physiology) -> Plan:
    goal = race("a", RACE_DAY, sport=Sport.CYCLING, distance_m=40_000)
    raw: PlanInputs = inputs([goal], avail=BIG_WEEK, choices_for=Sport.CYCLING)
    result = build_plan(replace(raw, physiology=physiology_of_athlete), TODAY)
    assert isinstance(result, Plan)
    return result


def highest_hr_of_the_zone_one_blocks(plan: Plan) -> set[int]:
    return {
        block.hr_high_bpm
        for day in plan.days
        if day.session is not None
        for block in day.session.blocks
        if block.zone == 1
    }


def test_the_zone_ranges_of_a_bike_session_follow_the_other_sports_lt2() -> None:
    default = zone_ranges(athlete(), Sport.CYCLING, None).hr
    entered = zone_ranges(athlete(other=150), Sport.CYCLING, None).hr

    assert default != entered
    assert default == zone_ranges(athlete(), Sport.RUNNING, None).hr  # no value: the running LT2
    assert entered[0][0] == 50
    assert entered[-1][1] == 190
    assert (
        zone_ranges(athlete(other=150), Sport.RUNNING, None).hr
        == zone_ranges(athlete(), Sport.RUNNING, None).hr
    )


def test_the_zone_ranges_of_every_other_sport_follow_the_same_lt2_as_the_bike() -> None:
    assert (
        zone_ranges(athlete(other=140), Sport.WALKING, None).hr
        != zone_ranges(athlete(), Sport.WALKING, None).hr
    )
    assert (
        zone_ranges(athlete(other=140), Sport.CYCLING, None).hr
        == zone_ranges(athlete(other=140), Sport.WALKING, None).hr
    )


def test_the_load_per_minute_of_a_zone_follows_the_other_sports_lt2() -> None:
    assert zone_rates(athlete(other=150), Sport.CYCLING) != zone_rates(athlete(), Sport.CYCLING)
    assert zone_rates(athlete(other=150), Sport.RUNNING) == zone_rates(athlete(), Sport.RUNNING)


def test_a_bike_plan_has_its_blocks_on_the_cycling_threshold() -> None:
    default_plan = bike_plan(physiology())
    entered_plan = bike_plan(athlete(other=150))

    sessions = [d.session for d in entered_plan.days if d.session is not None]
    assert sessions
    assert Sport.CYCLING in {s.sport for s in sessions}
    for session in sessions:
        expected_hr = zone_ranges(athlete(other=150), session.sport, None).hr
        for block in session.blocks:
            assert (block.hr_low_bpm, block.hr_high_bpm) == expected_hr[block.zone - 1]
    assert highest_hr_of_the_zone_one_blocks(entered_plan) != highest_hr_of_the_zone_one_blocks(
        default_plan
    )


def test_the_plan_is_deterministic_with_the_sport_thresholds() -> None:
    first = bike_plan(athlete(other=150))
    second = bike_plan(athlete(other=150))

    assert first.to_json() == second.to_json()
