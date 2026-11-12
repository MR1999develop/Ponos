# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Sessions of more than one sport (user's request, 2026-11-12): up to a race of another sport than
the plan's, every second easy session of a week is in that sport, and in its last six weeks the
long session too; the intense sessions stay in the plan's sport, and after that race the plan
trains its own sport only."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

from ponos_coach.activities.domain import Sport
from ponos_coach.goals.domain import INTENSE_TYPES, Priority, RaceGoal, SessionChoices, SessionType
from ponos_coach.planning.domain import Plan, PlanDay, build_plan, defaults
from tests.planning.builders import TODAY, availability, inputs, race
from tests.planning.invariants import violations

A_DAY = TODAY + timedelta(weeks=30)
B_DAY = TODAY + timedelta(weeks=14)
WEEK = availability(sessions=5, long_day=6, max_week_minutes=600)
ALL = frozenset(SessionType)


def _plan(*races: RaceGoal, bike_types: frozenset[SessionType] | None = ALL) -> Plan:
    base = inputs(races, avail=WEEK)
    by_sport = {Sport.TRAIL: ALL}
    if bike_types is not None:
        by_sport[Sport.CYCLING] = bike_types
    plan = build_plan(replace(base, choices=SessionChoices.of(by_sport)), TODAY)
    assert isinstance(plan, Plan)
    assert violations(plan) == []
    return plan


def _trail_a() -> RaceGoal:
    return race("a", A_DAY, distance_m=30_000, sport=Sport.TRAIL)


def _bike_b() -> RaceGoal:
    return race("b", B_DAY, Priority.B, distance_m=100_000, sport=Sport.CYCLING)


def _sessions(plan: Plan) -> list[PlanDay]:
    return [d for d in plan.days if d.session is not None]


def test_before_a_bike_race_some_easy_sessions_are_on_the_bike() -> None:
    plan = _plan(_trail_a(), _bike_b())
    before = [d for d in _sessions(plan) if d.day < B_DAY]

    easy = [d for d in before if d.session and d.session.session_type is SessionType.EASY]
    bike = [d for d in easy if d.session and d.session.sport is Sport.CYCLING]
    assert bike
    assert len(bike) < len(easy)  # mixed, not all
    first_week = [d for d in easy if d.day < TODAY + timedelta(weeks=1)]
    if len(first_week) >= defaults.OTHER_SPORT_EASY_EVERY:
        assert any(d.session and d.session.sport is Sport.CYCLING for d in first_week)


def test_the_long_session_goes_on_the_bike_only_in_the_last_six_weeks() -> None:
    plan = _plan(_trail_a(), _bike_b())
    longs = [d for d in _sessions(plan) if d.session and d.session.session_type is SessionType.LONG]
    window = timedelta(days=defaults.OTHER_SPORT_LONG_DAYS)

    for day in longs:
        assert day.session is not None
        on_bike = day.session.sport is Sport.CYCLING
        assert on_bike == (B_DAY - window <= day.day < B_DAY and day.session.ascent_m == 0)
    assert any(d.session and d.session.sport is Sport.CYCLING for d in longs)


def test_intense_sessions_stay_in_the_sport_of_the_a_race() -> None:
    plan = _plan(_trail_a(), _bike_b())
    for day in _sessions(plan):
        assert day.session is not None
        if day.session.session_type in INTENSE_TYPES:
            assert day.session.sport is Sport.TRAIL


def test_after_the_bike_race_the_plan_trains_trail_only() -> None:
    plan = _plan(_trail_a(), _bike_b())
    after = [d for d in _sessions(plan) if d.day > B_DAY]
    assert after
    assert all(d.session and d.session.sport is not Sport.CYCLING for d in after)


def test_bike_sessions_go_by_time_and_heart_rate() -> None:
    plan = _plan(_trail_a(), _bike_b())
    for day in _sessions(plan):
        assert day.session is not None
        if day.session.sport is Sport.CYCLING:
            assert all(b.pace_slow_s_per_km is None for b in day.session.blocks)
            assert all(b.hr_low_bpm <= b.hr_high_bpm for b in day.session.blocks)


def test_without_a_race_of_another_sport_every_session_is_of_the_plans_sport() -> None:
    plan = _plan(_trail_a())
    assert all(
        d.session and d.session.sport in (Sport.TRAIL, Sport.STRENGTH, Sport.OTHER)
        for d in _sessions(plan)
    )


def test_a_bike_type_not_chosen_stays_in_the_plans_sport() -> None:
    plan = _plan(_trail_a(), _bike_b(), bike_types=frozenset({SessionType.EASY}))
    longs = [d for d in _sessions(plan) if d.session and d.session.session_type is SessionType.LONG]
    assert all(d.session and d.session.sport is Sport.TRAIL for d in longs)
    assert any(d.session and d.session.sport is Sport.CYCLING for d in _sessions(plan))


def test_the_mixed_plan_is_the_same_every_time() -> None:
    assert _plan(_trail_a(), _bike_b()) == _plan(_bike_b(), _trail_a())
