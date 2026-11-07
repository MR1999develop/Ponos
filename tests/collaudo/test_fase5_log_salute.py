# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""T-SEC-08 for goals and the plan: fitness, heart rate and the plan itself never in logs or errors.

Recognisable values go through the goal commands, the plan rebuild and the reading of tampered
plan text (a form goal of 61.37 CTL, heart-rate limits of 47 and 197, a threshold of 167). Then they
are searched for in every log record and in the text of every exception raised, with its chain of
causes. The modules of the feature must not even import `logging`, nor print.
"""

from __future__ import annotations

import ast
import logging
import re
from collections.abc import Callable
from dataclasses import replace
from datetime import date, timedelta
from functools import partial
from pathlib import Path

import pytest

from ponos_coach.goals.domain import FitnessGoal, GoalId
from ponos_coach.goals.ports import GoalStoreError
from ponos_coach.goals.use_cases import AddGoal, ChangeGoal, RemoveGoal
from ponos_coach.load.domain import HeartRateLimits, Physiology, Sex, ThresholdHeartRate
from ponos_coach.planning.domain import Plan, build_plan
from ponos_coach.planning.plan_codec import PlanDecodingError, decode_plan, digest
from ponos_coach.planning.ports import FrozenDay
from ponos_coach.profiles.rebuild_plan import PlanBuilt
from tests.planning.builders import TODAY, inputs, race
from tests.profiles.test_rebuild_plan import PlanWorld

ROOT = Path(__file__).resolve().parents[2] / "src" / "ponos_coach"
FEATURE_FILES = (
    ROOT / "goals" / "ports.py",
    ROOT / "goals" / "use_cases.py",
    ROOT / "planning" / "ports.py",
    ROOT / "planning" / "plan_codec.py",
    ROOT / "planning" / "use_cases.py",
    ROOT / "profiles" / "rebuild_plan.py",
    ROOT / "storage" / "sqlite_goals.py",
    ROOT / "storage" / "sqlite_planning.py",
)
MIN_BPM, MAX_BPM, THRESHOLD_BPM, CTL = 47, 197, 167, 61.37
FORBIDDEN = re.compile(rf"(?<![\w.:-])({MIN_BPM}|{MAX_BPM}|{THRESHOLD_BPM}|{CTL})(?![\w:-])")


def chain_text(error: BaseException) -> str:
    """Message, repr, notes and the same for every cause and context."""
    parts: list[str] = []
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        parts += [str(current), repr(current), *getattr(current, "__notes__", [])]
        current = current.__cause__ or current.__context__
    return "\n".join(parts)


def _decode(text: str) -> Plan:
    return decode_plan(text, digest(text))


def test_the_modules_of_the_feature_neither_log_nor_print() -> None:
    for path in FEATURE_FILES:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert "logging" not in [alias.name for alias in node.names], path.name
            if isinstance(node, ast.ImportFrom):
                assert node.module != "logging", path.name
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id != "print", path.name


def test_t_sec_08_no_value_of_goals_or_plan_reaches_an_error_text_or_the_log(
    caplog: pytest.LogCaptureFixture,
) -> None:
    errors: list[BaseException] = []

    def attempt(action: Callable[[], object]) -> None:
        try:
            action()
        except Exception as exc:
            errors.append(exc)

    narrow = Physiology(
        HeartRateLimits(MIN_BPM, MAX_BPM), ThresholdHeartRate(THRESHOLD_BPM), Sex.MALE
    )
    with caplog.at_level(logging.DEBUG):
        world = PlanWorld()
        goal = FitnessGoal(GoalId("form"), TODAY + timedelta(days=30), CTL)
        add = AddGoal(world.profile.goals)
        add(goal)
        attempt(lambda: add(goal))  # id taken
        add(race("same-day", goal.day))
        attempt(lambda: add(race("other", goal.day)))  # a second race on that day
        attempt(
            lambda: ChangeGoal(world.profile.activities, world.profile.goals)(
                replace(goal, goal_id=GoalId("no"))
            )
        )
        attempt(lambda: RemoveGoal(world.profile.activities, world.profile.goals)(GoalId("no")))
        built = build_plan(replace(inputs([race("a", date(2026, 4, 5))]), physiology=narrow), TODAY)
        assert isinstance(built, Plan)
        text = built.to_json()
        attempt(lambda: decode_plan(text, digest(text + " ")))  # checksum
        for damaged in (
            text[:-5],
            text.replace('"ctl"', '"x"', 1),
            "[" * 40,
            text.replace("47", "-"),
        ):
            attempt(partial(_decode, damaged))
        attempt(lambda: FrozenDay(built.days[0].day, built.days[0]))  # planned on its own day
        result = world.rebuild(TODAY)
        assert isinstance(result, PlanBuilt)
        attempt(lambda: world.profile.goals.remove(GoalId("never-there")))

    assert errors, "the flow must have raised"
    assert any(isinstance(e, PlanDecodingError) for e in errors)
    assert any(isinstance(e, GoalStoreError) for e in errors)
    leaks = sorted({m for e in errors for m in FORBIDDEN.findall(chain_text(e))})
    assert leaks == []
    assert FORBIDDEN.findall(caplog.text) == []
