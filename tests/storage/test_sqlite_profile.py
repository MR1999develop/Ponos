# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Profile database on SQLite: contracts, migrations (T-DATI-01), constraints, failures."""

from __future__ import annotations

import itertools
import sqlite3
from collections.abc import Iterator
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from ponos_coach.activities.domain import (
    RETRYABLE_REASONS,
    ActivityOrigin,
    ActivityRecord,
    FailureReason,
    FileFormat,
    ListingKind,
    Sport,
    SyncStatus,
    is_possible_duplicate,
)
from ponos_coach.activities.ports import (
    ActivityIndexError,
    StorageBusyError,
    SyncJournalError,
    TransactionStateError,
)
from ponos_coach.load.domain import LoadSource, Sex
from ponos_coach.profiles.domain import PROFILE_DATABASE_NAME, ProfileId, ProfileName
from ponos_coach.profiles.ports import (
    OpenProfile,
    ProfileCorruptError,
    ProfileFormatTooNewError,
    ProfileNotFoundError,
    ProfileStorage,
    ProfileStorageError,
)
from ponos_coach.storage import sqlite_database, sqlite_schema
from ponos_coach.storage.sqlite_database import create_database, open_database
from ponos_coach.storage.sqlite_profile import SqliteProfileStorage
from ponos_coach.storage.sqlite_schema import SCHEMA_VERSION
from tests.builders import T0, fit_identity, gpx_identity, make_record
from tests.contracts.contract_storage import (
    FINGERPRINT,
    LOAD,
    NAME,
    PROFILE_ID,
    ActivityIndexContract,
    FixedDataStoreContract,
    LoadCacheContract,
    LoadCacheZonesContract,
    PhysiologyStoreContract,
    ProfileStorageContract,
    SportWeightStoreContract,
    WeightStoreContract,
)
from tests.contracts.contract_sync import ActivityIndexGarminIdContract, SyncJournalContract
from tests.contracts.harness import StorageHarness


class _DiskStorageHarness:
    def __init__(self, root: Path) -> None:
        self._root = root
        self._storage = SqliteProfileStorage()
        self._numbers = itertools.count(1)

    @property
    def storage(self) -> ProfileStorage:
        return self._storage

    def new_folder(self) -> Path:
        folder = self._root / f"p{next(self._numbers)}"
        folder.mkdir()
        return folder


class _DiskStorageFixture:
    @pytest.fixture
    def harness(self, tmp_path: Path) -> StorageHarness:
        return _DiskStorageHarness(tmp_path)


class TestSqliteProfileStorage(_DiskStorageFixture, ProfileStorageContract):
    pass


class TestSqliteActivityIndex(_DiskStorageFixture, ActivityIndexContract):
    pass


class TestSqliteActivityIndexGarminIds(_DiskStorageFixture, ActivityIndexGarminIdContract):
    pass


class TestSqliteSyncJournal(_DiskStorageFixture, SyncJournalContract):
    pass


class TestSqlitePhysiologyStore(_DiskStorageFixture, PhysiologyStoreContract):
    pass


class TestSqliteLoadCache(_DiskStorageFixture, LoadCacheContract):
    pass


class TestSqliteLoadCacheZones(_DiskStorageFixture, LoadCacheZonesContract):
    pass


class TestSqliteWeightStore(_DiskStorageFixture, WeightStoreContract):
    pass


class TestSqliteFixedDataStore(_DiskStorageFixture, FixedDataStoreContract):
    pass


class TestSqliteSportWeightStore(_DiskStorageFixture, SportWeightStoreContract):
    pass


def _create(folder: Path) -> None:
    SqliteProfileStorage().create(folder, PROFILE_ID, NAME, "Europe/Rome").close()


def _db_path(folder: Path) -> Path:
    return folder / PROFILE_DATABASE_NAME


# --- Migrations (T-DATI-01) ----------------------------------------------------------------------


