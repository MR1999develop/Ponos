# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""T-DATI-01 for schema v8: from the frozen v7 database to v8 (track box for the map).

The v7 database is written by hand, independent of the code (v1 to v7 are in the earlier files).
It is shown that v8 does not change after release, that a v7 profile migrates without losing
anything and keeps a copy (`backup/ponos-v7-v8.sqlite3`), that the migration is one transaction
(killed at every statement the database is still v7), that the database itself refuses an
inconsistent box, and that the boxes of the activities indexed before v8 are filled afterwards by
the resumable pass, reading only the index.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from ponos_coach.activities.domain import GeoBox, GeoPoint, encode_polyline
from ponos_coach.activities.map_queries import backfill_track_boxes
from ponos_coach.profiles.domain import PROFILE_DATABASE_NAME
from ponos_coach.profiles.ports import ProfileStorageError
from ponos_coach.storage import sqlite_database, sqlite_schema
from ponos_coach.storage.sqlite_profile import SqliteProfileStorage
from tests.collaudo.test_database_del_profilo import rows, squash
from tests.collaudo.test_fase3_dati_personali import run_worker_until_death
from tests.collaudo.test_fase3_migrazione import table_names, user_version
from tests.collaudo.test_fase5_migrazione import KEPT_V5, V6_TABLES
from tests.collaudo.test_fase5_migrazione_v7 import FROZEN_V7, KEPT_V6, write_frozen_v6
from tests.fixtures.generators.collaudo_fase3_worker import DIED

# Migration v8 as it was released. It does not change, like v1 to v7.
FROZEN_V8 = (
    """ALTER TABLE activities ADD COLUMN track_south REAL
        CHECK (track_south IS NULL OR track_south BETWEEN -90 AND 90)""",
    """ALTER TABLE activities ADD COLUMN track_west REAL
        CHECK (track_west IS NULL OR track_west BETWEEN -180 AND 180)""",
    """ALTER TABLE activities ADD COLUMN track_north REAL
        CHECK (track_north IS NULL OR track_north BETWEEN -90 AND 90)""",
    """ALTER TABLE activities ADD COLUMN track_east REAL
        CHECK (track_east IS NULL OR (
            track_east BETWEEN -180 AND 180
            AND track_south IS NOT NULL AND track_west IS NOT NULL AND track_north IS NOT NULL
            AND track_south <= track_north))
        CHECK (track_east IS NOT NULL OR (
            track_south IS NULL AND track_west IS NULL AND track_north IS NULL))""",
)

KEPT_V7 = (*KEPT_V6, "lean_mass")
POLYLINE = encode_polyline([GeoPoint(45.0, 9.0), GeoPoint(45.5, 9.2), GeoPoint(45.2, 8.8)])


def write_frozen_v7(path: Path) -> None:
    """A v7 database as the phase 5 app wrote it, with a located and an unlocated activity."""
    write_frozen_v6(path)
    with closing(sqlite3.connect(path)) as raw:
        for statement in FROZEN_V7:
            raw.execute(statement)
        raw.execute(
            "INSERT INTO activities (identity_key, file_format, sport, start_ms, elapsed_s, "
            "timer_s, distance_m, ascent_m, start_lat, start_lon, track_polyline, archive_path, "
            "archive_sha256, archive_size_bytes, origin, imported_ms) "
            "VALUES ('fit:9:9:9:2026-03-03T07:00:00+00:00', 'fit', 'running', 1772521200000, "
            "3600.0, 3500.0, 10000.0, 120.0, 45.0, 9.0, ?, '2026/x.fit', ?, 10, 'import', "
            "1772521300000)",
            (POLYLINE, "a" * 64),
        )
        raw.execute("INSERT INTO lean_mass VALUES ('2026-03-01', 600)")
        raw.execute("PRAGMA user_version = 7")
        raw.commit()


def activities_before_v8(path: Path) -> list[tuple[object, ...]]:
    with closing(sqlite3.connect(path)) as raw:
        return raw.execute(
            "SELECT id, identity_key, sport, start_ms, distance_m, start_lat, start_lon, "
            "track_polyline FROM activities ORDER BY id"
        ).fetchall()


def test_t_dati_01_the_released_v8_migration_has_not_changed() -> None:
    released = tuple(squash(statement) for statement in sqlite_schema.MIGRATIONS[7])

    assert released == tuple(squash(statement) for statement in FROZEN_V8)
    assert sqlite_schema.SCHEMA_VERSION >= 8


