# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Deleted activities: the migration (T-DATI-01) and what the database itself enforces.

The migration is found by identity (`DELETED_ACTIVITY_MIGRATION`), not by its number. The store
contract runs in `test_sqlite_profile.py` (and on the fake in `tests/contracts/test_fakes.py`),
including the rule that an identity is either indexed or deleted.
"""

from __future__ import annotations

import hashlib
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from ponos_coach.activities.domain import GarminActivityId, SyncStatus, mark_skipped
from ponos_coach.profiles.domain import PROFILE_DATABASE_NAME, ProfileId, ProfileName
from ponos_coach.storage.schema_deleted_activity import DELETED_ACTIVITY_MIGRATION
from ponos_coach.storage.sqlite_database import create_database
from ponos_coach.storage.sqlite_profile import SqliteProfileStorage
from ponos_coach.storage.sqlite_schema import MIGRATIONS, SCHEMA_VERSION
from tests.builders import IMPORTED_AT, fit_identity, make_deleted, make_record

PROFILE_ID = "00000000-0000-4000-8000-000000000001"
META = {"profile_id": PROFILE_ID, "name": "Atleta di prova", "timezone": "Europe/Rome"}
# SHA-256 of the migration with the white space removed: a released migration never changes.
FROZEN_DIGEST = "b0e28502d3256ac543e092335f5cb2ab2197464fd55f043c44ab0e6efa537531"


def _position() -> int:
    """The version this migration comes after (its place in the list, found by identity)."""
    return next(i for i, m in enumerate(MIGRATIONS) if m is DELETED_ACTIVITY_MIGRATION)


def _digest() -> str:
    text = "".join("".join(statement.split()) for statement in DELETED_ACTIVITY_MIGRATION)
    return hashlib.sha256(text.encode()).hexdigest()


def test_the_released_migration_has_not_changed() -> None:
    assert _digest() == FROZEN_DIGEST


def _journal_row(raw: sqlite3.Connection, garmin_id: int, status: str, failure: str | None) -> None:
    attempts = 1 if status in ("retryable", "failed") else 0
    raw.execute(
        "INSERT INTO sync_journal (garmin_id, start_ms, duration_s, sport_key, has_heart_rate, "
        "status, failed_attempts, failure, updated_ms, unanswered_rounds) "
        "VALUES (?, 1772000000000, 60.0, 'running', NULL, ?, ?, ?, 1772000001000, 0)",
        (garmin_id, status, attempts, failure),
    )


def test_a_profile_from_before_the_migration_keeps_its_journal_and_gets_the_new_state(
    tmp_path: Path,
) -> None:
    path = tmp_path / PROFILE_DATABASE_NAME
    before = _position()
    with closing(sqlite3.connect(path)) as raw:
        for migration in MIGRATIONS[:before]:
            for statement in migration:
                raw.execute(statement)
        raw.executemany("INSERT INTO meta (key, value) VALUES (?, ?)", list(META.items()))
        _journal_row(raw, 1, "pending", None)
        _journal_row(raw, 2, "downloaded", None)
        _journal_row(raw, 3, "matched", None)
        _journal_row(raw, 4, "retryable", "transient_error")
        _journal_row(raw, 5, "failed", "remote_not_provided")
        raw.execute(f"PRAGMA user_version = {before}")
        raw.commit()

    opened = SqliteProfileStorage().open(tmp_path)
    try:
        assert opened.info.format_version == SCHEMA_VERSION
        counts = opened.sync_journal.counts()
        assert [counts[s] for s in SyncStatus] == [1, 1, 1, 1, 1, 0]  # enum order, last: skipped
        assert opened.deleted_activities.list_latest_first() == []
        entry = opened.sync_journal.get(GarminActivityId(1))
        assert entry is not None
        opened.sync_journal.update(mark_skipped(entry, IMPORTED_AT))
        assert opened.sync_journal.counts()[SyncStatus.SKIPPED] == 1
    finally:
        opened.close()
    [copy] = sorted((tmp_path / "backup").glob("*"))
    assert copy.name == f"ponos-v{before}-v{SCHEMA_VERSION}.sqlite3"


def test_the_journal_keeps_its_partial_index_for_the_entries_to_do(tmp_path: Path) -> None:
    path = tmp_path / PROFILE_DATABASE_NAME
    create_database(path, META).close()

    with closing(sqlite3.connect(path)) as raw:
        [(sql,)] = raw.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'index' AND name = 'sync_journal_to_do'"
        ).fetchall()
    assert "pending" in sql
    assert "skipped" not in sql


def test_a_skipped_entry_has_no_failure(tmp_path: Path) -> None:
    path = tmp_path / PROFILE_DATABASE_NAME
    create_database(path, META).close()

    with closing(sqlite3.connect(path)) as raw:
        _journal_row(raw, 1, "skipped", None)
        with pytest.raises(sqlite3.IntegrityError):
            _journal_row(raw, 2, "skipped", "corrupt_file")


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("sport", "swimming"),
        ("elapsed_s", 0.0),
        ("distance_m", -1.0),
        ("archive_sha256", "abc"),
        ("garmin_activity_id", 0),
        ("file_format", "gpx"),
        ("origin", "garmin"),  # without a Garmin id
    ],
)
def test_the_database_refuses_an_invalid_tombstone(
    tmp_path: Path, column: str, value: object
) -> None:
    path = tmp_path / PROFILE_DATABASE_NAME
    create_database(path, META).close()
    base: dict[str, object] = {
        "identity_key": "fit:255:1:1:2026-03-02T07:00:00+00:00",
        "file_format": "fit",
        "sport": "running",
        "start_ms": 1000,
        "elapsed_s": 60.0,
        "distance_m": None,
        "archive_path": "2026/" + "a" * 64 + ".fit",
        "archive_sha256": "a" * 64,
        "archive_size_bytes": 10,
        "origin": "import",
        "imported_ms": 1000,
        "garmin_activity_id": None,
        "deleted_ms": 2000,
    }
    values = base | {column: value}
    names = ", ".join(values)
    marks = ", ".join("?" for _ in values)

    with closing(sqlite3.connect(path)) as raw, pytest.raises(sqlite3.IntegrityError):
        raw.execute(
            f"INSERT INTO deleted_activity ({names}) VALUES ({marks})",  # noqa: S608
            list(values.values()),
        )


def test_a_tombstone_survives_closing_and_reopening_the_profile(tmp_path: Path) -> None:
    storage = SqliteProfileStorage()
    opened = storage.create(
        tmp_path, ProfileId.parse(PROFILE_ID), ProfileName.parse("Atleta di prova"), "UTC"
    )
    tombstone = make_deleted(make_record(fit_identity(3)))
    opened.deleted_activities.add(tombstone)
    opened.close()

    again = storage.open(tmp_path)
    try:
        assert again.deleted_activities.get(fit_identity(3)) == tombstone
    finally:
        again.close()