def test_new_database_has_the_latest_schema_and_the_expected_tables(tmp_path: Path) -> None:
    _create(tmp_path)

    with closing(sqlite3.connect(_db_path(tmp_path))) as raw:
        assert raw.execute("PRAGMA user_version").fetchone() == (SCHEMA_VERSION,)
        tables = {r[0] for r in raw.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        meta = dict(raw.execute("SELECT key, value FROM meta").fetchall())
    assert tables == {
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
    }
    assert meta == {
        "profile_id": str(PROFILE_ID),
        "name": "Atleta di prova",
        "timezone": "Europe/Rome",
    }


def test_closed_database_leaves_a_single_file_after_the_checkpoint(tmp_path: Path) -> None:
    _create(tmp_path)

    assert sorted(p.name for p in tmp_path.iterdir()) == [PROFILE_DATABASE_NAME]


def test_closed_sets_of_schema_v1_match_the_domain_enums() -> None:
    """A new value in an enum forces writing a schema migration."""
    assert {s.value for s in Sport} == sqlite_schema.V1_SPORTS
    assert {f.value for f in FileFormat} == sqlite_schema.V1_FORMATS
    assert {o.value for o in ActivityOrigin} == sqlite_schema.V1_ORIGINS
    assert {s.value for s in Sex} == sqlite_schema.V1_SEXES
    assert {s.value for s in LoadSource} == sqlite_schema.V1_LOAD_SOURCES


def test_closed_sets_of_schema_v2_match_the_domain_enums() -> None:
    """A new value in a sync enum forces writing a migration."""
    assert {s.value for s in SyncStatus} == sqlite_schema.V2_SYNC_STATUSES
    assert {f.value for f in FailureReason} == sqlite_schema.V2_FAILURES
    assert {f.value for f in RETRYABLE_REASONS} == sqlite_schema.V2_RETRYABLE_FAILURES
    assert {k.value for k in ListingKind} == sqlite_schema.V2_LISTING_KINDS


def test_empty_database_is_migrated_to_the_latest_version_in_one_transaction(
    tmp_path: Path,
) -> None:
    db = create_database(_db_path(tmp_path), {"profile_id": "x"})
    try:
        assert db.user_version() == SCHEMA_VERSION
    finally:
        db.close()


def test_older_database_is_migrated_keeping_data_and_unknown_meta_keys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = tmp_path
    profile = SqliteProfileStorage().create(folder, PROFILE_ID, NAME, "UTC")
    profile.activities.add(make_record())
    profile.close()
    with closing(sqlite3.connect(_db_path(folder))) as raw, raw:
        raw.execute("INSERT INTO meta (key, value) VALUES ('futuro', 'conservami')")
    v2 = ("CREATE TABLE note (id INTEGER PRIMARY KEY, text TEXT NOT NULL) STRICT",)
    monkeypatch.setattr(sqlite_database, "MIGRATIONS", (*sqlite_schema.MIGRATIONS, v2))
    monkeypatch.setattr(sqlite_database, "SCHEMA_VERSION", SCHEMA_VERSION + 1)

    with closing(SqliteProfileStorage().open(folder)) as migrated:
        assert migrated.info.format_version == SCHEMA_VERSION + 1
        assert migrated.activities.list_by_start() == [make_record()]
    with closing(sqlite3.connect(_db_path(folder))) as raw:
        assert raw.execute("SELECT value FROM meta WHERE key='futuro'").fetchone() == (
            "conservami",
        )
        assert raw.execute("SELECT count(*) FROM note").fetchone() == (0,)


def test_failed_migration_changes_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _create(tmp_path)
    broken = ("CREATE TABLE a (x INTEGER) STRICT", "CREATE TABLE a (y INTEGER) STRICT")
    monkeypatch.setattr(sqlite_database, "MIGRATIONS", (*sqlite_schema.MIGRATIONS, broken))
    monkeypatch.setattr(sqlite_database, "SCHEMA_VERSION", SCHEMA_VERSION + 1)

    with pytest.raises(ProfileStorageError):
        SqliteProfileStorage().open(tmp_path)

    with closing(sqlite3.connect(_db_path(tmp_path))) as raw:
        assert raw.execute("PRAGMA user_version").fetchone() == (SCHEMA_VERSION,)
        assert raw.execute("SELECT name FROM sqlite_master WHERE name='a'").fetchone() is None


def _backups(folder: Path) -> list[Path]:
    return sorted((folder / "backup").glob("*"))


def test_failed_migration_leaves_the_original_intact_and_a_coherent_copy_in_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    profile = SqliteProfileStorage().create(tmp_path, PROFILE_ID, NAME, "UTC")
    profile.activities.add(make_record())
    profile.close()
    before = _db_path(tmp_path).read_bytes()
    half_done = ("CREATE TABLE a (x INTEGER) STRICT", "NON E SQL")
    monkeypatch.setattr(sqlite_database, "MIGRATIONS", (*sqlite_schema.MIGRATIONS, half_done))
    monkeypatch.setattr(sqlite_database, "SCHEMA_VERSION", SCHEMA_VERSION + 1)

    with pytest.raises(ProfileStorageError):
        SqliteProfileStorage().open(tmp_path)

    assert _db_path(tmp_path).read_bytes() == before  # the previous database, byte for byte
    [copy] = _backups(tmp_path)
    assert copy.name == f"ponos-v{SCHEMA_VERSION}-v{SCHEMA_VERSION + 1}.sqlite3"
    with closing(sqlite3.connect(copy)) as raw:
        assert raw.execute("PRAGMA user_version").fetchone() == (SCHEMA_VERSION,)
        assert raw.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert raw.execute("SELECT count(*) FROM activities").fetchone() == (1,)


def test_a_successful_migration_also_leaves_the_copy_of_the_old_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _create(tmp_path)
    v2 = ("CREATE TABLE note (id INTEGER PRIMARY KEY, text TEXT NOT NULL) STRICT",)
    monkeypatch.setattr(sqlite_database, "MIGRATIONS", (*sqlite_schema.MIGRATIONS, v2))
    monkeypatch.setattr(sqlite_database, "SCHEMA_VERSION", SCHEMA_VERSION + 1)

    SqliteProfileStorage().open(tmp_path).close()

    [copy] = _backups(tmp_path)
    with closing(sqlite3.connect(copy)) as raw:
        assert raw.execute("PRAGMA user_version").fetchone() == (SCHEMA_VERSION,)
        assert raw.execute("SELECT name FROM sqlite_master WHERE name='note'").fetchone() is None


def test_opening_an_up_to_date_database_makes_no_backup(tmp_path: Path) -> None:
    _create(tmp_path)

    SqliteProfileStorage().open(tmp_path).close()

    assert not (tmp_path / "backup").exists()


def test_a_missing_backup_folder_blocks_the_migration_instead_of_skipping_the_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _create(tmp_path)
    (tmp_path / "backup").write_bytes(b"un file, non una cartella")
    v2 = ("CREATE TABLE note (id INTEGER PRIMARY KEY) STRICT",)
    monkeypatch.setattr(sqlite_database, "MIGRATIONS", (*sqlite_schema.MIGRATIONS, v2))
    monkeypatch.setattr(sqlite_database, "SCHEMA_VERSION", SCHEMA_VERSION + 1)

    with pytest.raises(ProfileStorageError):
        SqliteProfileStorage().open(tmp_path)

    with closing(sqlite3.connect(_db_path(tmp_path))) as raw:
        assert raw.execute("PRAGMA user_version").fetchone() == (SCHEMA_VERSION,)


def test_future_version_is_refused_and_the_file_is_untouched(tmp_path: Path) -> None:
    _create(tmp_path)
    with closing(sqlite3.connect(_db_path(tmp_path))) as raw:
        raw.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
    before = _db_path(tmp_path).read_bytes()

    with pytest.raises(ProfileFormatTooNewError):
        SqliteProfileStorage().open(tmp_path)

    assert _db_path(tmp_path).read_bytes() == before
    assert sorted(p.name for p in tmp_path.iterdir()) == [PROFILE_DATABASE_NAME]


# --- Invalid files -----------------------------------------------------------------------------


def test_file_that_is_not_sqlite_is_corrupt(tmp_path: Path) -> None:
    _db_path(tmp_path).write_bytes(b"questo non e un database " * 100)

    with pytest.raises(ProfileCorruptError):
        SqliteProfileStorage().open(tmp_path)


def test_empty_file_is_corrupt_not_a_profile(tmp_path: Path) -> None:
    _db_path(tmp_path).write_bytes(b"")

    with pytest.raises(ProfileCorruptError):
        SqliteProfileStorage().open(tmp_path)


def test_damaged_pages_fail_the_integrity_check(tmp_path: Path) -> None:
    profile = SqliteProfileStorage().create(tmp_path, PROFILE_ID, NAME, "UTC")
    for number in range(50):
        profile.activities.add(make_record(fit_identity(number)))
    profile.close()
    path = _db_path(tmp_path)
    data = bytearray(path.read_bytes())
    data[4096:8192] = b"\xff" * 4096
    path.write_bytes(bytes(data))

    with pytest.raises(ProfileCorruptError):
        SqliteProfileStorage().open(tmp_path)


def test_missing_profile_information_is_corrupt(tmp_path: Path) -> None:
    _create(tmp_path)
    with closing(sqlite3.connect(_db_path(tmp_path))) as raw, raw:
        raw.execute("DELETE FROM meta WHERE key = 'timezone'")

    with pytest.raises(ProfileCorruptError):
        SqliteProfileStorage().open(tmp_path)


def test_a_directory_in_place_of_the_database_is_corrupt(tmp_path: Path) -> None:
    _db_path(tmp_path).mkdir()

    with pytest.raises(ProfileCorruptError):
        SqliteProfileStorage().open(tmp_path)


def test_open_in_a_folder_without_database_creates_nothing(tmp_path: Path) -> None:
    with pytest.raises(ProfileNotFoundError):
        SqliteProfileStorage().open(tmp_path)

    assert list(tmp_path.iterdir()) == []


def test_unknown_timezone_creates_nothing(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="time zone"):
        SqliteProfileStorage().create(tmp_path, PROFILE_ID, NAME, "Mars/Olympus")

    assert list(tmp_path.iterdir()) == []


def test_failed_creation_removes_the_file_it_made(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    broken = ("CREATE TABLE a (x INTEGER) STRICT", "NON E SQL")
    monkeypatch.setattr(sqlite_database, "MIGRATIONS", (broken,))

    with pytest.raises(ProfileStorageError):
        SqliteProfileStorage().create(tmp_path, PROFILE_ID, NAME, "UTC")

    assert list(tmp_path.iterdir()) == []


def test_name_with_quotes_and_unicode_round_trips(tmp_path: Path) -> None:
    name = ProfileName.parse("Atleta di prova' \"; DROP TABLE meta; -- è 日本")
    SqliteProfileStorage().create(tmp_path, PROFILE_ID, name, "UTC").close()

    with closing(SqliteProfileStorage().open(tmp_path)) as profile:
        assert profile.info.name == name


# --- Connection and constraints -------------------------------------------------------------------


def _pragmas(db: sqlite_database.SqliteDatabase) -> dict[str, object]:
    return {
        name: db.connection.execute(f"PRAGMA {name}").fetchone()[0]
        for name in ("foreign_keys", "journal_mode", "synchronous", "busy_timeout")
    }


def test_every_connection_has_the_durability_and_integrity_settings(tmp_path: Path) -> None:
    expected = {
        "foreign_keys": 1,
        "journal_mode": "wal",
        "synchronous": 2,  # FULL
        "busy_timeout": sqlite_database.BUSY_TIMEOUT_MS,
    }
    created = create_database(_db_path(tmp_path), {})
    try:
        assert _pragmas(created) == expected
    finally:
        created.close()

    reopened = open_database(_db_path(tmp_path))
    try:
        assert _pragmas(reopened) == expected
    finally:
        reopened.close()


@pytest.fixture
def raw_db(tmp_path: Path) -> Iterator[sqlite_database.SqliteDatabase]:
    db = create_database(_db_path(tmp_path), {})
    yield db
    db.close()


_VALID_ACTIVITY = (
    "fit:1:1:1:2026-03-02T07:00:00+00:00", "fit", "running", 0, 60.0, 60.0, None, None, None, None,
    None, "2026/x.fit", "a" * 64, 10, "import", 0, None,
)  # fmt: skip
_COLUMNS = (
    "identity_key, file_format, sport, start_ms, elapsed_s, timer_s, distance_m, ascent_m, "
    "start_lat, start_lon, track_polyline, archive_path, archive_sha256, archive_size_bytes, "
    "origin, imported_ms, garmin_activity_id"
)
_INSERT = f"INSERT INTO activities ({_COLUMNS}) VALUES ({', '.join('?' * 17)})"  # noqa: S608


def _with(**changes: object) -> tuple[object, ...]:
    names = [c.strip() for c in _COLUMNS.split(",")]
    values = dict(zip(names, _VALID_ACTIVITY, strict=True)) | changes
    return tuple(values[n] for n in names)


def test_the_valid_baseline_row_is_accepted(raw_db: sqlite_database.SqliteDatabase) -> None:
    raw_db.connection.execute(_INSERT, _with())


@pytest.mark.parametrize(
    "changes",
    [
        {"file_format": "mp3"},
        {"sport": "curling"},
        {"origin": "altro"},
        {"elapsed_s": 0.0},
        {"timer_s": 61.0},
        {"distance_m": -1.0},
        {"start_lat": 91.0, "start_lon": 0.0},
        {"start_lat": 10.0},  # a single coordinate
        {"archive_sha256": "abc"},
        {"archive_size_bytes": -1},
        {"origin": "garmin"},  # no Garmin id
        {"garmin_activity_id": 0},
        {"identity_key": "gpx:" + "a" * 64},  # prefix different from the format
        {"track_polyline": ""},
    ],
    ids=lambda c: next(iter(c)),
)
def test_database_refuses_rows_that_break_the_domain_invariants(
    raw_db: sqlite_database.SqliteDatabase, changes: dict[str, object]
) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        raw_db.connection.execute(_INSERT, _with(**changes))


_JOURNAL_INSERT = (
    "INSERT INTO sync_journal (garmin_id, start_ms, duration_s, sport_key, has_heart_rate, "
    "status, failed_attempts, failure, updated_ms) VALUES (?, 0, 60.0, 'running', 1, ?, ?, ?, 0)"
)


def test_database_accepts_the_coherent_journal_rows(
    raw_db: sqlite_database.SqliteDatabase,
) -> None:
    for number, row in enumerate(
        [
            ("pending", 0, None),
            ("downloaded", 0, None),
            ("downloaded", 2, None),
            ("matched", 1, None),
            ("retryable", 1, "rate_limited"),
            ("failed", 3, "attempts_exhausted"),
            ("failed", 1, "corrupt_file"),
        ],
        start=1,
    ):
        raw_db.connection.execute(_JOURNAL_INSERT, (number, *row))


@pytest.mark.parametrize(
    "row",
    [
        ("sconosciuto", 0, None),  # state outside the closed set
        ("pending", 1, None),  # to download but with failed attempts
        ("pending", 0, "corrupt_file"),  # cause without failure
        ("downloaded", 0, "corrupt_file"),
        ("retryable", 1, None),  # failure without cause
        ("retryable", 0, "rate_limited"),  # no failed attempt
        ("retryable", 1, "corrupt_file"),  # retryable with definitive cause
        ("failed", 1, "rate_limited"),  # definitive with retryable cause
        ("failed", 0, "corrupt_file"),
        ("failed", 1, "causa inventata"),
        ("failed", -1, "corrupt_file"),
    ],
    ids=lambda r: f"{r[0]}-{r[1]}-{r[2]}",
)
def test_database_refuses_incoherent_journal_rows(
    raw_db: sqlite_database.SqliteDatabase, row: tuple[str, int, str | None]
) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        raw_db.connection.execute(_JOURNAL_INSERT, (1, *row))


def test_database_refuses_bad_journal_keys_and_cursors(
    raw_db: sqlite_database.SqliteDatabase,
) -> None:
    for bad_id in (0, -5):
        with pytest.raises(sqlite3.IntegrityError):
            raw_db.connection.execute(_JOURNAL_INSERT, (bad_id, "pending", 0, None))
    raw_db.connection.execute(_JOURNAL_INSERT, (1, "pending", 0, None))
    with pytest.raises(sqlite3.IntegrityError):  # same Garmin id: primary key
        raw_db.connection.execute(_JOURNAL_INSERT, (1, "pending", 0, None))
    cursor = "INSERT INTO sync_cursor VALUES (?, ?, ?, ?)"
    for bad in (
        ("altro", 0, None, 0),
        ("full", -1, None, 0),
        ("full", 0, -1, 0),
        ("full", 0, None, 2),
    ):
        with pytest.raises(sqlite3.IntegrityError):
            raw_db.connection.execute(cursor, bad)
    with pytest.raises(sqlite3.IntegrityError):  # a single state row
        raw_db.connection.execute("INSERT INTO sync_state VALUES (2, 0)")


def test_database_refuses_inconsistent_physiology(raw_db: sqlite_database.SqliteDatabase) -> None:
    insert = "INSERT INTO physiology VALUES (?, ?, ?, ?, ?)"
    raw_db.connection.execute(insert, ("2026-01-01", 50, 190, 170, "male"))
    for bad in (
        ("2026-01-02", 190, 190, 170, "male"),  # minimum not below maximum
        ("2026-01-03", 50, 190, 50, "male"),  # threshold not above minimum
        ("2026-01-04", 50, 190, 191, "male"),  # threshold above maximum
        ("2026-01-05", 50, 190, 170, "other"),
        ("2026-01-01", 50, 190, 170, "male"),  # same date: primary key
    ):
        with pytest.raises(sqlite3.IntegrityError):
            raw_db.connection.execute(insert, bad)


def test_foreign_key_refuses_a_load_without_its_activity(
    raw_db: sqlite_database.SqliteDatabase,
) -> None:
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        raw_db.connection.execute(
            "INSERT INTO load_cache (activity_id, fingerprint, trimp, hr_tss, load, used_samples, "
            "discarded_samples, excluded_seconds, source) "
            "VALUES (999, ?, 1, 1, 1, 0, 0, 0, 'estimate')",
            ("b" * 64,),
        )


def test_deleting_an_activity_deletes_its_cached_load(
    tmp_path: Path,
) -> None:
    profile = SqliteProfileStorage().create(tmp_path, PROFILE_ID, NAME, "UTC")
    profile.activities.add(make_record())
    profile.load_cache.put(fit_identity(), FINGERPRINT, LOAD)
    profile.close()

    db = open_database(_db_path(tmp_path))
    try:
        db.connection.execute("DELETE FROM activities")
        assert db.connection.execute("SELECT count(*) FROM load_cache").fetchone() == (0,)
    finally:
        db.close()


def test_sql_in_an_identity_or_a_path_is_data_not_code(tmp_path: Path) -> None:
    profile = SqliteProfileStorage().create(tmp_path, PROFILE_ID, NAME, "UTC")
    try:
        record = make_record(gpx_identity())
        profile.activities.add(record)
        assert profile.activities.get(record.identity) == record
    finally:
        profile.close()


# --- Transactions, closing, reopening ------------------------------------------------------------


def test_cancelled_transaction_leaves_the_database_as_it_was_and_the_next_one_works(
    tmp_path: Path,
) -> None:
    profile = SqliteProfileStorage().create(tmp_path, PROFILE_ID, NAME, "UTC")
    try:
        with pytest.raises(RuntimeError, match="cancelled"), profile.activities.transaction():
            profile.activities.add(make_record(fit_identity(1)))
            raise RuntimeError("cancelled")
        with profile.activities.transaction():
            profile.activities.add(make_record(fit_identity(2)))

        assert [r.identity for r in profile.activities.list_by_start()] == [fit_identity(2)]
    finally:
        profile.close()


def test_failed_transaction_does_not_block_a_later_nesting_check(tmp_path: Path) -> None:
    profile = SqliteProfileStorage().create(tmp_path, PROFILE_ID, NAME, "UTC")
    try:
        with (
            profile.activities.transaction(),
            pytest.raises(TransactionStateError),
            profile.activities.transaction(),
        ):
            pass
        with profile.activities.transaction():
            pass
    finally:
        profile.close()


def test_committed_data_survives_a_connection_closed_and_reopened_without_a_checkpoint(
    tmp_path: Path,
) -> None:
    """The commit is in the WAL: a second connection sees it even before closing."""
    profile = SqliteProfileStorage().create(tmp_path, PROFILE_ID, NAME, "UTC")
    try:
        profile.activities.add(make_record())
        with closing(sqlite3.connect(_db_path(tmp_path))) as other:
            assert other.execute("SELECT count(*) FROM activities").fetchone() == (1,)
    finally:
        profile.close()


def test_using_the_stores_after_close_fails_with_the_port_error(tmp_path: Path) -> None:
    profile = SqliteProfileStorage().create(tmp_path, PROFILE_ID, NAME, "UTC")
    profile.close()

    with pytest.raises(ActivityIndexError):
        profile.activities.list_by_start()


def test_instants_below_the_millisecond_are_refused_not_rounded(tmp_path: Path) -> None:
    profile = SqliteProfileStorage().create(tmp_path, PROFILE_ID, NAME, "UTC")
    try:
        with pytest.raises(ValueError, match="millisecond"):
            profile.activities.add(make_record(start=T0 + timedelta(microseconds=1)))
        assert profile.activities.list_by_start() == []
    finally:
        profile.close()


def test_profile_id_is_stored_in_canonical_form(tmp_path: Path) -> None:
    other = ProfileId.parse("22222222-2222-4222-8222-222222222222")
    SqliteProfileStorage().create(tmp_path, other, NAME, "UTC").close()

    with closing(SqliteProfileStorage().open(tmp_path)) as profile:
        assert profile.info.profile_id == other


# --- Possible duplicates: wide SQL, then the domain rule ----------------------------------


@pytest.fixture(scope="module")
def shared_profile(tmp_path_factory: pytest.TempPathFactory) -> Iterator[OpenProfile]:
    folder = tmp_path_factory.mktemp("dup")
    profile = SqliteProfileStorage().create(folder, PROFILE_ID, NAME, "UTC")
    yield profile
    profile.close()


class _Rollback(Exception):  # noqa: N818 - internal signal of the test
    pass


@settings(max_examples=60)
@given(
    stored=st.lists(
        st.tuples(
            st.integers(min_value=-200_000, max_value=200_000),
            st.integers(min_value=1, max_value=4_000_000),
        ),
        max_size=6,
    ),
    query_offset_ms=st.integers(min_value=-200_000, max_value=200_000),
    query_elapsed_ms=st.integers(min_value=1, max_value=4_000_000),
)
def test_possible_duplicates_match_the_naive_domain_rule(
    shared_profile: OpenProfile,
    stored: list[tuple[int, int]],
    query_offset_ms: int,
    query_elapsed_ms: int,
) -> None:
    records: list[ActivityRecord] = [
        make_record(
            gpx_identity(number),
            start=T0 + timedelta(milliseconds=offset),
            elapsed_s=elapsed_ms / 1000,
        )
        for number, (offset, elapsed_ms) in enumerate(stored)
    ]
    query_start = T0 + timedelta(milliseconds=query_offset_ms)
    query_elapsed = query_elapsed_ms / 1000

    with pytest.raises(_Rollback), shared_profile.activities.transaction():
        for record in records:
            shared_profile.activities.add(record)
        found = shared_profile.activities.find_possible_duplicates(query_start, query_elapsed)
        expected = [
            r
            for r in shared_profile.activities.list_by_start()
            if is_possible_duplicate(query_start, query_elapsed, r.start, r.elapsed_s)
        ]
        assert found == expected
        raise _Rollback


def test_possible_duplicates_window_is_not_narrower_than_the_rule(tmp_path: Path) -> None:
    """Sub-millisecond in the query: the wide prefilter does not lose the boundary case."""
    profile = SqliteProfileStorage().create(tmp_path, PROFILE_ID, NAME, "UTC")
    try:
        record = make_record(gpx_identity(), start=T0, elapsed_s=1000.0)
        profile.activities.add(record)
        edge = T0 + timedelta(seconds=60) + timedelta(microseconds=500)

        assert is_possible_duplicate(edge, 1000.0, record.start, record.elapsed_s) is False
        assert profile.activities.find_possible_duplicates(edge, 1000.0) == []
        assert profile.activities.find_possible_duplicates(
            datetime(2026, 3, 2, 7, 1, tzinfo=UTC), 1000.0
        ) == [record]
    finally:
        profile.close()


# --- Read-only reading (peek) --------------------------------------------------------------


def test_peek_of_a_closed_database_writes_nothing_and_creates_no_side_files(
    tmp_path: Path,
) -> None:
    profile = SqliteProfileStorage().create(tmp_path, PROFILE_ID, NAME, "UTC")
    profile.activities.add(make_record())
    profile.close()
    before = _db_path(tmp_path).read_bytes()

    peeked = SqliteProfileStorage().peek(tmp_path)

    assert peeked.activity_count == 1
    assert _db_path(tmp_path).read_bytes() == before
    assert sorted(p.name for p in tmp_path.iterdir()) == [PROFILE_DATABASE_NAME]


def test_peek_of_an_older_database_reads_it_without_migrating(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _create(tmp_path)
    v2 = ("CREATE TABLE note (id INTEGER PRIMARY KEY) STRICT",)
    monkeypatch.setattr(sqlite_database, "MIGRATIONS", (*sqlite_schema.MIGRATIONS, v2))
    monkeypatch.setattr(sqlite_database, "SCHEMA_VERSION", SCHEMA_VERSION + 1)

    peeked = SqliteProfileStorage().peek(tmp_path)

    assert peeked.info.format_version == SCHEMA_VERSION
    assert not (tmp_path / "backup").exists()
    with closing(sqlite3.connect(_db_path(tmp_path))) as raw:
        assert raw.execute("PRAGMA user_version").fetchone() == (SCHEMA_VERSION,)


def test_peek_refuses_a_future_version_a_foreign_file_and_garbage(tmp_path: Path) -> None:
    folder = tmp_path / "futuro"
    folder.mkdir()
    _create(folder)
    with closing(sqlite3.connect(_db_path(folder))) as raw:
        raw.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
    with pytest.raises(ProfileFormatTooNewError):
        SqliteProfileStorage().peek(folder)

    foreign = tmp_path / "estraneo"
    foreign.mkdir()
    with closing(sqlite3.connect(_db_path(foreign))) as raw:
        raw.execute("CREATE TABLE altro (a INTEGER)")
        raw.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    with pytest.raises(ProfileCorruptError):
        SqliteProfileStorage().peek(foreign)

    garbage = tmp_path / "rumore"
    garbage.mkdir()
    _db_path(garbage).write_bytes(b"questo non e un database " * 100)
    with pytest.raises(ProfileCorruptError):
        SqliteProfileStorage().peek(garbage)


def test_a_foreign_sqlite_file_with_a_meta_table_but_no_activity_tables_is_corrupt(
    tmp_path: Path,
) -> None:
    with closing(sqlite3.connect(_db_path(tmp_path))) as raw:
        raw.execute("CREATE TABLE meta (key TEXT, value TEXT)")
        raw.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    with pytest.raises(ProfileCorruptError):
        SqliteProfileStorage().open(tmp_path)


# --- R2: busy database when opening the transaction -------------------------------------


def test_r2_a_database_held_by_another_connection_gives_a_port_error_not_a_raw_one(
    tmp_path: Path,
) -> None:
    create_database(_db_path(tmp_path), {}).close()
    holder = open_database(_db_path(tmp_path))
    waiter = open_database(_db_path(tmp_path))
    try:
        waiter.connection.execute("PRAGMA busy_timeout = 20")  # the test does not wait 5 s
        with holder.transaction():
            with pytest.raises(StorageBusyError) as caught, waiter.transaction():
                pytest.fail("the transaction must not start")
            assert isinstance(caught.value, ActivityIndexError)
            assert isinstance(caught.value, SyncJournalError)
            assert isinstance(caught.value.__cause__, sqlite3.OperationalError)
        with waiter.transaction():  # free: now it opens, and the connection was not left crooked
            waiter.connection.execute("SELECT 1")
    finally:
        holder.close()
        waiter.close()


def _spy_on_integrity_checks(monkeypatch: pytest.MonkeyPatch, calls: list[str]) -> None:
    real = sqlite_database._check_integrity

    def spy(connection: sqlite3.Connection) -> None:
        calls.append("x")
        real(connection)

    monkeypatch.setattr(sqlite_database, "_check_integrity", spy)


# --- R15: the second connection may skip the integrity checks -----------------------------


def test_r15_an_extra_connection_can_skip_the_integrity_checks_but_a_normal_open_runs_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    create_database(_db_path(tmp_path), {}).close()
    calls: list[str] = []
    _spy_on_integrity_checks(monkeypatch, calls)

    open_database(_db_path(tmp_path), check_integrity=False).close()
    assert calls == []
    open_database(_db_path(tmp_path)).close()
    assert calls == ["x"]


def test_r15_an_older_database_is_checked_even_when_the_checks_are_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    create_database(_db_path(tmp_path), {}).close()
    newer = ("CREATE TABLE note (id INTEGER PRIMARY KEY) STRICT",)
    monkeypatch.setattr(sqlite_database, "MIGRATIONS", (*sqlite_schema.MIGRATIONS, newer))
    monkeypatch.setattr(sqlite_database, "SCHEMA_VERSION", SCHEMA_VERSION + 1)
    calls: list[str] = []
    _spy_on_integrity_checks(monkeypatch, calls)

    migrating = open_database(_db_path(tmp_path), check_integrity=False)

    assert calls == ["x"]
    migrating.close()