def test_t_dati_01_a_frozen_v7_database_is_migrated_to_v8_with_data_intact_and_a_copy(
    tmp_path: Path,
) -> None:
    path = tmp_path / PROFILE_DATABASE_NAME
    write_frozen_v7(path)
    before = {table: rows(path, table) for table in KEPT_V7 if table != "activities"}
    activities = activities_before_v8(path)

    opened = SqliteProfileStorage().open(tmp_path)
    try:
        assert opened.info.format_version == sqlite_schema.SCHEMA_VERSION
        assert len(opened.activities.list_by_start()) == 2
    finally:
        opened.close()

    assert {table: rows(path, table) for table in before} == before
    assert activities_before_v8(path) == activities
    with closing(sqlite3.connect(path)) as raw:  # nothing is filled by the migration itself
        boxes = raw.execute(
            "SELECT track_south, track_west, track_north, track_east FROM activities"
        ).fetchall()
    assert boxes == [(None, None, None, None)] * 2
    [copy] = sorted((tmp_path / "backup").glob("*"))
    assert copy.name == f"ponos-v7-v{sqlite_schema.SCHEMA_VERSION}.sqlite3"
    assert user_version(copy) == 7
    with closing(sqlite3.connect(copy)) as raw:
        columns = [r[1] for r in raw.execute("PRAGMA table_info(activities)")]
    assert "track_south" not in columns
    assert {table: rows(copy, table) for table in KEPT_V7 if table != "activities"} == before


def test_t_dati_01_a_v6_database_goes_through_v7_and_v8_in_one_round_with_one_copy(
    tmp_path: Path,
) -> None:
    path = tmp_path / PROFILE_DATABASE_NAME
    write_frozen_v6(path)

    SqliteProfileStorage().open(tmp_path).close()

    assert user_version(path) == sqlite_schema.SCHEMA_VERSION
    [copy] = sorted((tmp_path / "backup").glob("*"))
    assert copy.name == f"ponos-v6-v{sqlite_schema.SCHEMA_VERSION}.sqlite3"
    assert set(KEPT_V5) | V6_TABLES <= table_names(path)


