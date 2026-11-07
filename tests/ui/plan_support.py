# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Plans built by the real engine for the interface tests: neutral data, made-up athlete."""

from __future__ import annotations

from datetime import date, timedelta

from ponos_coach.goals.domain import Priority, RaceGoal, SessionType
from ponos_coach.planning.domain import Plan, build_plan
from tests.planning.builders import ALL_TYPES, BIG_WEEK, TODAY, inputs, race

PLAN_TODAY = TODAY  # 2 March 2026, a Monday
MARATHON_DAY = PLAN_TODAY + timedelta(weeks=14)


def marathon(
    goal_id: str = "marathon", day: date = MARATHON_DAY, target_s: int | None = None
) -> RaceGoal:
    """An A marathon, with an optional target time."""
    return race(goal_id, day, Priority.A, 42_195, target_s)


def plan_for(
    *races: RaceGoal,
    types: frozenset[SessionType] = ALL_TYPES,
    pace: int | None = 300,
) -> Plan:
    """The plan the engine makes for the goals (it must not refuse)."""
    result = build_plan(
        inputs(races or (marathon(),), types=types, avail=BIG_WEEK, pace=pace), PLAN_TODAY
    )
    assert isinstance(result, Plan), result
    return result
