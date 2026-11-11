# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""T-SEC-08 for the rest of phase 5: lean mass, weight, heart-rate limits, the 20-minute test and
the nutrition of the days ahead never reach a log record or the text of an error.

Recognisable values (a lean mass of 63.7 kg, a weight of 83.3 kg, limits of 47 and 197 bpm, a
threshold of 167) go through the real stack - successes and refusals - and are then searched for in
every log record and in every exception raised, with its chain of causes. The pure packages of the
phase must not import `logging` nor print.
"""

from __future__ import annotations

import ast
import json
import logging
import re
import sqlite3
from collections.abc import Callable
from contextlib import closing
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from ponos_coach.assessment.domain import ThresholdProtocol, ThresholdRejection, ThresholdTestResult
from ponos_coach.goals.domain import Priority
from ponos_coach.load.domain import (
    HeartRateLimits,
    Physiology,
    PhysiologyEntry,
    Sex,
    ThresholdHeartRate,
)
from ponos_coach.nutrition.domain import GoalPeriod, LeanMass, WeightLossGoal
from ponos_coach.planning.plan_codec import digest
from ponos_coach.profiles.domain import PROFILE_DATABASE_NAME, Weight, WeightEntry
from ponos_coach.profiles.lean_mass import (
    RemoveLeanMass,
    SetLeanMass,
    UndoLeanMass,
)
from tests.collaudo.fase4_support import Leg, run_fit, wire
from tests.collaudo.fase5_support import (
    FIRST_DAY,
    TODAY,
    local_start,
    make_world,
    race,
    reopen_world,
)
from tests.collaudo.test_fase5_log_salute import chain_text

ROOT = Path(__file__).resolve().parents[2] / "src" / "ponos_coach"
PURE_PACKAGES = (
    ROOT / "nutrition" / "domain",
    ROOT / "planning" / "domain",
    ROOT / "assessment" / "domain",
    ROOT / "goals" / "domain",
    ROOT / "load" / "domain",
)
SHELL_FILES = (
    ROOT / "profiles" / "lean_mass.py",
    ROOT / "profiles" / "day_nutrition.py",
    ROOT / "profiles" / "threshold_tests.py",
    ROOT / "profiles" / "rebuild_plan.py",
    ROOT / "assessment" / "use_cases.py",
    ROOT / "nutrition" / "use_cases.py",
    ROOT / "storage" / "sqlite_nutrition.py",
    ROOT / "storage" / "sqlite_assessment.py",
)
LEAN, WEIGHT, MIN_BPM, MAX_BPM, THRESHOLD = "63.7", "83.3", 47, 197, 167
FORBIDDEN = re.compile(
    rf"(?<![\w.:-])({re.escape(LEAN)}|{re.escape(WEIGHT)}|{MIN_BPM}|{MAX_BPM}|{THRESHOLD})(?![\w:-])"
)


def parsed(path: Path) -> ast.AST:
    return ast.parse(path.read_text(encoding="utf-8"))


def test_the_pure_packages_and_the_shell_modules_neither_log_nor_print() -> None:
    files = [p for package in PURE_PACKAGES for p in sorted(package.glob("*.py"))] + list(
        SHELL_FILES
    )
    assert len(files) > 25
    for path in files:
        for node in ast.walk(parsed(path)):
            if isinstance(node, ast.Import):
                assert "logging" not in [alias.name for alias in node.names], path.name
            if isinstance(node, ast.ImportFrom):
                assert node.module != "logging", path.name
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id != "print", path.name


def test_t_sec_08_nothing_of_the_lean_mass_the_weight_or_the_limits_reaches_a_log_or_an_error(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    errors: list[BaseException] = []

    def attempt(action: Callable[[], object]) -> object:
        try:
            return action()
        except Exception as exc:
            errors.append(exc)
            return None

    with caplog.at_level(logging.DEBUG):
        world = make_world(tmp_path / "w")
        session = world.profile.session
        session.physiology.put(
            PhysiologyEntry(
                FIRST_DAY,
                Physiology(
                    HeartRateLimits(MIN_BPM, MAX_BPM), ThresholdHeartRate(THRESHOLD), Sex.MALE
                ),
            )
        )
        session.weights.put(WeightEntry(FIRST_DAY, Weight(833)))
        session.goal_periods.add(GoalPeriod(FIRST_DAY, None, WeightLossGoal(500)))
        world.import_history(TODAY - timedelta(days=20), TODAY - timedelta(days=1), every=3)
        today = lambda: TODAY  # noqa: E731
        set_lean = SetLeanMass(session.activities, session.weights, session.lean_masses, today)
        undo = UndoLeanMass(session.activities, session.lean_masses)
        remove = RemoveLeanMass(session.activities, session.lean_masses)

        applied = set_lean(LeanMass.from_kg(Decimal(LEAN)), TODAY - timedelta(days=10))
        attempt(
            lambda: set_lean(LeanMass.from_kg(Decimal("20.1")), TODAY)
        )  # implausible for 83.3 kg
        attempt(lambda: set_lean(LeanMass.from_kg(Decimal(LEAN)), TODAY + timedelta(days=1)))
        attempt(lambda: LeanMass.from_kg(Decimal("83.35")))
        attempt(lambda: LeanMass.from_kg(Decimal("NaN")))
        replaced = set_lean(LeanMass.from_kg(Decimal("64.0")), TODAY - timedelta(days=10))
        attempt(lambda: undo(applied))  # the entry changed since: refused
        undo(replaced)
        attempt(lambda: remove(TODAY - timedelta(days=500)))
        remove(TODAY - timedelta(days=10))

        world.add_goal(race("a", TODAY + timedelta(days=70), Priority.A, 21_097))
        world.built()
        world.report(TODAY + timedelta(days=5))
        world.report(TODAY - timedelta(days=4))
        # a plan that was damaged by hand: read as unreadable, then replaced
        world.close()
        with closing(sqlite3.connect(world.base / "profilo" / PROFILE_DATABASE_NAME)) as connection:
            text = str(connection.execute("SELECT plan_json FROM plan_state").fetchone()[0])
            tree = json.loads(text)
            tree["days"][3]["session"] = {"_type": "PlannedSession", "minutes": MAX_BPM}
            broken = json.dumps(tree, sort_keys=True, separators=(",", ":"))
            connection.execute(
                "UPDATE plan_state SET plan_json = ?, plan_sha256 = ?", (broken, digest(broken))
            )
            connection.commit()
        again = reopen_world(world.base)
        attempt(lambda: again.report(TODAY + timedelta(days=5)))
        attempt(lambda: again.nutrition(TODAY + timedelta(days=5)))
        again.built()
        again.close()

    leaks = sorted({m for e in errors for m in FORBIDDEN.findall(chain_text(e))})
    assert errors, "the flow must have raised"
    assert leaks == []
    assert FORBIDDEN.findall(caplog.text) == []


def test_t_sec_08_nothing_of_the_20_minute_test_reaches_a_log_or_an_error(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    errors: list[BaseException] = []
    with caplog.at_level(logging.DEBUG):
        world = make_world(tmp_path / "w")
        session = world.profile.session
        session.physiology.put(
            PhysiologyEntry(
                FIRST_DAY,
                Physiology(
                    HeartRateLimits(MIN_BPM, MAX_BPM), ThresholdHeartRate(THRESHOLD), Sex.MALE
                ),
            )
        )
        lab = wire(world.profile, TODAY)
        day = TODAY - timedelta(days=3)
        good = lab.import_bytes(
            run_fit([Leg(1200, 180, 3.5)], local_start(day, 8), serial=801), "good.fit"
        )
        short = lab.import_bytes(
            run_fit([Leg(900, 180, 3.5)], local_start(day, 12), serial=802), "short.fit"
        )
        result = lab.evaluate(good.identity, ThresholdProtocol.TWENTY_MINUTES)
        assert isinstance(result, ThresholdTestResult)
        assert result.threshold_bpm == 171  # 180 x 0.95
        assert lab.evaluate(short.identity, ThresholdProtocol.TWENTY_MINUTES) is (
            ThresholdRejection.TOO_SHORT
        )
        applied = lab.apply(result)
        try:
            lab.apply(result)  # already a test
        except Exception as exc:
            errors.append(exc)
        lab.remove(applied)
        world.close()

    assert errors
    assert re.search(r"(?<![\w.:-])171(?![\w:-])", caplog.text) is None
    assert all(not re.search(r"(?<![\w.:-])171(?![\w:-])", chain_text(e)) for e in errors)
    assert FORBIDDEN.findall(caplog.text) == []
    assert all(FORBIDDEN.findall(chain_text(e)) == [] for e in errors)
