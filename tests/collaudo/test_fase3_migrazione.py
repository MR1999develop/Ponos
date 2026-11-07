# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""T-DATI-01 for phase 3: from the frozen v2 database to v3 (personal data, weights, zones).

The v2 database below is written by hand, independent of the code (the two released migrations
are in `test_database_del_profilo.py`). It is shown that v3 does not change after release, that a
v2 profile migrates without losing anything (except the load cache, which is derived and gets
emptied), that it
stays valid without height and date of birth and that it completes them only once.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import date
from pathlib import Path

import pytest

from ponos_coach.activities.domain import Sport
from ponos_coach.load.domain import ActivityLoad, LoadFingerprint, LoadSource, ZoneSeconds
from ponos_coach.profiles.domain import (
    PROFILE_DATABASE_NAME,
    BirthDate,
    FixedProfileData,
    Height,
    Weight,
    WeightEntry,
)
from ponos_coach.profiles.ports import FixedDataAlreadySetError, ProfileStorageError
from ponos_coach.storage import sqlite_database, sqlite_schema
from ponos_coach.storage.sqlite_profile import SqliteProfileStorage
from ponos_coach.storage.sqlite_profile_data import SqliteFixedDataStore, SqliteWeightStore
from ponos_coach.storage.sqlite_stores import SqlitePhysiologyStore
from tests.collaudo.test_database_del_profilo import (
    FINGERPRINT,
    FROZEN_V2,
    START_MS,
    rows,
    squash,
    write_frozen_v1,
)

# Migration v3 as it was released. It does not change, like v1 and v2.
FROZEN_V3 = (
    """CREATE TABLE profile_body (
        id INTEGER NOT NULL PRIMARY KEY CHECK (id = 1),
        height_cm INTEGER NOT NULL CHECK (height_cm BETWEEN 100 AND 250),
        birth_date TEXT NOT NULL
            CHECK (date(birth_date) IS birth_date AND birth_date >= '1900-01-01')
    ) STRICT""",
    """CREATE TRIGGER profile_body_is_fixed BEFORE UPDATE ON profile_body
    BEGIN
        SELECT RAISE(ABORT, 'profile_body is fixed');
    END""",
    """CREATE TRIGGER profile_body_is_kept BEFORE DELETE ON profile_body
    BEGIN
        SELECT RAISE(ABORT, 'profile_body is fixed');
    END""",
    """CREATE TABLE weight (
        valid_from TEXT NOT NULL PRIMARY KEY
            CHECK (date(valid_from) IS valid_from AND valid_from >= '1900-01-01'),
        tenths_kg INTEGER NOT NULL CHECK (tenths_kg BETWEEN 200 AND 3000)
    ) STRICT""",
    """CREATE TABLE sport_weights (
        sport TEXT NOT NULL PRIMARY KEY CHECK (sport IN
            ('running','trail','cycling','strength','hiking','walking','other')),
        weight REAL NOT NULL CHECK (weight >= 0 AND weight <= 5.0)
    ) STRICT""",
    "DELETE FROM load_cache",
    "ALTER TABLE load_cache ADD COLUMN zone1_s REAL NOT NULL DEFAULT 0 CHECK (zone1_s >= 0)",
    "ALTER TABLE load_cache ADD COLUMN zone2_s REAL NOT NULL DEFAULT 0 CHECK (zone2_s >= 0)",
    "ALTER TABLE load_cache ADD COLUMN zone3_s REAL NOT NULL DEFAULT 0 CHECK (zone3_s >= 0)",
    "ALTER TABLE load_cache ADD COLUMN zone4_s REAL NOT NULL DEFAULT 0 CHECK (zone4_s >= 0)",
    "ALTER TABLE load_cache ADD COLUMN zone5_s REAL NOT NULL DEFAULT 0 CHECK (zone5_s >= 0)",
)

V2_TABLES = ("meta", "activities", "physiology", "load_cache", "sync_journal", "sync_cursor")


