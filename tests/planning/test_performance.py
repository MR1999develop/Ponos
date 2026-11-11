# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""T-PERF: a plan of 52 weeks is built in well under a second."""

from __future__ import annotations

import time
from datetime import timedelta

from ponos_coach.goals.domain import Priority
from ponos_coach.planning.domain import Plan, build_plan
from tests.planning.builders import BIG_WEEK, TODAY, fitness_goal, inputs, race

BUDGET_S = 1.0


def test_a_52_week_plan_with_many_goals_takes_less_than_a_second() -> None:
    goals = [
        race("a1", TODAY + timedelta(weeks=20), Priority.A, 21_097, 6600),
        race("a2", TODAY + timedelta(weeks=44), Priority.A, 42_195, 13_800),
        race("b1", TODAY + timedelta(weeks=30), Priority.B, 10_000),
        race("c1", TODAY + timedelta(weeks=12), Priority.C, 5_000),
        race("c2", TODAY + timedelta(weeks=52), Priority.C, 10_000),
    ]
    plan_inputs = inputs(
        goals, [fitness_goal("f", TODAY + timedelta(weeks=8), 45)], avail=BIG_WEEK, ctl=35, atl=40
    )
    build_plan(plan_inputs, TODAY)  # warm-up: imports, caches of the interpreter
    started = time.perf_counter()
    plan = build_plan(plan_inputs, TODAY)
    elapsed = time.perf_counter() - started
    assert isinstance(plan, Plan)
    assert len(plan.days) == 364
    assert len(plan.weeks) == 53
    assert elapsed < BUDGET_S, f"{elapsed * 1000:.0f} ms"
