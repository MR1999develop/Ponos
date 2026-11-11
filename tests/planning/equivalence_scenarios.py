# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Scenarios of the plan whose result was recorded with the hrTSS engine (method version 6, with
sport weights), before the move to TRIMPeq. Running only: the strength and alternative sessions
had weights of their own, which are gone, so they are not part of the equivalence."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from ponos_coach.assessment.domain import NoTest
from ponos_coach.goals.domain import Priority, SessionType
from ponos_coach.planning.domain import CtlTargetNotReached, Plan
from tests.planning.builders import BIG_WEEK, TODAY, availability, fitness_goal, race

RUNNING_TYPES = frozenset(SessionType) - {SessionType.STRENGTH, SessionType.ALTERNATIVE}


def _weeks(n: int) -> date:
    return TODAY + timedelta(weeks=n)


def scenarios() -> dict[str, dict[str, Any]]:
    """Name -> keyword arguments of the plan inputs builder (only the running session types)."""
    plain = _scenarios()
    return {name: {"types": RUNNING_TYPES, **kwargs} for name, kwargs in plain.items()}


def _scenarios() -> dict[str, dict[str, Any]]:
    return {
        "one_a_race_30_weeks": {
            "races": [race("a", _weeks(30))],
            "avail": BIG_WEEK,
            "ctl": 30.0,
            "atl": 30.0,
        },
        "ten_k_12_weeks_default": {"races": [race("a", _weeks(12))]},
        "half_marathon_with_b_race": {
            "races": [
                race("a", _weeks(16), distance_m=21_097),
                race("b", _weeks(9), Priority.B, distance_m=10_000),
            ],
            "ctl": 35.0,
            "atl": 38.0,
        },
        "marathon_target_time": {
            "races": [race("a", _weeks(24), distance_m=42_195, target_time_s=3 * 3600 + 30 * 60)],
            "avail": availability(days=[0, 1, 3, 5, 6], max_week_minutes=900),
            "ctl": 45.0,
            "atl": 50.0,
        },
        "two_a_races_and_fitness_goal": {
            "races": [race("a", _weeks(10)), race("b", _weeks(22), distance_m=21_097)],
            "fitness_goals": [fitness_goal("f", _weeks(30), 60.0)],
            "ctl": 25.0,
            "atl": 25.0,
        },
        "after_injury_lower_ramp": {
            "races": [race("a", _weeks(14))],
            "after_injury": True,
            "ctl": 20.0,
            "atl": 15.0,
        },
        "empty_fitness": {"races": [race("a", _weeks(10))], "ctl": 0.0, "atl": 0.0},
        "tired_athlete_recovers_first": {
            "races": [race("a", _weeks(12))],
            "ctl": 60.0,
            "atl": 110.0,
        },
        "fitness_goal_only": {"fitness_goals": [fitness_goal("f", _weeks(20), 60.0)], "ctl": 30.0},
        "threshold_test_due": {
            "races": [race("a", _weeks(12))],
            "retest": NoTest(),
            "ctl": 35.0,
            "atl": 35.0,
        },
        "few_types_chosen": {
            "races": [race("a", _weeks(12))],
            "types": [SessionType.EASY, SessionType.LONG, SessionType.THRESHOLD],
            "ctl": 40.0,
            "atl": 42.0,
        },
    }


def summary(plan: Plan) -> dict[str, Any]:
    """What the equivalence compares. `shape` is everything that does not depend on the scale of
    the load (days, phases, session types, minutes, zones, heart-rate ranges, warnings); `loads`
    are the numbers in the unit of the load, in the order the plan lists them."""
    shape: dict[str, Any] = {"days": [], "weeks": [], "warnings": []}
    loads: dict[str, list[float]] = {"days": [], "weeks": [], "projection": [], "warnings": []}
    for day in plan.days:
        session = day.session
        shape["days"].append(
            [
                day.day.isoformat(),
                day.phase.value,
                None
                if session is None
                else [
                    session.sport.value,
                    session.session_type.value,
                    session.minutes,
                    [
                        [b.role.value, b.minutes, b.zone, b.repeats, b.hr_low_bpm, b.hr_high_bpm]
                        for b in session.blocks
                    ],
                ],
                None if day.race is None else [day.race.goal_id, day.race.expected_time_s],
            ]
        )
        loads["days"] += [
            day.load,
            0.0 if session is None else session.load,
            0.0 if day.race is None else day.race.load,
        ]
    for week in plan.weeks:
        shape["weeks"].append(
            [week.week_start.isoformat(), week.kind.value, week.session_minutes, week.session_count]
        )
        loads["weeks"].append(week.load)
    for point in plan.projection:
        loads["projection"] += [point.ctl, point.atl, point.tsb]
    for warning in plan.warnings:
        shape["warnings"].append(type(warning).__name__)
        if isinstance(warning, CtlTargetNotReached):
            loads["warnings"] += [warning.projected_ctl, warning.target_ctl]
    return {"shape": shape, "loads": loads}
