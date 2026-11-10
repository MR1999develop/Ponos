# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""T-DATI-01 for schema v13: from the v12 database to v13 (entries that Garmin never serves).

The v13 migration rebuilds `sync_journal` with the new column `unanswered_rounds` (zero for every
row that exists) and the new failure cause `remote_not_provided`. A v12 profile must migrate with
every journal row intact and keep a copy (`backup/ponos-v12-v13.sqlite3`), the migration is one
transaction (killed at every statement the database is still v12), and the new database refuses a
row that is not coherent.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from ponos_coach.activities.domain import GarminActivityId, SyncStatus
from ponos_coach.profiles.domain import PROFILE_DATABASE_NAME
from ponos_coach.profiles.ports import ProfileStorageError
from ponos_coach.storage import sqlite_database, sqlite_schema
from ponos_coach.storage.sqlite_profile import SqliteProfileStorage
from tests.collaudo.test_database_del_profilo import rows
from tests.collaudo.test_fase3_dati_personali import run_worker_until_death
from tests.collaudo.test_fase3_migrazione import user_version
from tests.fixtures.generators.collaudo_fase3_worker import DIED

V12 = 12
START_MS = 1_772_000_000_000
COLUMNS = (
    "garmin_id, start_ms, duration_s, sport_key, has_heart_rate, status, failed_attempts, "
    "failure, updated_ms"
)
JOURNAL_V12 = [
    (1001, "downloaded", 0, None),
    (1002, "matched", 1, None),
    (1003, "pending", 0, None),
    (1004, "retryable", 2, "transient_error"),
    (1005, "retryable", 1, "rate_limited"),
    (1006, "failed", 3, "attempts_exhausted"),
    (1007, "failed", 1, "corrupt_file"),
]


def write_v12(folder: Path) -> None:
    """A v12 profile (the chain of migrations up to v12) with one journal row per situation."""
    path = folder / PROFILE_DATABASE_NAME
    with closing(sqlite3.connect(path)) as raw:
        for statements in sqlite_schema.MIGRATIONS[:V12]:
            for statement in statements:
                raw.execute(statement)
        raw.executemany(
            "INSERT INTO meta (key, value) VALUES (?, ?)",
            [
                ("profile_id", "00000000-0000-4000-8000-0000000000aa"),
                ("name", "Atleta di prova"),
                ("timezone", "Europe/Rome"),
            ],
        )
        for garmin_id, status, attempts, failure in JOURNAL_V12:
            raw.execute(
                f"INSERT INTO sync_journal ({COLUMNS}) "  # noqa: S608 - constants of the test
                "VALUES (?, ?, 3600.0, 'running', 1, ?, ?, ?, ?)",
                (garmin_id, START_MS, status, attempts, failure, START_MS + 5),
            )
        raw.execute(f"PRAGMA user_version = {V12}")
        raw.commit()


def journal_rows(path: Path) -> list[tuple[object, ...]]:
    with closing(sqlite3.connect(path)) as raw:
        query = "SELECT " + COLUMNS + " FROM sync_journal ORDER BY garmin_id"  # noqa: S608
        return raw.execute(query).fetchall()


def test_t_dati_01_a_v12_database_is_migrated_to_v13_with_every_journal_row_intact_and_a_copy(
    tmp_path: Path,
) -> None:
    path = tmp_path / PROFILE_DATABASE_NAME
    write_v12(tmp_path)
    before = journal_rows(path)
    assert len(before) == len(JOURNAL_V12)

    opened = SqliteProfileStorage().open(tmp_path)
    try:
        assert opened.info.format_version == sqlite_schema.SCHEMA_VERSION >= 13
        entry = opened.sync_journal.get(GarminActivityId(1004))
        assert entry is not None
        assert (entry.status, entry.failed_attempts, entry.unanswered_rounds) == (
            SyncStatus.RETRYABLE,
            2,
            0,
        )
        assert opened.sync_journal.counts()[SyncStatus.FAILED] == 2
    finally:
        opened.close()

    assert journal_rows(path) == before
    with closing(sqlite3.connect(path)) as raw:
        assert {r[0] for r in raw.execute("SELECT unanswered_rounds FROM sync_journal")} == {0}
        indexes = {r[1] for r in raw.execute("PRAGMA index_list(sync_journal)")}
    assert "sync_journal_to_do" in indexes  # the to-do index is back after the rebuild
    [copy] = sorted((tmp_path / "backup").glob("*"))
    assert copy.name == f"ponos-v12-v{sqlite_schema.SCHEMA_VERSION}.sqlite3"
    assert user_version(copy) == V12
    assert journal_rows(copy) == before
    with closing(sqlite3.connect(copy)) as raw:
        names = [r[1] for r in raw.execute("PRAGMA table_info(sync_journal)")]
    assert "unanswered_rounds" not in names


