# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""T-DATI-01 for phase 4: from the frozen v4 database to v5 (threshold test history).

The v4 database below is written by hand, independent of the code (the released migrations v1 to
v3 are in the earlier files, v4 in `test_fase4b_migrazione.py`). It is shown that v5 does not
change after release, that a v4 profile migrates without losing anything and keeps a copy
(`backup/ponos-v4-v5.sqlite3`), that the migration is one transaction (killed at every statement
the database is still v4) and that the database itself refuses what the domain would refuse.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from ponos_coach.assessment.domain import ThresholdTest
from ponos_coach.nutrition.domain import ActivityLevel, ActivityLevelEntry
from ponos_coach.profiles.domain import PROFILE_DATABASE_NAME
from ponos_coach.profiles.ports import ProfileStorageError
from ponos_coach.storage import sqlite_database, sqlite_schema
from ponos_coach.storage.sqlite_profile import SqliteProfileStorage
from tests.builders import fit_identity
from tests.collaudo.test_database_del_profilo import rows, squash
from tests.collaudo.test_fase3_dati_personali import run_worker_until_death
from tests.collaudo.test_fase3_migrazione import table_names, user_version
from tests.collaudo.test_fase4b_migrazione import FROZEN_V4, write_frozen_v3
from tests.fixtures.generators.collaudo_fase3_worker import DIED

# Migration v5 as it was released. It does not change, like v1 to v4.
FROZEN_V5 = (
    """CREATE TABLE threshold_test (
        activity_key TEXT NOT NULL PRIMARY KEY
            CHECK (length(activity_key) BETWEEN 5 AND 128
                   AND substr(activity_key, 1, 4) IN ('fit:', 'gpx:')),
        test_date TEXT NOT NULL
            CHECK (date(test_date) IS test_date AND test_date >= '1900-01-01'),
        segment_start_ms INTEGER NOT NULL,
        threshold_bpm INTEGER NOT NULL CHECK (threshold_bpm BETWEEN 40 AND 250),
        pace_s_per_km INTEGER
            CHECK (pace_s_per_km IS NULL OR pace_s_per_km BETWEEN 90 AND 3600),
        method_version INTEGER NOT NULL CHECK (method_version >= 1)
    ) STRICT""",
)

V5_TABLES = {"threshold_test"}
KEY = "fit:255:1:1:2026-03-02T07:00:00+00:00"


def write_frozen_v4(path: Path) -> None:
    """A v4 database as the phase 4b app wrote it, with data in every table (without `src`)."""
    write_frozen_v3(path)
    with closing(sqlite3.connect(path)) as raw:
        for statement in FROZEN_V4:
            raw.execute(statement)
        raw.execute("UPDATE activities SET calories_kcal = 612.0")
        raw.execute("INSERT INTO activity_level VALUES ('2026-03-01', 'active')")
        raw.execute("INSERT INTO meal_times VALUES (1, 450, 780, 1200)")
        raw.execute("INSERT INTO nutrition_goal VALUES ('2026-03-01', NULL, 'weight_loss', 500)")
        raw.execute("PRAGMA user_version = 4")
        raw.commit()


KEPT = (
    "meta", "physiology", "load_cache", "sync_journal", "sync_cursor", "sync_state",
    "profile_body", "weight", "sport_weights", "activity_level", "meal_times", "nutrition_goal",
    "activities",
)  # fmt: skip


def test_t_dati_01_the_released_v5_migration_has_not_changed() -> None:
    released = tuple(squash(statement) for statement in sqlite_schema.MIGRATIONS[4])

    assert released == tuple(squash(statement) for statement in FROZEN_V5)
    assert sqlite_schema.SCHEMA_VERSION >= 5


def test_t_dati_01_a_frozen_v4_database_is_migrated_to_v5_with_data_intact_and_a_copy(
    tmp_path: Path,
) -> None:
    path = tmp_path / PROFILE_DATABASE_NAME
    write_frozen_v4(path)
    before = {table: rows(path, table) for table in KEPT}

    opened = SqliteProfileStorage().open(tmp_path)
    try:
        assert opened.info.format_version == sqlite_schema.SCHEMA_VERSION
        assert list(opened.activity_levels.list_entries()) == [
            ActivityLevelEntry(date(2026, 3, 1), ActivityLevel.ACTIVE)
        ]
    finally:
        opened.close()

    assert {table: rows(path, table) for table in KEPT} == before
    assert table_names(path) >= V5_TABLES
    [copy] = sorted((tmp_path / "backup").glob("*"))
    assert copy.name == f"ponos-v4-v{sqlite_schema.SCHEMA_VERSION}.sqlite3"
    assert user_version(copy) == 4
    assert V5_TABLES.isdisjoint(table_names(copy))
    assert {table: rows(copy, table) for table in KEPT} == before