def write_frozen_v2(path: Path) -> None:
    """A v2 database as the phase 2 app wrote it (without using `src`)."""
    write_frozen_v1(path)
    with closing(sqlite3.connect(path)) as raw:
        for statement in FROZEN_V2:
            raw.execute(statement)
        raw.execute(
            "INSERT INTO sync_journal VALUES (1042, ?, 3600.0, 'running', 1, 'downloaded', 0, "
            "NULL, ?)",
            (START_MS, START_MS + 5),
        )
        raw.execute("INSERT INTO sync_cursor VALUES ('full', 40, 40, 1)")
        raw.execute("INSERT INTO sync_state VALUES (1, ?)", (START_MS + 9,))
        raw.execute("PRAGMA user_version = 2")
        raw.commit()


def user_version(path: Path) -> int:
    with closing(sqlite3.connect(path)) as raw:
        return int(raw.execute("PRAGMA user_version").fetchone()[0])


def table_names(path: Path) -> set[str]:
    with closing(sqlite3.connect(path)) as raw:
        return {r[0] for r in raw.execute("SELECT name FROM sqlite_master WHERE type='table'")}


FIXED = FixedProfileData(Height(172), BirthDate(date(1990, 5, 17)))


def test_t_dati_01_the_released_v3_migration_has_not_changed() -> None:
    released = tuple(squash(statement) for statement in sqlite_schema.MIGRATIONS[2])

    assert released == tuple(squash(statement) for statement in FROZEN_V3)


def test_t_dati_01_a_frozen_v2_database_is_migrated_to_v3_with_data_intact_and_a_copy(
    tmp_path: Path,
) -> None:
    path = tmp_path / PROFILE_DATABASE_NAME
    write_frozen_v2(path)
    kept = ("meta", "activities", "physiology", "sync_journal", "sync_cursor", "sync_state")
    before = {table: rows(path, table) for table in kept}

    opened = SqliteProfileStorage().open(tmp_path)
    try:
        assert opened.info.format_version == sqlite_schema.SCHEMA_VERSION
        (record,) = opened.activities.list_by_start()
        assert record.sport is Sport.RUNNING
    finally:
        opened.close()

    # The v4 calories column is the only difference in the activities (NULL for old rows).
    after = {table: rows(path, table) for table in kept}
    after["activities"] = [row[:-1] for row in after["activities"]]
    assert after == before  # including 'future'
    assert rows(path, "load_cache") == []  # derived: recalculated from the files
    [copy] = sorted((tmp_path / "backup").glob("*"))
    assert copy.name == f"ponos-v2-v{sqlite_schema.SCHEMA_VERSION}.sqlite3"
    assert user_version(copy) == 2
    assert {table: rows(copy, table) for table in kept} == before
    assert rows(copy, "load_cache") != []  # the copy still has the v2 load
    assert {"profile_body", "weight", "sport_weights"}.isdisjoint(table_names(copy))


def test_t_dati_01_a_v2_profile_stays_valid_without_the_personal_data(tmp_path: Path) -> None:
    write_frozen_v2(tmp_path / PROFILE_DATABASE_NAME)

    opened = SqliteProfileStorage().open(tmp_path)
    try:
        assert opened.fixed_data.get() is None
        assert opened.weights.list_entries() == []
        assert opened.sport_weights.load() == {}
        (entry,) = opened.physiology.list_entries()  # the previous parameters are there
        assert entry.valid_from == date(2026, 1, 1)
    finally:
        opened.close()


def test_t_dati_01_a_v2_profile_completes_height_and_birth_date_once(tmp_path: Path) -> None:
    write_frozen_v2(tmp_path / PROFILE_DATABASE_NAME)
    opened = SqliteProfileStorage().open(tmp_path)
    try:
        opened.fixed_data.set_once(FIXED)
        with pytest.raises(FixedDataAlreadySetError):
            opened.fixed_data.set_once(FixedProfileData(Height(180), FIXED.birth_date))
    finally:
        opened.close()

    reopened = SqliteProfileStorage().open(tmp_path)
    try:
        assert reopened.fixed_data.get() == FIXED  # the second write changed nothing
    finally:
        reopened.close()


def test_t_dati_01_a_migrated_profile_stores_loads_with_their_zones(tmp_path: Path) -> None:
    write_frozen_v2(tmp_path / PROFILE_DATABASE_NAME)
    load = ActivityLoad(
        80.0, 70.0, 75.0, 3500, 3, 12.5, LoadSource.HEART_RATE,
        ZoneSeconds((100.0, 200.0, 300.0, 400.0, 500.0)),
    )  # fmt: skip
    opened = SqliteProfileStorage().open(tmp_path)
    try:
        (record,) = opened.activities.list_by_start()
        opened.load_cache.put(record.identity, LoadFingerprint(FINGERPRINT), load)

        assert opened.load_cache.get(record.identity, LoadFingerprint(FINGERPRINT)) == load
    finally:
        opened.close()


