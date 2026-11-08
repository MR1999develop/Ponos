# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""T-PIANO-01: same inputs, same plan, byte for byte; the order of the goals does not matter."""

from __future__ import annotations

import itertools
import json
from datetime import timedelta

from hypothesis import given, settings
from hypothesis import strategies as st

from ponos_coach.goals.domain import Priority
from ponos_coach.planning.domain import Plan, build_plan
from tests.planning.builders import BIG_WEEK, TODAY, fitness_goal, inputs, race

RACES = [
    race("a", TODAY + timedelta(weeks=20), Priority.A, 21_097, 6600),
    race("b", TODAY + timedelta(weeks=10), Priority.B, 10_000),
    race("c", TODAY + timedelta(weeks=6), Priority.C, 5_000),
]
GOALS = [
    fitness_goal("f1", TODAY + timedelta(weeks=14), 50),
    fitness_goal("f2", TODAY + timedelta(weeks=3), 35),
]


def _json(plan: object) -> str:
    assert isinstance(plan, Plan)
    return plan.to_json()


def test_the_same_inputs_give_the_same_bytes() -> None:
    first = _json(build_plan(inputs(RACES, GOALS, avail=BIG_WEEK, ctl=30, atl=35), TODAY))
    second = _json(build_plan(inputs(RACES, GOALS, avail=BIG_WEEK, ctl=30, atl=35), TODAY))
    assert first == second
    assert first.encode("ascii")  # canonical JSON is plain ASCII


def test_every_order_of_the_goals_gives_the_same_bytes() -> None:
    texts = {
        _json(build_plan(inputs(races, goals, avail=BIG_WEEK, ctl=30, atl=35), TODAY))
        for races in itertools.permutations(RACES)
        for goals in itertools.permutations(GOALS)
    }
    assert len(texts) == 1


@given(st.permutations(RACES), st.permutations(GOALS), st.integers(0, 80))
@settings(max_examples=25, deadline=None)
def test_goal_order_never_matters_whatever_the_fitness(
    races: list[object], goals: list[object], ctl: int
) -> None:
    ordered = build_plan(inputs(RACES, GOALS, ctl=float(ctl), atl=float(ctl)), TODAY)
    shuffled = build_plan(inputs(races, goals, ctl=float(ctl), atl=float(ctl)), TODAY)  # type: ignore[arg-type]
    assert _json(ordered) == _json(shuffled)


def test_the_text_is_canonical_json() -> None:
    text = _json(build_plan(inputs(RACES, GOALS, avail=BIG_WEEK), TODAY))
    data = json.loads(text)
    assert json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True) == text
    assert data["method_version"] == 4
    assert data["generated_on"] == TODAY.isoformat()


def test_the_plan_records_what_generated_it() -> None:
    plan = build_plan(inputs(RACES, GOALS, avail=BIG_WEEK, after_injury=True), TODAY)
    assert isinstance(plan, Plan)
    assert plan.method_version == 4
    assert dict(plan.parameters)["max_ramp_per_week"] == 3.0
    assert [r.goal_id.value for r in plan.inputs.races] == ["c", "b", "a"]  # normalised order
    assert plan.inputs.availability == BIG_WEEK


def test_another_day_gives_another_plan() -> None:
    later = build_plan(inputs(RACES, GOALS, avail=BIG_WEEK), TODAY + timedelta(days=1))
    now = build_plan(inputs(RACES, GOALS, avail=BIG_WEEK), TODAY)
    assert _json(now) != _json(later)
