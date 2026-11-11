# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""T-DATI-01 for schema v7: from the frozen v6 database to v7 (test protocol, measured lean mass).

The v6 database is written by hand, independent of the code (v1 to v6 are in the earlier files).
It is shown that v7 does not change after release, that a v6 profile migrates without losing
anything (its tests count as 30-minute tests) and keeps a copy (`backup/ponos-v6-v7.sqlite3`),
that the migration is one transaction (killed at every statement the database is still v6), and
that the database itself refuses what the domain would refuse.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import date
from pathlib import Path

import pytest

from ponos_coach.assessment.domain import ThresholdProtocol
from ponos_coach.nutrition.domain import LeanMass, LeanMassEntry
from ponos_coach.profiles.domain import PROFILE_DATABASE_NAME
from ponos_coach.profiles.ports import ProfileStorageError
from ponos_coach.storage import sqlite_database, sqlite_schema
from ponos_coach.storage.sqlite_profile import SqliteProfileStorage
from tests.collaudo.test_database_del_profilo import rows, squash
from tests.collaudo.test_fase3_dati_personali import run_worker_until_death
from tests.collaudo.test_fase3_migrazione import table_names, user_version
from tests.collaudo.test_fase4_migrazione import FROZEN_V5
from tests.collaudo.test_fase5_migrazione import FROZEN_V6, KEPT_V5, V6_TABLES, write_frozen_v5
from tests.fixtures.generators.collaudo_fase3_worker import DIED

# Migration v7 as it was released. It does not change, like v1 to v6.
FROZEN_V7 = (
    """ALTER TABLE threshold_test ADD COLUMN protocol TEXT NOT NULL DEFAULT 'thirty_minutes'
        CHECK (protocol IN ('thirty_minutes','twenty_minutes'))""",
    """CREATE TABLE lean_mass (
        valid_from TEXT NOT NULL PRIMARY KEY
            CHECK (date(valid_from) IS valid_from AND valid_from >= '1900-01-01'),
        tenths_kg INTEGER NOT NULL CHECK (tenths_kg BETWEEN 150 AND 2000)
    ) STRICT""",
)

KEPT_V6 = (*KEPT_V5, *sorted(V6_TABLES))


def write_frozen_v6(path: Path) -> None:
    """A v6 database as the phase 5 app wrote it, with data in the tables that v7 touches."""
    write_frozen_v5(path)
    with closing(sqlite3.connect(path)) as raw:
        for statement in FROZEN_V6:
            raw.execute(statement)
        raw.execute(
            "INSERT INTO goal VALUES "
            "('g1', 'race', '2026-09-06', 'A', 'running', 21097, 6000, NULL)"
        )
        raw.execute("INSERT INTO session_choice VALUES ('running', 'threshold')")
        raw.execute("PRAGMA user_version = 6")
        raw.commit()


def test_t_dati_01_the_released_v7_migration_has_not_changed() -> None:
    released = tuple(squash(statement) for statement in sqlite_schema.MIGRATIONS[6])

    assert released == tuple(squash(statement) for statement in FROZEN_V7)
    assert sqlite_schema.SCHEMA_VERSION >= 7
    assert {"lean_mass"} == sqlite_schema.V7_TABLES


def test_the_closed_set_of_v7_is_the_domain_enum() -> None:
    assert {p.value for p in ThresholdProtocol} == sqlite_schema.V7_THRESHOLD_PROTOCOLS


