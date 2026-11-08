# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""T-DATI-01 and hostile databases: frozen v1 schema, files that are not the profile database.

The defence line of T-DATI-01 is that an already released migration is never modified: here the
v1 schema is copied by hand, independent of the code, and the data written with it must stay
readable by every future version of the app.
"""

from __future__ import annotations

import sqlite3
import uuid
from contextlib import closing
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from ponos_coach.activities.domain import GarminActivityId, ListingKind, SyncStatus
from ponos_coach.load.domain import Sex
from ponos_coach.profiles.domain import PROFILE_DATABASE_NAME, ProfileId
from ponos_coach.profiles.ports import (
    ProfileCorruptError,
    ProfileFormatTooNewError,
    ProfileStorageError,
)
from ponos_coach.storage import sqlite_database, sqlite_schema
from ponos_coach.storage.sqlite_profile import SqliteProfileStorage
from tests.collaudo.support import (
    PROFILE_NAME,
    create_profile,
    open_registered,
    real_ports,
)
from tests.fixtures.generators.import_files import fit_run

# Schema v1 as it was released (PLAN.md 4.3). It does not change: to change the schema the next
# migration is added and a new database is frozen below.
FROZEN_V1 = (
    """CREATE TABLE meta (
        key TEXT NOT NULL PRIMARY KEY CHECK (length(key) BETWEEN 1 AND 64),
        value TEXT NOT NULL CHECK (length(value) <= 1024)
    ) STRICT""",
    """CREATE TABLE activities (
        id INTEGER PRIMARY KEY,
        identity_key TEXT NOT NULL UNIQUE CHECK (length(identity_key) BETWEEN 5 AND 128),
        file_format TEXT NOT NULL CHECK (file_format IN ('fit','gpx')),
        sport TEXT NOT NULL CHECK (sport IN
            ('running','trail','cycling','strength','hiking','walking','other')),
        start_ms INTEGER NOT NULL,
        elapsed_s REAL NOT NULL CHECK (elapsed_s > 0),
        timer_s REAL NOT NULL CHECK (timer_s > 0 AND timer_s <= elapsed_s),
        distance_m REAL CHECK (distance_m IS NULL OR distance_m >= 0),
        ascent_m REAL CHECK (ascent_m IS NULL OR ascent_m >= 0),
        start_lat REAL CHECK (start_lat IS NULL OR start_lat BETWEEN -90 AND 90),
        start_lon REAL CHECK (start_lon IS NULL OR start_lon BETWEEN -180 AND 180),
        track_polyline TEXT CHECK (track_polyline IS NULL
            OR length(track_polyline) BETWEEN 1 AND 24000),
        archive_path TEXT NOT NULL CHECK (length(archive_path) BETWEEN 1 AND 1024),
        archive_sha256 TEXT NOT NULL CHECK (length(archive_sha256) = 64),
        archive_size_bytes INTEGER NOT NULL CHECK (archive_size_bytes >= 0),
        origin TEXT NOT NULL CHECK (origin IN ('import','garmin')),
        imported_ms INTEGER NOT NULL,
        garmin_activity_id INTEGER UNIQUE
            CHECK (garmin_activity_id IS NULL OR garmin_activity_id > 0),
        CHECK ((start_lat IS NULL) = (start_lon IS NULL)),
        CHECK (origin <> 'garmin' OR garmin_activity_id IS NOT NULL),
        CHECK (substr(identity_key, 1, 4) = file_format || ':')
    ) STRICT""",
    "CREATE INDEX activities_by_start ON activities (start_ms, identity_key)",
    """CREATE TABLE physiology (
        valid_from TEXT NOT NULL PRIMARY KEY CHECK (length(valid_from) = 10),
        min_bpm INTEGER NOT NULL CHECK (min_bpm >= 20),
        max_bpm INTEGER NOT NULL CHECK (max_bpm <= 250),
        threshold_bpm INTEGER NOT NULL CHECK (threshold_bpm >= 40),
        sex TEXT NOT NULL CHECK (sex IN ('male','female')),
        CHECK (min_bpm < max_bpm),
        CHECK (threshold_bpm > min_bpm AND threshold_bpm <= max_bpm)
    ) STRICT""",
    """CREATE TABLE load_cache (
        activity_id INTEGER NOT NULL PRIMARY KEY REFERENCES activities (id) ON DELETE CASCADE,
        fingerprint TEXT NOT NULL CHECK (length(fingerprint) = 64),
        trimp REAL NOT NULL,
        hr_tss REAL NOT NULL,
        load REAL NOT NULL,
        used_samples INTEGER NOT NULL CHECK (used_samples >= 0),
        discarded_samples INTEGER NOT NULL CHECK (discarded_samples >= 0),
        excluded_seconds REAL NOT NULL CHECK (excluded_seconds >= 0),
        source TEXT NOT NULL CHECK (source IN ('heart_rate','estimate'))
    ) STRICT""",
)
# Migration v2 (Garmin synchronisation) as it was released. It does not change, like v1.
FROZEN_V2 = (
    """CREATE TABLE sync_journal (
        garmin_id INTEGER NOT NULL PRIMARY KEY CHECK (garmin_id > 0),
        start_ms INTEGER NOT NULL,
        duration_s REAL NOT NULL CHECK (duration_s > 0),
        sport_key TEXT NOT NULL CHECK (length(sport_key) BETWEEN 1 AND 64),
        has_heart_rate INTEGER CHECK (has_heart_rate IS NULL OR has_heart_rate IN (0, 1)),
        status TEXT NOT NULL CHECK (status IN
            ('pending','downloaded','matched','retryable','failed')),
        failed_attempts INTEGER NOT NULL CHECK (failed_attempts >= 0),
        failure TEXT CHECK (failure IS NULL OR failure IN
            ('transient_error','rate_limited','unsupported_format','corrupt_file',
             'file_too_large','not_an_activity','archive_invalid','remote_rejected',
             'attempts_exhausted')),
        updated_ms INTEGER NOT NULL,
        CHECK ((status IN ('retryable', 'failed')) = (failure IS NOT NULL)),
        CHECK (status <> 'pending' OR failed_attempts = 0),
        CHECK (status NOT IN ('retryable', 'failed') OR failed_attempts >= 1),
        CHECK (status <> 'retryable' OR failure IN ('transient_error','rate_limited')),
        CHECK (status <> 'failed' OR failure NOT IN ('transient_error','rate_limited'))
    ) STRICT""",
    """CREATE INDEX sync_journal_to_do ON sync_journal (start_ms DESC, garmin_id DESC)
        WHERE status IN ('pending', 'retryable')""",
    """CREATE TABLE sync_cursor (
        kind TEXT NOT NULL PRIMARY KEY CHECK (kind IN ('full','verify','incremental')),
        next_offset INTEGER NOT NULL CHECK (next_offset >= 0),
        declared_total INTEGER CHECK (declared_total IS NULL OR declared_total >= 0),
        completed INTEGER NOT NULL CHECK (completed IN (0, 1))
    ) STRICT""",
    """CREATE TABLE sync_state (
        id INTEGER NOT NULL PRIMARY KEY CHECK (id = 1),
        last_completed_ms INTEGER NOT NULL
    ) STRICT""",
)
START = datetime(2026, 3, 2, 7, 0, tzinfo=UTC)
START_MS = int(START.timestamp()) * 1000
ARCHIVE_PATH = "2026/" + "ab" * 32 + ".fit"
SHA = "ab" * 32
FINGERPRINT = "cd" * 32
PROFILE_ID = "00000000-0000-4000-8000-0000000000aa"


def squash(text: str) -> str:
    return "".join(text.split())


def write_frozen_v1(path: Path) -> None:
    """A v1 database as the first version of the app would have written it (without using `src`)."""
    with closing(sqlite3.connect(path)) as raw:
        for statement in FROZEN_V1:
            raw.execute(statement)
        raw.executemany(
            "INSERT INTO meta (key, value) VALUES (?, ?)",
            [
                ("profile_id", PROFILE_ID),
                ("name", PROFILE_NAME),
                ("timezone", "Europe/Rome"),
                ("futuro", "conservami"),
            ],
        )
        raw.execute(
            "INSERT INTO activities (identity_key, file_format, sport, start_ms, elapsed_s, "
            "timer_s, distance_m, ascent_m, archive_path, archive_sha256, archive_size_bytes, "
            "origin, imported_ms) VALUES (?, 'fit', 'running', ?, 3600.0, 3500.0, 10000.0, 120.0, "
            "?, ?, 1234, 'import', ?)",
            (f"fit:1:2:3:{START.isoformat()}", START_MS, ARCHIVE_PATH, SHA, START_MS + 1000),
        )
        raw.execute(
            "INSERT INTO physiology VALUES ('2026-01-01', 50, 190, 170, 'female')",
        )
        raw.execute(
            "INSERT INTO load_cache VALUES (1, ?, 80.0, 70.0, 75.0, 3500, 3, 12.5, 'estimate')",
            (FINGERPRINT,),
        )
        raw.execute("PRAGMA user_version = 1")
        raw.commit()


# Columns added by migrations after the ones these frozen tests describe (v8: the track box).
# The tests of those migrations read them with their own queries.
LATER_COLUMNS = frozenset({"track_south", "track_west", "track_north", "track_east"})


def rows(path: Path, table: str) -> list[tuple[object, ...]]:
    """The rows of a table with the columns the earlier migrations gave it (`SELECT *` before
    the later columns were added)."""
    with closing(sqlite3.connect(path)) as raw:
        columns = [
            r[1] for r in raw.execute(f"PRAGMA table_info({table})")
            if r[1] not in LATER_COLUMNS
        ]  # fmt: skip
        return raw.execute(f"SELECT {', '.join(columns)} FROM {table}").fetchall()  # noqa: S608


# --- T-DATI-01 -----------------------------------------------------------------------------------


def test_t_dati_01_the_released_v1_migration_has_not_changed() -> None:
    released = tuple(squash(statement) for statement in sqlite_schema.MIGRATIONS[0])

    assert released == tuple(squash(statement) for statement in FROZEN_V1)


def test_t_dati_01_the_released_v2_migration_has_not_changed() -> None:
    released = tuple(squash(statement) for statement in sqlite_schema.MIGRATIONS[1])

    assert released == tuple(squash(statement) for statement in FROZEN_V2)


def test_t_dati_01_a_frozen_v1_database_is_migrated_with_data_intact_and_a_copy(
    tmp_path: Path,
) -> None:
    path = tmp_path / PROFILE_DATABASE_NAME
    write_frozen_v1(path)
    tables = ("meta", "activities", "physiology", "load_cache")
    before = {table: rows(path, table) for table in tables}

    opened = SqliteProfileStorage().open(tmp_path)
    try:
        assert opened.info.format_version == sqlite_schema.SCHEMA_VERSION
        assert opened.sync_journal.counts()[SyncStatus.PENDING] == 0
        assert opened.sync_journal.load_cursor(ListingKind.FULL) is None
        (record,) = opened.activities.list_by_start()
        opened.activities.link_garmin_id(record.identity, GarminActivityId(42))
    finally:
        opened.close()

    after = {table: rows(path, table) for table in tables}
    assert after["meta"] == before["meta"]  # including 'future'
    assert after["physiology"] == before["physiology"]
    assert after["load_cache"] == []  # derived: v3 empties it, the loads are recalculated
    # Same columns as before, apart from the Garmin id (linked above) and the v4 calories (NULL).
    assert [row[:-2] for row in after["activities"]] == [row[:-1] for row in before["activities"]]
    assert {row[-1] for row in after["activities"]} == {None}
    [copy] = sorted((tmp_path / "backup").glob("*"))
    assert copy.name == f"ponos-v1-v{sqlite_schema.SCHEMA_VERSION}.sqlite3"
    assert {table: rows(copy, table) for table in tables} == before
    with closing(sqlite3.connect(copy)) as raw:
        assert raw.execute("PRAGMA user_version").fetchone() == (1,)
        names = {r[0] for r in raw.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert names == set(tables)


def test_t_dati_01_a_frozen_v1_database_gets_exactly_the_latest_tables(tmp_path: Path) -> None:
    path = tmp_path / PROFILE_DATABASE_NAME
    write_frozen_v1(path)

    SqliteProfileStorage().open(tmp_path).close()

    with closing(sqlite3.connect(path)) as raw:
        assert raw.execute("PRAGMA user_version").fetchone() == (sqlite_schema.SCHEMA_VERSION,)
        names = {r[0] for r in raw.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert names == {
        "meta",
        "activities",
        "physiology",
        "load_cache",
        "sync_journal",
        "sync_cursor",
        "sync_state",
        "profile_body",
        "weight",
        "sport_weights",
        "activity_level",
        "meal_times",
        "nutrition_goal",
        "threshold_test",
        "goal",
        "weekly_availability",
        "session_choice",
        "workout_backup",
        "plan_state",
        "plan_past_day",
        "lean_mass",
    }


def test_t_dati_01_a_frozen_v1_database_opened_twice_migrates_once(tmp_path: Path) -> None:
    path = tmp_path / PROFILE_DATABASE_NAME
    write_frozen_v1(path)

    SqliteProfileStorage().open(tmp_path).close()
    SqliteProfileStorage().open(tmp_path).close()

    assert len(list((tmp_path / "backup").glob("*"))) == 1


def test_t_dati_01_a_frozen_v1_database_that_cannot_be_copied_is_not_migrated(
    tmp_path: Path,
) -> None:
    path = tmp_path / PROFILE_DATABASE_NAME
    write_frozen_v1(path)
    (tmp_path / "backup").write_bytes(b"un file, non una cartella")

    with pytest.raises(ProfileStorageError):
        SqliteProfileStorage().open(tmp_path)

    with closing(sqlite3.connect(path)) as raw:
        assert raw.execute("PRAGMA user_version").fetchone() == (1,)


def test_t_dati_01_a_frozen_v1_database_is_read_by_the_current_code(tmp_path: Path) -> None:
    write_frozen_v1(tmp_path / PROFILE_DATABASE_NAME)

    opened = SqliteProfileStorage().open(tmp_path)
    try:
        assert str(opened.info.profile_id) == PROFILE_ID
        assert opened.info.name.value == PROFILE_NAME
        (record,) = opened.activities.list_by_start()
        assert record.start == START
        assert (record.elapsed_s, record.timer_s) == (3600.0, 3500.0)
        assert (record.summary.distance_m, record.summary.ascent_m) == (10000.0, 120.0)
        assert record.archived.sha256_hex == SHA
        assert str(record.archived.relative_path) == ARCHIVE_PATH
        (entry,) = opened.physiology.list_entries()
        assert entry.valid_from == date(2026, 1, 1)
        assert entry.physiology.sex is Sex.FEMALE
    finally:
        opened.close()


def test_t_dati_01_a_future_migration_keeps_every_v1_row_and_the_unknown_keys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / PROFILE_DATABASE_NAME
    write_frozen_v1(path)
    before = {table: rows(path, table) for table in ("meta", "activities", "physiology")}
    v2 = (
        "ALTER TABLE activities ADD COLUMN note TEXT",
        "CREATE TABLE tag (id INTEGER PRIMARY KEY, name TEXT NOT NULL) STRICT",
    )
    monkeypatch.setattr(sqlite_database, "MIGRATIONS", (*sqlite_schema.MIGRATIONS, v2))
    monkeypatch.setattr(sqlite_database, "SCHEMA_VERSION", sqlite_schema.SCHEMA_VERSION + 1)

    SqliteProfileStorage().open(tmp_path).close()

    after_meta = rows(path, "meta")
    assert after_meta == before["meta"]  # including 'future'
    assert [row[:-2] for row in rows(path, "activities")] == before["activities"]
    assert rows(path, "physiology") == before["physiology"]
    assert rows(path, "load_cache") == []  # derived: the v3 migration empties it
    with closing(sqlite3.connect(path)) as raw:
        assert raw.execute("PRAGMA user_version").fetchone() == (sqlite_schema.SCHEMA_VERSION + 1,)


def test_t_dati_01_migrating_twice_is_the_same_as_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / PROFILE_DATABASE_NAME
    write_frozen_v1(path)
    v2 = ("CREATE TABLE tag (id INTEGER PRIMARY KEY, name TEXT NOT NULL) STRICT",)
    monkeypatch.setattr(sqlite_database, "MIGRATIONS", (*sqlite_schema.MIGRATIONS, v2))
    monkeypatch.setattr(sqlite_database, "SCHEMA_VERSION", sqlite_schema.SCHEMA_VERSION + 1)

    SqliteProfileStorage().open(tmp_path).close()
    first = {table: rows(path, table) for table in ("meta", "activities", "physiology", "tag")}
    SqliteProfileStorage().open(tmp_path).close()

    assert {table: rows(path, table) for table in first} == first


def test_t_dati_01_a_database_from_a_newer_app_is_refused_and_left_alone(tmp_path: Path) -> None:
    path = tmp_path / PROFILE_DATABASE_NAME
    write_frozen_v1(path)
    with closing(sqlite3.connect(path)) as raw:
        raw.execute(f"PRAGMA user_version = {sqlite_schema.SCHEMA_VERSION + 5}")
    before = path.read_bytes()

    with pytest.raises(ProfileFormatTooNewError):
        SqliteProfileStorage().open(tmp_path)

    assert path.read_bytes() == before
    assert sorted(p.name for p in tmp_path.iterdir()) == [PROFILE_DATABASE_NAME]


# --- Database that is not a profile database -----------------------------------------------------


def foreign_database(path: Path, user_version: int) -> None:
    with closing(sqlite3.connect(path)) as raw:
        raw.execute("CREATE TABLE altro (a INTEGER)")
        raw.execute(f"PRAGMA user_version = {user_version}")
        raw.commit()


@pytest.mark.parametrize("user_version", [0, -1])
def test_a_sqlite_file_of_another_program_without_the_schema_is_corrupt_and_untouched(
    tmp_path: Path, user_version: int
) -> None:
    path = tmp_path / PROFILE_DATABASE_NAME
    foreign_database(path, user_version)
    before = path.read_bytes()

    with pytest.raises(ProfileCorruptError):
        SqliteProfileStorage().open(tmp_path)

    assert path.read_bytes() == before
    assert sorted(p.name for p in tmp_path.iterdir()) == [PROFILE_DATABASE_NAME]


def test_d_f1_01_a_foreign_sqlite_file_with_user_version_1_is_reported_as_corrupt(
    tmp_path: Path,
) -> None:
    path = tmp_path / PROFILE_DATABASE_NAME
    foreign_database(path, 1)

    with pytest.raises(ProfileCorruptError):
        SqliteProfileStorage().open(tmp_path)

    assert sorted(p.name for p in tmp_path.iterdir()) == [PROFILE_DATABASE_NAME]


@pytest.mark.parametrize(
    "garbage",
    [
        b"questo non e un database " * 400,
        b"SQLite format 3\x00" + b"\xff" * 5000,
        b"SQLite format 3\x00",
        b"\x00" * 8192,
        b"Atleta di prova\n" * 600,
    ],
    ids=["testo", "intestazione-e-rumore", "solo-intestazione", "zeri", "righe-di-testo"],
)
def test_a_file_that_is_not_sqlite_is_refused_and_never_overwritten_or_deleted(
    tmp_path: Path, garbage: bytes
) -> None:
    (tmp_path / PROFILE_DATABASE_NAME).write_bytes(garbage)

    with pytest.raises(ProfileCorruptError):
        SqliteProfileStorage().open(tmp_path)

    assert (tmp_path / PROFILE_DATABASE_NAME).read_bytes() == garbage
    assert sorted(p.name for p in tmp_path.iterdir()) == [PROFILE_DATABASE_NAME]


@pytest.mark.parametrize("keep", [0.5, 0.9])
def test_a_truncated_real_database_is_refused_and_untouched(tmp_path: Path, keep: float) -> None:
    profile = create_profile(tmp_path)
    folder = profile.folder
    for number in range(40):
        (tmp_path / f"c{number}.fit").write_bytes(fit_run(number))
    profile.import_files(sorted(tmp_path.glob("c*.fit")))
    profile.close()
    path = folder / PROFILE_DATABASE_NAME
    data = path.read_bytes()
    path.write_bytes(data[: int(len(data) * keep)])
    cut = path.read_bytes()

    with pytest.raises(ProfileCorruptError):
        SqliteProfileStorage().open(folder)

    assert path.read_bytes() == cut


def test_the_database_replaced_by_garbage_fails_the_open_cleanly_and_the_old_one_comes_back(
    tmp_path: Path,
) -> None:
    profile = create_profile(tmp_path)
    folder, ports = profile.folder, profile.ports
    (tmp_path / "a.fit").write_bytes(fit_run(1))
    profile.import_files([tmp_path / "a.fit"])
    profile_id = profile.session.info.profile_id
    profile.close()
    path = folder / PROFILE_DATABASE_NAME
    original = path.read_bytes()
    garbage = b"sostituito da un altro programma " * 200
    path.write_bytes(garbage)
    registry_before = [
        (e.profile_id, e.folder, e.last_used_utc) for e in ports.registry.list_entries()
    ]

    with pytest.raises(ProfileCorruptError):
        open_registered(ports, profile_id)

    assert path.read_bytes() == garbage  # the file is neither repaired nor deleted
    assert [
        (e.profile_id, e.folder, e.last_used_utc) for e in ports.registry.list_entries()
    ] == registry_before
    ports.folders.acquire_lock(folder).release()  # the lock taken to open has been released
    path.write_bytes(original)
    reopened = open_registered(real_ports(tmp_path / "config"), profile_id)
    try:
        assert len(reopened.records()) == 1
    finally:
        reopened.close()


def test_a_profile_id_that_is_not_a_uuid4_in_the_database_is_corrupt(tmp_path: Path) -> None:
    path = tmp_path / PROFILE_DATABASE_NAME
    write_frozen_v1(path)
    with closing(sqlite3.connect(path)) as raw:
        raw.execute("UPDATE meta SET value = ? WHERE key = 'profile_id'", (str(uuid.uuid1()),))
        raw.commit()

    with pytest.raises(ProfileCorruptError):
        SqliteProfileStorage().open(tmp_path)


def test_the_profile_id_class_rejects_what_the_database_may_hold() -> None:
    with pytest.raises(ValueError, match="id"):
        ProfileId.parse("'; DROP TABLE meta; --")
