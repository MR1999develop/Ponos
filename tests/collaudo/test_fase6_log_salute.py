# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""T-SEC-08 for the injuries: health data never in logs or errors, never in the saved plan.

Recognisable values (the doctor's notes, a zone and a sign tampered into the database) go through
the commands, the plan and the reading of a tampered database. Then they are searched for in every
log record and in the text of every exception raised, with its chain of causes. The modules of the
feature must not even import `logging`, nor print.
"""

from __future__ import annotations

import ast
import logging
import sqlite3
from contextlib import closing
from datetime import timedelta
from pathlib import Path

import pytest

from ponos_coach.injury.domain import (
    BodyZone,
    InjuryId,
    WarningSign,
    plan_injuries,
)
from ponos_coach.injury.ports import InjuryAlreadyExistsError, InjuryStoreError
from ponos_coach.injury.use_cases import (
    ConfirmMedicalReview,
    NoWarningSignToReviewError,
    OpenInjury,
    RecordCheckIn,
)
from ponos_coach.planning.domain import Plan, build_plan
from ponos_coach.profiles.domain import PROFILE_DATABASE_NAME
from ponos_coach.storage.sqlite_profile import SqliteProfileStorage
from tests.collaudo.test_fase5_log_salute import chain_text
from tests.contracts.contract_storage import NAME, PROFILE_ID
from tests.injury.builders import DAY0, checkin, injury, questionnaire
from tests.planning.builders import BIG_WEEK, TODAY, inputs, race

ROOT = Path(__file__).resolve().parents[2] / "src" / "ponos_coach"
FEATURE_FILES = (
    *sorted((ROOT / "injury").rglob("*.py")),
    ROOT / "planning" / "domain" / "health.py",
    ROOT / "storage" / "sqlite_injury.py",
    ROOT / "profiles" / "rebuild_plan.py",
)
NOTE = "SENTINEL-NOTE-7731 knee clicks"
TAMPERED = "SENTINEL-ZONE-9915"


def test_the_modules_of_the_feature_neither_log_nor_print() -> None:
    assert len(FEATURE_FILES) > 8
    for path in FEATURE_FILES:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert "logging" not in [alias.name for alias in node.names], path.name
            if isinstance(node, ast.ImportFrom):
                assert node.module != "logging", path.name
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id != "print", path.name


def test_t_sec_08_no_value_of_an_injury_reaches_an_error_text_or_the_log(
    caplog: pytest.LogCaptureFixture, tmp_path: Path
) -> None:
    caplog.set_level(logging.DEBUG)
    errors: list[BaseException] = []
    with closing(SqliteProfileStorage().create(tmp_path, PROFILE_ID, NAME, "Europe/Rome")) as db:
        store, index = db.injuries, db.activities
        entry = injury("a", questionnaire(notes=NOTE, signs=[WarningSign.NIGHT_PAIN]))
        OpenInjury(store)(entry)
        for command in (
            lambda: OpenInjury(store)(entry),  # the same id again
            lambda: RecordCheckIn(index, store)(InjuryId("a"), checkin(-3)),  # before the opening
            lambda: ConfirmMedicalReview(index, store)(InjuryId("a"), DAY0 - timedelta(days=1)),
        ):
            with pytest.raises(InjuryStoreError) as caught:
                command()
            errors.append(caught.value)
        RecordCheckIn(index, store)(InjuryId("a"), checkin(1, signs=[WarningSign.FEVER]))
        ConfirmMedicalReview(index, store)(InjuryId("a"), DAY0 + timedelta(days=1))
        with pytest.raises(NoWarningSignToReviewError) as no_sign:
            ConfirmMedicalReview(index, store)(InjuryId("a"), DAY0 + timedelta(days=2))
        errors.append(no_sign.value)
    assert isinstance(errors[0], InjuryAlreadyExistsError)
    for error in errors:
        assert NOTE not in chain_text(error)
    assert NOTE not in caplog.text


def test_t_sec_08_a_tampered_database_gives_an_error_that_does_not_repeat_what_was_read(
    tmp_path: Path,
) -> None:
    with closing(SqliteProfileStorage().create(tmp_path, PROFILE_ID, NAME, "Europe/Rome")) as db:
        OpenInjury(db.injuries)(injury("a", questionnaire(notes=NOTE)))
    with closing(sqlite3.connect(tmp_path / PROFILE_DATABASE_NAME)) as raw:
        raw.execute("PRAGMA ignore_check_constraints = ON")
        raw.execute("UPDATE injury SET zone = ?", (TAMPERED,))
        raw.commit()

    with (
        closing(SqliteProfileStorage().open(tmp_path, check_integrity=False)) as db,
        pytest.raises(InjuryStoreError) as caught,
    ):
        db.injuries.list_problems()

    assert TAMPERED not in chain_text(caught.value)
    assert NOTE not in chain_text(caught.value)
    assert BodyZone.SHIN.value not in str(caught.value)  # not even the valid values around it


def test_t_sec_08_the_saved_plan_carries_no_note_and_no_pain_value() -> None:
    entry = injury("a", questionnaire(notes=NOTE, rest=3, walking=7, morning=6))
    given = plan_injuries([entry])
    plan = build_plan(
        inputs([race("a", TODAY + timedelta(weeks=12))], avail=BIG_WEEK, injuries=given), TODAY
    )
    assert isinstance(plan, Plan)
    text = plan.to_json()
    assert "SENTINEL" not in text
    assert "doctor_notes" not in text
    assert "pain" not in text.replace("pain_free", "")
