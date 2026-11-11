# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""T-DATI-01 for phase 8: from the v10 database to v11, which works with FIT files only.

The v10 profile below has FIT and GPX activities (one that came from Garmin, one matched to a
journal entry), threshold tests, their files in the archive and the load cache. Opening it must
give a profile with FIT activities only: first a safety copy of the database, then the migration
in a single transaction, then the removal of the GPX files from the archive; the number of
activities removed reaches whoever opens the profile, once.
"""

from __future__ import annotations

import sqlite3
import subprocess
import sys
import uuid
from contextlib import closing
from pathlib import Path

import pytest

from ponos_coach.activities.domain import FitIdentity
from ponos_coach.profiles.domain import PROFILE_DATABASE_NAME, ProfileId, ProfileName
from ponos_coach.profiles.ports import OpenProfile, ProfileStorageError
from ponos_coach.storage import sqlite_database, sqlite_schema
from ponos_coach.storage.sqlite_profile import SqliteProfileStorage
from tests.collaudo.test_database_del_profilo import FINGERPRINT, SHA, START_MS, rows
from tests.collaudo.test_fase3_migrazione import user_version
from tests.fixtures.generators.collaudo_fase3_worker import DIED

WORKER = (
    Path(__file__).resolve().parents[1] / "fixtures" / "generators" / "collaudo_fase3_worker.py"
)
FIT_PATH_2 = "2026/" + "cd" * 32 + ".fit"
GPX_IMPORTED_PATH = "2026/" + "11" * 32 + ".gpx"
GPX_GARMIN_PATH = "2026/" + "22" * 32 + ".gpx"
GPX_MATCHED_PATH = "2026/" + "33" * 32 + ".gpx"
GPX_PATHS = (GPX_IMPORTED_PATH, GPX_GARMIN_PATH, GPX_MATCHED_PATH)
GARMIN_ID_DOWNLOADED = 1042
GARMIN_ID_MATCHED = 2077
GARMIN_ID_OF_A_FIT = 3099
V10 = 10  # the schema version of the profiles this migration reads
V11_STATEMENTS = len(sqlite_schema.MIGRATIONS[V10])
FIT_KEY_1 = "fit:1:2:3:2026-03-02T07:00:00+00:00"
FIT_KEY_2 = "fit:1:2:4:2026-03-02T07:00:00+00:00"
FIT_KEY_3 = "fit:1:2:5:2026-03-02T07:00:00+00:00"
ARCHIVE_PATH = "2026/" + SHA + ".fit"


def insert_activity(
    raw: sqlite3.Connection,
    identity_key: str,
    file_format: str,
    path: str,
    *,
    origin: str = "import",
    garmin_id: int | None = None,
    calories: float | None = None,
    track_box: tuple[float, float, float, float] | None = None,
) -> None:
    south, west, north, east = track_box or (None, None, None, None)
    raw.execute(
        "INSERT INTO activities (identity_key, file_format, sport, start_ms, elapsed_s, timer_s, "
        "archive_path, archive_sha256, archive_size_bytes, origin, imported_ms, "
        "garmin_activity_id, calories_kcal, track_south, track_west, track_north, track_east) "
        "VALUES (?, ?, 'running', ?, 3600.0, 3500.0, ?, ?, 10, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            identity_key, file_format, START_MS, path, SHA, origin, START_MS + 1000, garmin_id,
            calories, south, west, north, east,
        ),
    )  # fmt: skip


def write_archive_file(profile_folder: Path, relative: str) -> Path:
    path = profile_folder / "archivio" / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"contenuto di prova")
    return path


def write_v10(folder: Path, *, with_gpx: bool = True) -> None:
    """A v10 profile: three FIT, three GPX, journal entries, threshold tests and loads."""
    path = folder / PROFILE_DATABASE_NAME
    with closing(sqlite3.connect(path)) as raw:
        for statements in sqlite_schema.MIGRATIONS[:V10]:
            for statement in statements:
                raw.execute(statement)
        raw.executemany(
            "INSERT INTO meta (key, value) VALUES (?, ?)",
            [
                ("profile_id", "00000000-0000-4000-8000-0000000000aa"),
                ("name", "Atleta di prova"),
                ("timezone", "Europe/Rome"),
                ("futuro", "conservami"),
            ],
        )
        raw.execute(
            "INSERT INTO physiology (valid_from, min_bpm, max_bpm, threshold_bpm, sex) "
            "VALUES ('2026-01-01', 50, 190, 170, 'female')"
        )
        raw.execute("INSERT INTO sync_cursor VALUES ('full', 40, 40, 1)")
        insert_activity(
            raw, FIT_KEY_1, "fit", ARCHIVE_PATH, calories=612.0, track_box=(1.0, 2.0, 1.5, 2.5)
        )
        insert_activity(raw, FIT_KEY_2, "fit", FIT_PATH_2)
        insert_activity(
            raw, FIT_KEY_3, "fit", "2026/" + "ef" * 32 + ".fit", garmin_id=GARMIN_ID_OF_A_FIT
        )
        journal = [(GARMIN_ID_OF_A_FIT, "downloaded")]
        if with_gpx:
            insert_activity(raw, "gpx:" + "11" * 32, "gpx", GPX_IMPORTED_PATH)
            insert_activity(
                raw, "gpx:" + "22" * 32, "gpx", GPX_GARMIN_PATH,
                origin="garmin", garmin_id=GARMIN_ID_DOWNLOADED,
            )  # fmt: skip
            insert_activity(
                raw, "gpx:" + "33" * 32, "gpx", GPX_MATCHED_PATH, garmin_id=GARMIN_ID_MATCHED
            )
            journal += [(GARMIN_ID_DOWNLOADED, "downloaded"), (GARMIN_ID_MATCHED, "matched")]
        for garmin_id, status in journal:
            raw.execute(
                "INSERT INTO sync_journal VALUES (?, ?, 3600.0, 'running', 1, ?, 0, NULL, ?)",
                (garmin_id, START_MS, status, START_MS + 7),
            )
        for activity_id in range(1, 7 if with_gpx else 4):
            raw.execute(
                "INSERT INTO load_cache (activity_id, fingerprint, trimp, hr_tss, load, "
                "used_samples, discarded_samples, excluded_seconds, source) "
                "VALUES (?, ?, 80.0, 70.0, 75.0, 3500, 3, 12.5, 'estimate')",
                (activity_id, FINGERPRINT),
            )
        tests = [(FIT_KEY_1, "twenty_minutes")] + (
            [("gpx:" + "11" * 32, "thirty_minutes")] if with_gpx else []
        )
        for key, protocol in tests:
            raw.execute(
                "INSERT INTO threshold_test (activity_key, test_date, segment_start_ms, "
                "threshold_bpm, pace_s_per_km, method_version, protocol) "
                "VALUES (?, '2026-03-02', ?, 160, 310, 1, ?)",
                (key, START_MS, protocol),
            )
        raw.execute(f"PRAGMA user_version = {V10}")
        raw.commit()
    for relative in (ARCHIVE_PATH, FIT_PATH_2, *(GPX_PATHS if with_gpx else ())):
        write_archive_file(folder, relative)


def open_profile(folder: Path) -> OpenProfile:
    return SqliteProfileStorage().open(folder)


def journal_of(folder: Path) -> dict[int, tuple[str, int, str | None]]:
    query = "SELECT garmin_id, status, failed_attempts, failure FROM sync_journal"
    with closing(sqlite3.connect(folder / PROFILE_DATABASE_NAME)) as raw:
        return {row[0]: (row[1], row[2], row[3]) for row in raw.execute(query)}


def archive_files(folder: Path) -> set[str]:
    root = folder / "archivio"
    return {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}


# --- Migration ---------------------------------------------------------------------------------


def test_v11_is_the_migration_that_removes_non_fit_activities() -> None:
    assert sqlite_schema.SCHEMA_VERSION >= 11
    assert sqlite_schema.NON_FIT_REMOVED_IN == 11


def test_a_v10_profile_with_gpx_activities_opens_with_only_the_fit_ones(tmp_path: Path) -> None:
    write_v10(tmp_path)

    opened = open_profile(tmp_path)
    try:
        records = opened.activities.list_by_start()
        assert opened.info.format_version == sqlite_schema.SCHEMA_VERSION
        assert opened.removed_unsupported_activities == 3
    finally:
        opened.close()

    assert len(records) == 3
    assert all(isinstance(r.identity, FitIdentity) for r in records)
    assert {r.archived.relative_path.suffix for r in records} == {".fit"}
    with closing(sqlite3.connect(tmp_path / PROFILE_DATABASE_NAME)) as raw:
        formats = {row[0] for row in raw.execute("SELECT file_format FROM activities")}
    assert formats == {"fit"}


def test_the_gpx_files_leave_the_archive_and_the_fit_files_stay(tmp_path: Path) -> None:
    write_v10(tmp_path)

    open_profile(tmp_path).close()

    assert archive_files(tmp_path) == {ARCHIVE_PATH, FIT_PATH_2}


def test_a_copy_of_the_v10_database_is_made_before_anything_changes(tmp_path: Path) -> None:
    write_v10(tmp_path)
    before_kinds = rows(tmp_path / PROFILE_DATABASE_NAME, "activities")

    open_profile(tmp_path).close()

    [copy] = sorted((tmp_path / "backup").glob("*"))
    assert copy.name == f"ponos-v10-v{sqlite_schema.SCHEMA_VERSION}.sqlite3"
    assert user_version(copy) == V10
    assert rows(copy, "activities") == before_kinds  # the copy still has the GPX ones
    assert len(rows(copy, "activities")) == 6


def test_the_notice_comes_once_the_second_open_removes_nothing_and_copies_nothing(
    tmp_path: Path,
) -> None:
    write_v10(tmp_path)
    open_profile(tmp_path).close()

    again = open_profile(tmp_path)
    try:
        assert again.removed_unsupported_activities == 0
    finally:
        again.close()
    assert len(list((tmp_path / "backup").glob("*"))) == 1


def test_a_profile_without_gpx_activities_reports_nothing_removed(tmp_path: Path) -> None:
    write_v10(tmp_path, with_gpx=False)

    opened = open_profile(tmp_path)
    try:
        assert opened.removed_unsupported_activities == 0
        assert len(opened.activities.list_by_start()) == 3
    finally:
        opened.close()
    assert archive_files(tmp_path) == {ARCHIVE_PATH, FIT_PATH_2}


def test_the_sync_journal_entries_of_removed_activities_are_to_be_downloaded_again(
    tmp_path: Path,
) -> None:
    write_v10(tmp_path)

    open_profile(tmp_path).close()

    journal = journal_of(tmp_path)
    assert journal[GARMIN_ID_DOWNLOADED] == ("pending", 0, None)  # the Garmin GPX is removed
    assert journal[GARMIN_ID_MATCHED] == ("pending", 0, None)  # the matched GPX is removed
    assert journal[GARMIN_ID_OF_A_FIT] == ("downloaded", 0, None)  # a FIT is left alone


def test_the_load_cache_is_emptied_because_it_is_derived(tmp_path: Path) -> None:
    write_v10(tmp_path)

    open_profile(tmp_path).close()

    assert rows(tmp_path / PROFILE_DATABASE_NAME, "load_cache") == []


def test_the_database_refuses_a_gpx_row_after_the_migration(tmp_path: Path) -> None:
    write_v10(tmp_path)
    open_profile(tmp_path).close()

    with closing(sqlite3.connect(tmp_path / PROFILE_DATABASE_NAME)) as raw:
        with pytest.raises(sqlite3.IntegrityError):
            insert_activity(raw, "gpx:" + "44" * 32, "gpx", "2026/" + "44" * 32 + ".gpx")
        insert_activity(
            raw, "fit:9:9:9:2026-03-02T07:00:00+00:00", "fit", "2026/x.fit"
        )  # a FIT is fine


def test_the_other_tables_survive_the_migration_intact(tmp_path: Path) -> None:
    write_v10(tmp_path)
    path = tmp_path / PROFILE_DATABASE_NAME
    kept = ("physiology", "sync_cursor", "sync_state", "weight", "sport_weights", "profile_body")
    before = {table: rows(path, table) for table in kept}
    meta_before = rows(path, "meta")

    open_profile(tmp_path).close()

    assert {table: rows(path, table) for table in kept} == before
    assert rows(path, "meta") == meta_before  # unknown key `futuro` included


def test_the_remaining_activities_keep_their_ids_and_identities(tmp_path: Path) -> None:
    write_v10(tmp_path)

    open_profile(tmp_path).close()

    with closing(sqlite3.connect(tmp_path / PROFILE_DATABASE_NAME)) as raw:
        kept = raw.execute("SELECT id, identity_key FROM activities ORDER BY id").fetchall()
    assert [row[0] for row in kept] == [1, 2, 3]
    assert [row[1] for row in kept] == [
        "fit:1:2:3:2026-03-02T07:00:00+00:00",
        "fit:1:2:4:2026-03-02T07:00:00+00:00",
        "fit:1:2:5:2026-03-02T07:00:00+00:00",
    ]


def test_the_threshold_tests_of_removed_activities_go_and_the_others_keep_their_protocol(
    tmp_path: Path,
) -> None:
    write_v10(tmp_path)

    open_profile(tmp_path).close()

    with closing(sqlite3.connect(tmp_path / PROFILE_DATABASE_NAME)) as raw:
        tests = raw.execute("SELECT activity_key, protocol FROM threshold_test").fetchall()
        with pytest.raises(sqlite3.IntegrityError):
            raw.execute(
                "INSERT INTO threshold_test (activity_key, test_date, segment_start_ms, "
                "threshold_bpm, pace_s_per_km, method_version) "
                "VALUES (?, '2026-03-02', 0, 160, 310, 1)",
                ("gpx:" + "55" * 32,),
            )
    assert tests == [(FIT_KEY_1, "twenty_minutes")]


def test_the_columns_added_after_v1_survive_in_the_remaining_activities(tmp_path: Path) -> None:
    write_v10(tmp_path)

    open_profile(tmp_path).close()

    with closing(sqlite3.connect(tmp_path / PROFILE_DATABASE_NAME)) as raw:
        row = raw.execute(
            "SELECT calories_kcal, track_south, track_west, track_north, track_east "
            "FROM activities WHERE id = 1"
        ).fetchone()
        other = raw.execute(
            "SELECT calories_kcal, track_south FROM activities WHERE id = 2"
        ).fetchone()
    assert row == (612.0, 1.0, 2.0, 1.5, 2.5)
    assert other == (None, None)  # the optional columns stay empty where they were


# --- Interruptions -------------------------------------------------------------------------------


@pytest.mark.parametrize("kill_at", range(1, V11_STATEMENTS + 1))
def test_a_migration_killed_at_every_statement_changes_nothing_and_then_resumes(
    tmp_path: Path, kill_at: int
) -> None:
    write_v10(tmp_path)
    path = tmp_path / PROFILE_DATABASE_NAME
    before = {table: rows(path, table) for table in ("activities", "load_cache", "sync_journal")}
    files_before = archive_files(tmp_path)
    # From v10: the worker kills the process at statement `kill_at` of the whole chain.
    done = subprocess.run(  # noqa: S603 - fixed interpreter and script
        [sys.executable, "-I", str(WORKER), str(tmp_path), str(kill_at)],
        capture_output=True, text=True, timeout=120, check=False,
    )  # fmt: skip
    assert done.returncode == DIED, done.stderr

    assert user_version(path) == V10  # the transaction was not committed
    assert {table: rows(path, table) for table in before} == before
    assert archive_files(tmp_path) == files_before  # files are removed only after the commit
    opened = open_profile(tmp_path)  # the good run succeeds from where it was
    try:
        assert opened.removed_unsupported_activities == 3
    finally:
        opened.close()
    assert user_version(path) == sqlite_schema.SCHEMA_VERSION
    assert archive_files(tmp_path) == {ARCHIVE_PATH, FIT_PATH_2}


def test_a_failing_migration_leaves_the_v10_database_and_every_file_as_they_were(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_v10(tmp_path)
    path = tmp_path / PROFILE_DATABASE_NAME
    before = rows(path, "activities")
    broken_v11 = (*sqlite_schema.MIGRATIONS[V10][:3], "CREATE TABLE questa_no (")
    monkeypatch.setattr(
        sqlite_database, "MIGRATIONS", (*sqlite_schema.MIGRATIONS[:V10], broken_v11)
    )

    with pytest.raises((ProfileStorageError, sqlite3.Error)):
        open_profile(tmp_path)

    assert user_version(path) == V10
    assert rows(path, "activities") == before
    assert archive_files(tmp_path) == {ARCHIVE_PATH, FIT_PATH_2, *GPX_PATHS}


# --- Archive files ----------------------------------------------------------------------------


def test_a_gpx_path_that_is_not_the_archive_form_is_never_removed(tmp_path: Path) -> None:
    write_v10(tmp_path)
    outside = tmp_path / "fuori.gpx"
    outside.write_bytes(b"non e' dell'archivio")
    with closing(sqlite3.connect(tmp_path / PROFILE_DATABASE_NAME)) as raw:
        raw.execute(
            "UPDATE activities SET archive_path = '../fuori.gpx' WHERE archive_path = ?",
            (GPX_IMPORTED_PATH,),
        )
        raw.commit()

    opened = open_profile(tmp_path)
    try:
        assert opened.removed_unsupported_activities == 3  # the record is removed anyway
    finally:
        opened.close()

    assert outside.read_bytes() == b"non e' dell'archivio"
    assert archive_files(tmp_path) == {ARCHIVE_PATH, FIT_PATH_2, GPX_IMPORTED_PATH}  # never touched


def test_a_gpx_file_already_missing_from_the_archive_does_not_stop_the_migration(
    tmp_path: Path,
) -> None:
    write_v10(tmp_path)
    (tmp_path / "archivio" / GPX_GARMIN_PATH).unlink()

    opened = open_profile(tmp_path)
    try:
        assert opened.removed_unsupported_activities == 3
    finally:
        opened.close()

    assert archive_files(tmp_path) == {ARCHIVE_PATH, FIT_PATH_2}


def test_a_new_profile_is_created_at_the_latest_version_with_nothing_removed(
    tmp_path: Path,
) -> None:
    created = SqliteProfileStorage().create(
        tmp_path, ProfileId(uuid.uuid4()), ProfileName("Atleta di prova"), "UTC"
    )
    try:
        assert created.info.format_version == sqlite_schema.SCHEMA_VERSION
        assert created.removed_unsupported_activities == 0
    finally:
        created.close()