def test_t_dati_01_a_v3_migration_that_fails_halfway_leaves_the_v2_database_as_it_was(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / PROFILE_DATABASE_NAME
    write_frozen_v2(path)
    tables_before = table_names(path)
    cache_before = rows(path, "load_cache")
    broken_v3 = (
        sqlite_schema.MIGRATIONS[2][0],  # a new table that succeeds...
        sqlite_schema.MIGRATIONS[2][4],  # ...the emptied cache...
        "CREATE TABLE questa_no (",  # ...then a syntax error
    )
    monkeypatch.setattr(
        sqlite_database,
        "MIGRATIONS",
        (*sqlite_schema.MIGRATIONS[:2], broken_v3, sqlite_schema.MIGRATIONS[3]),
    )

    with pytest.raises((ProfileStorageError, sqlite3.Error)):
        SqliteProfileStorage().open(tmp_path)

    assert user_version(path) == 2
    assert table_names(path) == tables_before  # no half table
    assert rows(path, "load_cache") == cache_before  # and the cache was not emptied
    monkeypatch.undo()
    SqliteProfileStorage().open(tmp_path).close()  # the good round succeeds
    assert user_version(path) == sqlite_schema.SCHEMA_VERSION


def test_the_database_refuses_to_change_the_fixed_data_even_with_a_direct_update(
    tmp_path: Path,
) -> None:
    storage = SqliteProfileStorage()
    write_frozen_v2(tmp_path / PROFILE_DATABASE_NAME)
    storage.open(tmp_path).close()
    with closing(sqlite3.connect(tmp_path / PROFILE_DATABASE_NAME)) as raw:
        raw.execute("INSERT INTO profile_body VALUES (1, 172, '1990-05-17')")

        with pytest.raises(sqlite3.IntegrityError, match="fixed"):
            raw.execute("UPDATE profile_body SET height_cm = 180")


def test_the_database_refuses_to_delete_the_fixed_data_even_with_a_direct_delete(
    tmp_path: Path,
) -> None:
    write_frozen_v2(tmp_path / PROFILE_DATABASE_NAME)
    SqliteProfileStorage().open(tmp_path).close()
    with closing(sqlite3.connect(tmp_path / PROFILE_DATABASE_NAME)) as raw:
        raw.execute("INSERT INTO profile_body VALUES (1, 172, '1990-05-17')")

        with pytest.raises(sqlite3.IntegrityError, match="fixed"):
            raw.execute("DELETE FROM profile_body")


@pytest.mark.parametrize(
    "statement",
    [
        "INSERT INTO profile_body VALUES (1, 99, '1990-05-17')",  # off point: 100 is the minimum
        "INSERT INTO profile_body VALUES (1, 251, '1990-05-17')",  # off point: 250 is the maximum
        "INSERT INTO profile_body VALUES (1, 172, '1899-12-31')",  # before 1900-01-01
        "INSERT INTO profile_body VALUES (2, 172, '1990-05-17')",  # a single row
        "INSERT INTO profile_body VALUES (1, 172, '1990-02-30')",  # non-existent day
        "INSERT INTO profile_body VALUES (1, 172, '1990-13-01')",  # non-existent month
        "INSERT INTO profile_body VALUES (1, 172, 'abcdefghij')",  # right length, not a date
        "INSERT INTO profile_body VALUES (1, 172, '1990-5-17-0')",  # right length, not ISO
        "INSERT INTO weight VALUES ('2026-02-30', 700)",  # non-existent day
        "INSERT INTO weight VALUES ('xxxxxxxxxx', 700)",  # right length, not a date
        "INSERT INTO weight VALUES ('1899-12-31', 700)",  # before 1900-01-01
        "INSERT INTO weight VALUES ('2026-01-01', 199)",  # below 20.0 kg
        "INSERT INTO weight VALUES ('2026-01-01', 3001)",  # above 300.0 kg
        "INSERT INTO sport_weights VALUES ('curling', 1.0)",  # unknown sport
        "INSERT INTO sport_weights VALUES ('running', 5.1)",  # weight above the maximum
        "INSERT INTO sport_weights VALUES ('running', -0.1)",  # negative weight
    ],
)
def test_the_database_refuses_personal_data_outside_the_domain_limits(
    tmp_path: Path, statement: str
) -> None:
    write_frozen_v2(tmp_path / PROFILE_DATABASE_NAME)
    SqliteProfileStorage().open(tmp_path).close()
    with (
        closing(sqlite3.connect(tmp_path / PROFILE_DATABASE_NAME)) as raw,
        pytest.raises(sqlite3.IntegrityError),
    ):
        raw.execute(statement)


@pytest.mark.parametrize(
    "statement",
    [
        "INSERT INTO profile_body VALUES (1, 100, '1900-01-01')",  # on point: minimi ammessi
        "INSERT INTO profile_body VALUES (1, 250, '2020-02-29')",  # on point: massimo
        "INSERT INTO weight VALUES ('1900-01-01', 700)",  # first allowed date
        "INSERT INTO weight VALUES ('2026-01-01', 200)",  # 20.0 kg
        "INSERT INTO weight VALUES ('2026-01-01', 3000)",  # 300.0 kg
        "INSERT INTO sport_weights VALUES ('running', 0.0)",
        "INSERT INTO sport_weights VALUES ('running', 5.0)",
    ],
)
def test_the_database_accepts_personal_data_on_the_limits(tmp_path: Path, statement: str) -> None:
    write_frozen_v2(tmp_path / PROFILE_DATABASE_NAME)
    SqliteProfileStorage().open(tmp_path).close()
    with closing(sqlite3.connect(tmp_path / PROFILE_DATABASE_NAME)) as raw:
        raw.execute(statement)


def test_weight_entry_round_trips_through_a_migrated_database(tmp_path: Path) -> None:
    write_frozen_v2(tmp_path / PROFILE_DATABASE_NAME)
    entry = WeightEntry(date(2026, 3, 1), Weight(705))
    opened = SqliteProfileStorage().open(tmp_path)
    try:
        opened.weights.put(entry)
    finally:
        opened.close()

    reopened = SqliteProfileStorage().open(tmp_path)
    try:
        assert reopened.weights.list_entries() == [entry]
    finally:
        reopened.close()


class _UncheckedDatabase:
    """A database without CHECK constraints (the real file already refuses to open with invalid
    dates)."""

    def __init__(self, statement: str) -> None:
        self.connection = sqlite3.connect(":memory:")
        self.connection.execute("CREATE TABLE profile_body (id, height_cm, birth_date)")
        self.connection.execute("CREATE TABLE weight (valid_from, tenths_kg)")
        self.connection.execute(
            "CREATE TABLE physiology (valid_from, min_bpm, max_bpm, threshold_bpm, sex)"
        )
        self.connection.execute(statement)


@pytest.mark.parametrize(
    ("statement", "read"),
    [
        ("INSERT INTO profile_body VALUES (1, 172, '1990-13-45-SEGRETO')", "fixed"),
        ("INSERT INTO weight VALUES ('2026-13-45-SEGRETO', 700)", "weight"),
        ("INSERT INTO physiology VALUES ('2026-13-45-SEGRETO', 50, 190, 170, 'male')", "heart"),
    ],
)
def test_a_stored_date_that_cannot_be_read_is_reported_without_the_text_that_was_read(
    statement: str, read: str
) -> None:
    db = _UncheckedDatabase(statement)
    readers = {
        "fixed": SqliteFixedDataStore(db).get,  # type: ignore[arg-type]
        "weight": SqliteWeightStore(db).list_entries,  # type: ignore[arg-type]
        "heart": SqlitePhysiologyStore(db).list_entries,  # type: ignore[arg-type]
    }

    try:
        with pytest.raises(Exception, match=r"invalid") as error:
            readers[read]()
    finally:
        db.connection.close()

    chain: BaseException | None = error.value
    while chain is not None:
        assert "SEGRETO" not in str(chain)
        chain = chain.__cause__ or (None if chain.__suppress_context__ else chain.__context__)