def test_t_dati_01_a_v13_migration_that_fails_halfway_leaves_the_v12_database_as_it_was(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / PROFILE_DATABASE_NAME
    write_v12(tmp_path)
    before = rows(path, "sync_journal")
    broken = (*sqlite_schema.MIGRATIONS[V12][:3], "CREATE TABLE questa_no (")
    monkeypatch.setattr(sqlite_database, "MIGRATIONS", (*sqlite_schema.MIGRATIONS[:V12], broken))

    with pytest.raises((ProfileStorageError, sqlite3.Error)):
        SqliteProfileStorage().open(tmp_path)

    assert user_version(path) == V12
    assert rows(path, "sync_journal") == before  # not dropped, not half renamed
    monkeypatch.undo()
    SqliteProfileStorage().open(tmp_path).close()  # the good round succeeds
    assert user_version(path) == sqlite_schema.SCHEMA_VERSION


@pytest.mark.parametrize("kill_at", range(1, len(sqlite_schema.MIGRATIONS[V12]) + 1))
def test_t_dati_01_a_v12_database_killed_at_every_migration_statement_is_still_v12_then_migrates(
    tmp_path: Path, kill_at: int
) -> None:
    path = tmp_path / PROFILE_DATABASE_NAME
    write_v12(tmp_path)
    before = journal_rows(path)

    assert run_worker_until_death(tmp_path, kill_at) == DIED

    assert user_version(path) == V12  # the transaction was not committed
    assert journal_rows(path) == before
    SqliteProfileStorage().open(tmp_path).close()
    assert user_version(path) == sqlite_schema.SCHEMA_VERSION
    assert journal_rows(path) == before


# --- The database refuses a row that is not coherent ----------------------------------------------

INSERT = (
    f"INSERT INTO sync_journal ({COLUMNS}, unanswered_rounds) "  # noqa: S608 - constants
    "VALUES (?, 0, 60.0, 'running', 1, ?, ?, ?, 0, ?)"
)


def migrated(tmp_path: Path) -> sqlite3.Connection:
    write_v12(tmp_path)
    SqliteProfileStorage().open(tmp_path).close()
    connection = sqlite3.connect(tmp_path / PROFILE_DATABASE_NAME)
    connection.execute("DELETE FROM sync_journal")
    return connection


@pytest.mark.parametrize(
    "row",
    [
        ("pending", 0, None, 0),
        ("downloaded", 1, None, 0),
        ("retryable", 1, "rate_limited", 0),
        ("retryable", 0, "transient_error", 1),  # silent rounds only: no attempt used
        ("retryable", 1, "transient_error", 2),
        ("failed", 0, "remote_not_provided", 3),
        ("failed", 3, "attempts_exhausted", 0),
    ],
    ids=lambda r: f"{r[0]}-{r[1]}-{r[2]}-{r[3]}",
)
def test_the_database_accepts_the_coherent_rows(
    tmp_path: Path, row: tuple[str, int, str | None, int]
) -> None:
    with closing(migrated(tmp_path)) as raw:
        raw.execute(INSERT, (1, row[0], row[1], row[2], row[3]))


@pytest.mark.parametrize(
    "row",
    [
        ("pending", 0, None, 1),  # to download with a round
        ("retryable", 0, "transient_error", 0),  # no attempt and no round
        ("failed", 0, "remote_not_provided", 0),
        ("failed", 0, "corrupt_file", 0),
        ("downloaded", 0, None, -1),
        ("retryable", 0, "remote_not_provided", 1),  # a final cause on a retryable entry
        ("failed", 1, "transient_error", 1),  # a retryable cause on a failed entry
        ("failed", 1, "causa inventata", 1),
    ],
    ids=lambda r: f"{r[0]}-{r[1]}-{r[2]}-{r[3]}",
)
def test_the_database_refuses_the_incoherent_rows(
    tmp_path: Path, row: tuple[str, int, str | None, int]
) -> None:
    with closing(migrated(tmp_path)) as raw, pytest.raises(sqlite3.IntegrityError):
        raw.execute(INSERT, (1, row[0], row[1], row[2], row[3]))