def test_t_dati_01_a_frozen_v6_database_is_migrated_to_v7_with_data_intact_and_a_copy(
    tmp_path: Path,
) -> None:
    path = tmp_path / PROFILE_DATABASE_NAME
    write_frozen_v6(path)
    before = {table: rows(path, table) for table in KEPT_V6}

    opened = SqliteProfileStorage().open(tmp_path)
    try:
        assert opened.info.format_version == sqlite_schema.SCHEMA_VERSION
        assert opened.lean_masses.list_entries() == []
        (old,) = opened.threshold_tests.list_tests()
        assert old.protocol is ThresholdProtocol.THIRTY_MINUTES  # all the old ones were 30 minutes
        assert old.method_version == 1  # the version that computed it is not rewritten
    finally:
        opened.close()

    after = {table: rows(path, table) for table in KEPT_V6 if table != "threshold_test"}
    assert after == {table: before[table] for table in after}
    assert [row[:6] for row in rows(path, "threshold_test")] == before["threshold_test"]
    assert "lean_mass" in table_names(path)
    [copy] = sorted((tmp_path / "backup").glob("*"))
    assert copy.name == f"ponos-v6-v{sqlite_schema.SCHEMA_VERSION}.sqlite3"
    assert user_version(copy) == 6
    assert "lean_mass" not in table_names(copy)
    assert {table: rows(copy, table) for table in KEPT_V6} == before