def test_t_dati_01_a_v4_profile_starts_with_an_empty_history_and_keeps_new_tests(
    tmp_path: Path,
) -> None:
    write_frozen_v4(tmp_path / PROFILE_DATABASE_NAME)
    entry = ThresholdTest(
        fit_identity(), date(2026, 3, 2), datetime(2026, 3, 2, 7, 10, tzinfo=UTC), 168, None
    )
    opened = SqliteProfileStorage().open(tmp_path)
    try:
        assert opened.threshold_tests.list_tests() == []
        opened.threshold_tests.add(entry)
    finally:
        opened.close()

    reopened = SqliteProfileStorage().open(tmp_path)
    try:
        assert list(reopened.threshold_tests.list_tests()) == [entry]
    finally:
        reopened.close()


def test_t_dati_01_a_v5_migration_that_fails_halfway_leaves_the_v4_database_as_it_was(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / PROFILE_DATABASE_NAME
    write_frozen_v4(path)
    tables_before = table_names(path)
    broken_v5 = (sqlite_schema.MIGRATIONS[4][0], "CREATE TABLE questa_no (")  # a table, then error
    monkeypatch.setattr(sqlite_database, "MIGRATIONS", (*sqlite_schema.MIGRATIONS[:4], broken_v5))

    with pytest.raises((ProfileStorageError, sqlite3.Error)):
        SqliteProfileStorage().open(tmp_path)

    assert user_version(path) == 4
    assert table_names(path) == tables_before  # not even the table that had succeeded
    monkeypatch.undo()
    SqliteProfileStorage().open(tmp_path).close()  # the good round succeeds
    assert user_version(path) == sqlite_schema.SCHEMA_VERSION


@pytest.mark.parametrize("kill_at", range(1, len(sqlite_schema.MIGRATIONS[4]) + 1))
def test_t_dati_01_a_v4_database_killed_at_every_migration_statement_is_still_v4_then_migrates(
    tmp_path: Path, kill_at: int
) -> None:
    path = tmp_path / PROFILE_DATABASE_NAME
    write_frozen_v4(path)
    before = {table: rows(path, table) for table in KEPT}

    assert run_worker_until_death(tmp_path, kill_at) == DIED

    assert user_version(path) == 4  # the transaction was not committed
    assert V5_TABLES.isdisjoint(table_names(path))
    assert {table: rows(path, table) for table in KEPT} == before
    SqliteProfileStorage().open(tmp_path).close()
    assert user_version(path) == sqlite_schema.SCHEMA_VERSION


# --- The database refuses what the domain refuses -----------------------------------------


def migrated(tmp_path: Path) -> sqlite3.Connection:
    write_frozen_v4(tmp_path / PROFILE_DATABASE_NAME)
    SqliteProfileStorage().open(tmp_path).close()
    return sqlite3.connect(tmp_path / PROFILE_DATABASE_NAME)


def row(
    key: str = KEY,
    day: str = "2026-03-02",
    start_ms: int = 1_772_435_400_000,
    bpm: int = 168,
    pace: str = "300",
    version: int = 1,
) -> str:
    return (
        "INSERT INTO threshold_test VALUES "  # noqa: S608 - test data
        f"('{key}', '{day}', {start_ms}, {bpm}, {pace}, {version})"
    )


@pytest.mark.parametrize(
    "statement",
    [
        row(key="run:1"),  # neither fit nor gpx
        row(key="fit"),  # shorter than 5
        row(day="2026-02-30"),  # non-existent day
        row(day="1899-12-31"),  # before 1900-01-01
        row(bpm=39),  # off point: 40 is the minimum
        row(bpm=251),  # off point: 250 is the maximum
        row(pace="89"),  # off point: 90 is the minimum
        row(pace="3601"),  # off point: 3600 is the maximum
        row(version=0),
    ],
)
def test_the_database_refuses_threshold_tests_outside_the_domain_limits(
    tmp_path: Path, statement: str
) -> None:
    with closing(migrated(tmp_path)) as raw, pytest.raises(sqlite3.IntegrityError):
        raw.execute(statement)


@pytest.mark.parametrize(
    "statement",
    [
        row(key="gpx:" + "a" * 64),
        row(day="1900-01-01"),
        row(bpm=40),
        row(bpm=250),
        row(pace="NULL"),  # no distance: no pace
        row(pace="90"),
        row(pace="3600"),
    ],
)
def test_the_database_accepts_threshold_tests_on_the_limits(tmp_path: Path, statement: str) -> None:
    with closing(migrated(tmp_path)) as raw:
        raw.execute(statement)


def test_the_database_refuses_the_same_activity_twice(tmp_path: Path) -> None:
    with closing(migrated(tmp_path)) as raw:
        raw.execute(row())

        with pytest.raises(sqlite3.IntegrityError):
            raw.execute(row(bpm=170))


def test_the_history_survives_the_removal_of_the_activity_from_the_index(tmp_path: Path) -> None:
    with closing(migrated(tmp_path)) as raw:
        raw.execute(row())

        raw.execute("DELETE FROM activities")  # a rebuilt index: no foreign key holds the history

        assert raw.execute("SELECT COUNT(*) FROM threshold_test").fetchone() == (1,)