def test_t_dati_01_a_v8_migration_that_fails_halfway_leaves_the_v7_database_as_it_was(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / PROFILE_DATABASE_NAME
    write_frozen_v7(path)
    broken_v8 = (sqlite_schema.MIGRATIONS[7][0], "CREATE TABLE questa_no (")
    monkeypatch.setattr(sqlite_database, "MIGRATIONS", (*sqlite_schema.MIGRATIONS[:7], broken_v8))

    with pytest.raises((ProfileStorageError, sqlite3.Error)):
        SqliteProfileStorage().open(tmp_path)

    assert user_version(path) == 7
    with closing(sqlite3.connect(path)) as raw:  # not even the column that had succeeded
        columns = [r[1] for r in raw.execute("PRAGMA table_info(activities)")]
    assert "track_south" not in columns
    monkeypatch.undo()
    SqliteProfileStorage().open(tmp_path).close()  # the good round succeeds
    assert user_version(path) == sqlite_schema.SCHEMA_VERSION


@pytest.mark.parametrize("kill_at", range(1, len(sqlite_schema.MIGRATIONS[7]) + 1))
def test_t_dati_01_a_v7_database_killed_at_every_migration_statement_is_still_v7_then_migrates(
    tmp_path: Path, kill_at: int
) -> None:
    path = tmp_path / PROFILE_DATABASE_NAME
    write_frozen_v7(path)
    activities = activities_before_v8(path)

    assert run_worker_until_death(tmp_path, kill_at) == DIED

    assert user_version(path) == 7  # the transaction was not committed
    with closing(sqlite3.connect(path)) as raw:
        columns = [r[1] for r in raw.execute("PRAGMA table_info(activities)")]
    assert "track_south" not in columns  # not even the columns that had succeeded
    assert activities_before_v8(path) == activities
    SqliteProfileStorage().open(tmp_path).close()
    assert user_version(path) == sqlite_schema.SCHEMA_VERSION


# --- The database refuses an inconsistent box ---------------------------------------------


def migrated(tmp_path: Path) -> sqlite3.Connection:
    write_frozen_v7(tmp_path / PROFILE_DATABASE_NAME)
    SqliteProfileStorage().open(tmp_path).close()
    return sqlite3.connect(tmp_path / PROFILE_DATABASE_NAME)


def set_box(raw: sqlite3.Connection, south: str, west: str, north: str, east: str) -> None:
    raw.execute(
        f"UPDATE activities SET track_south = {south}, track_west = {west}, "  # noqa: S608
        f"track_north = {north}, track_east = {east} WHERE id = 2"
    )


BAD_BOXES = [
    ("-90.1", "0", "10", "10"),  # south out of range
    ("0", "0", "90.1", "10"),  # north out of range
    ("0", "-180.1", "10", "10"),  # west out of range
    ("0", "0", "10", "180.1"),  # east out of range
    ("20", "0", "10", "10"),  # south above north
    ("0", "0", "10", "NULL"),  # a box with a missing side
    ("NULL", "0", "10", "10"),
    ("0", "NULL", "10", "10"),
    ("0", "0", "NULL", "10"),
    ("'a'", "0", "10", "10"),  # REAL column: text that is not a number
]


@pytest.mark.parametrize("values", BAD_BOXES)
def test_the_database_refuses_an_inconsistent_box(tmp_path: Path, values: tuple[str, ...]) -> None:
    with closing(migrated(tmp_path)) as raw, pytest.raises(sqlite3.IntegrityError):
        set_box(raw, *values)


@pytest.mark.parametrize(
    "values",
    [
        ("NULL", "NULL", "NULL", "NULL"),  # no position
        ("0", "0", "10", "10"),
        ("44.9", "179.9", "45.1", "-179.9"),  # across the antimeridian: west above east
        ("-90", "-180", "90", "180"),  # the whole globe
        ("45", "9", "45", "9"),  # a single point
    ],
)
def test_the_database_accepts_a_consistent_box(tmp_path: Path, values: tuple[str, ...]) -> None:
    with closing(migrated(tmp_path)) as raw:
        set_box(raw, *values)
        raw.commit()


# --- The fill of the boxes of the activities indexed before v8 ---------------------------------


def test_the_fill_gives_the_old_activity_its_box_reading_only_the_index(tmp_path: Path) -> None:
    write_frozen_v7(tmp_path / PROFILE_DATABASE_NAME)  # its archive file does not exist
    opened = SqliteProfileStorage().open(tmp_path)
    try:
        index = opened.track_boxes
        assert index.count_without_box() == 1  # the unlocated activity needs none

        done = backfill_track_boxes(index)

        assert (done.filled, done.unresolved, done.cancelled) == (1, 0, False)
        assert index.count_without_box() == 0
        located = [item for item in index.list_by_start() if item.track_box is not None]
        assert [item.track_box for item in located] == [GeoBox(45.0, 8.8, 45.5, 9.2)]
        assert backfill_track_boxes(index).filled == 0
    finally:
        opened.close()


def test_a_fill_stopped_between_batches_keeps_the_batches_that_were_committed(
    tmp_path: Path,
) -> None:
    write_frozen_v7(tmp_path / PROFILE_DATABASE_NAME)
    opened = SqliteProfileStorage().open(tmp_path)
    try:
        with closing(sqlite3.connect(tmp_path / PROFILE_DATABASE_NAME)) as raw:
            for number in range(5):
                raw.execute(
                    "INSERT INTO activities (identity_key, file_format, sport, start_ms, "
                    "elapsed_s, timer_s, start_lat, start_lon, archive_path, archive_sha256, "
                    "archive_size_bytes, origin, imported_ms) "
                    "VALUES (?, 'fit', 'running', ?, 60.0, 60.0, 10.0, 20.0, '2026/y.fit', ?, "
                    "1, 'import', 1)",
                    (
                        f"fit:7:7:{number}:2026-03-04T07:00:00+00:00",
                        1772607600000 + number,
                        "b" * 64,
                    ),
                )
            raw.commit()
        calls = 0

        def die_on_third_batch() -> bool:
            nonlocal calls
            calls += 1
            return calls > 2

        first = backfill_track_boxes(
            opened.track_boxes, batch_size=2, is_cancelled=die_on_third_batch
        )
        assert first.cancelled
        remaining = opened.track_boxes.count_without_box()
        assert remaining == 6 - first.filled  # 1 old located + 5 new, two batches of 2 done
        assert backfill_track_boxes(opened.track_boxes, batch_size=2).cancelled is False
        assert opened.track_boxes.count_without_box() == 0
    finally:
        opened.close()