def test_t_dati_01_a_v7_migration_that_fails_halfway_leaves_the_v6_database_as_it_was(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / PROFILE_DATABASE_NAME
    write_frozen_v6(path)
    tables_before = table_names(path)
    broken_v7 = (sqlite_schema.MIGRATIONS[6][0], "CREATE TABLE questa_no (")
    monkeypatch.setattr(sqlite_database, "MIGRATIONS", (*sqlite_schema.MIGRATIONS[:6], broken_v7))

    with pytest.raises((ProfileStorageError, sqlite3.Error)):
        SqliteProfileStorage().open(tmp_path)

    assert user_version(path) == 6
    assert table_names(path) == tables_before
    with closing(sqlite3.connect(path)) as raw:  # not even the column that had succeeded
        columns = [r[1] for r in raw.execute("PRAGMA table_info(threshold_test)")]
    assert "protocol" not in columns
    monkeypatch.undo()
    SqliteProfileStorage().open(tmp_path).close()  # the good round succeeds
    assert user_version(path) == sqlite_schema.SCHEMA_VERSION


@pytest.mark.parametrize("kill_at", range(1, len(sqlite_schema.MIGRATIONS[6]) + 1))
def test_t_dati_01_a_v6_database_killed_at_every_migration_statement_is_still_v6_then_migrates(
    tmp_path: Path, kill_at: int
) -> None:
    path = tmp_path / PROFILE_DATABASE_NAME
    write_frozen_v6(path)
    before = {table: rows(path, table) for table in KEPT_V6}

    assert run_worker_until_death(tmp_path, kill_at) == DIED

    assert user_version(path) == 6  # the transaction was not committed
    assert "lean_mass" not in table_names(path)
    assert {table: rows(path, table) for table in KEPT_V6} == before  # not even the new column
    SqliteProfileStorage().open(tmp_path).close()
    assert user_version(path) == sqlite_schema.SCHEMA_VERSION


def test_t_dati_01_a_v5_database_goes_through_v6_and_v7_in_one_round_with_one_copy(
    tmp_path: Path,
) -> None:
    path = tmp_path / PROFILE_DATABASE_NAME
    write_frozen_v5(path)

    SqliteProfileStorage().open(tmp_path).close()

    assert user_version(path) == sqlite_schema.SCHEMA_VERSION
    assert {"lean_mass"} | V6_TABLES <= table_names(path)
    [copy] = sorted((tmp_path / "backup").glob("*"))
    assert copy.name == f"ponos-v5-v{sqlite_schema.SCHEMA_VERSION}.sqlite3"


def test_t_dati_01_a_migrated_profile_opens_again_without_a_second_copy(tmp_path: Path) -> None:
    write_frozen_v6(tmp_path / PROFILE_DATABASE_NAME)
    SqliteProfileStorage().open(tmp_path).close()
    first = sorted((tmp_path / "backup").glob("*"))

    SqliteProfileStorage().open(tmp_path).close()

    assert sorted((tmp_path / "backup").glob("*")) == first


def test_a_v6_profile_keeps_what_is_saved_and_takes_the_new_data(tmp_path: Path) -> None:
    write_frozen_v6(tmp_path / PROFILE_DATABASE_NAME)
    entry = LeanMassEntry(date(2026, 3, 1), LeanMass(612))
    opened = SqliteProfileStorage().open(tmp_path)
    try:
        opened.lean_masses.put(entry)
    finally:
        opened.close()

    reopened = SqliteProfileStorage().open(tmp_path)
    try:
        assert list(reopened.lean_masses.list_entries()) == [entry]
        assert len(reopened.goals.list_goals()) == 1
    finally:
        reopened.close()


# --- The database refuses what the domain refuses ---------------------------------------------
def migrated(tmp_path: Path) -> sqlite3.Connection:
    write_frozen_v6(tmp_path / PROFILE_DATABASE_NAME)
    SqliteProfileStorage().open(tmp_path).close()
    return sqlite3.connect(tmp_path / PROFILE_DATABASE_NAME)


def lean_row(day: str = "2026-03-01", tenths: str = "600") -> str:
    return f"INSERT INTO lean_mass VALUES ('{day}', {tenths})"  # noqa: S608 - test data


BAD_LEAN_MASS = [
    lean_row(tenths="149"),
    lean_row(tenths="2001"),
    lean_row(tenths="NULL"),
    lean_row(tenths="'61.2'"),  # STRICT: text in an integer column
    lean_row(tenths="600.5"),
    lean_row(day="2026-02-30"),
    lean_row(day="1899-12-31"),
    lean_row(day="not a date"),
    lean_row(day="2026-3-1"),
]
GOOD_LEAN_MASS = [
    lean_row(tenths="150"),
    lean_row(tenths="2000"),
    lean_row(day="1900-01-01"),
    lean_row(day="2026-02-28"),
]


@pytest.mark.parametrize("statement", BAD_LEAN_MASS)
def test_the_database_refuses_a_lean_mass_outside_the_domain(
    tmp_path: Path, statement: str
) -> None:
    with closing(migrated(tmp_path)) as raw, pytest.raises(sqlite3.IntegrityError):
        raw.execute(statement)


@pytest.mark.parametrize("statement", GOOD_LEAN_MASS)
def test_the_database_accepts_a_lean_mass_on_the_limits(tmp_path: Path, statement: str) -> None:
    with closing(migrated(tmp_path)) as raw:
        raw.execute(statement)


def test_the_database_refuses_a_date_twice(tmp_path: Path) -> None:
    with closing(migrated(tmp_path)) as raw:
        raw.execute(lean_row())

        with pytest.raises(sqlite3.IntegrityError):
            raw.execute(lean_row(tenths="610"))


def v7_row(protocol: str) -> str:
    """A history row of v7 with the protocol column given as SQL text."""
    key = "'fit:255:2:1:2026-04-02T07:00:00+00:00'"
    return (
        f"INSERT INTO threshold_test VALUES ({key}, '2026-04-02', 1775113200000, "  # noqa: S608
        f"168, 300, 2, {protocol})"
    )


@pytest.mark.parametrize("protocol", ["thirty_minutes", "twenty_minutes"])
def test_the_database_accepts_the_two_protocols(tmp_path: Path, protocol: str) -> None:
    with closing(migrated(tmp_path)) as raw:
        raw.execute(v7_row(f"'{protocol}'"))


@pytest.mark.parametrize("protocol", ["", "fifteen_minutes", "THIRTY_MINUTES", "NULL"])
def test_the_database_refuses_another_protocol(tmp_path: Path, protocol: str) -> None:
    value = protocol if protocol == "NULL" else f"'{protocol}'"
    with closing(migrated(tmp_path)) as raw, pytest.raises(sqlite3.IntegrityError):
        raw.execute(v7_row(value))


def test_the_frozen_v5_statements_are_still_the_ones_that_created_the_table() -> None:
    # the test table of v5 has no protocol column: v7 adds it, it does not rewrite v5
    assert not any("protocol" in squash(statement) for statement in FROZEN_V5)
