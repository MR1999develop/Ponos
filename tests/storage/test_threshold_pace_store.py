# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""The threshold pace series: the migration that creates it (T-DATI-01) and the store.

The migration is found by name (`THRESHOLD_PACE_MIGRATION`), not by its number, so the tests stay
valid when other migrations are added before or after it. The same store contract runs on the
in-memory fake and on SQLite.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import closing
from datetime import date
from pathlib import Path

import pytest

from ponos_coach.profiles.domain import (
    PROFILE_DATABASE_NAME,
    ProfileId,
    ProfileName,
    ThresholdPace,
    ThresholdPaceEntry,
)
from ponos_coach.profiles.ports import ProfileDataError, ThresholdPaceStore
from ponos_coach.storage.schema_threshold_pace import THRESHOLD_PACE_MIGRATION
from ponos_coach.storage.sqlite_database import create_database
from ponos_coach.storage.sqlite_profile import SqliteProfileStorage
from ponos_coach.storage.sqlite_profile_data import SqliteThresholdPaceStore
from ponos_coach.storage.sqlite_schema import MIGRATIONS, SCHEMA_VERSION
from tests.builders import absolute_path
from tests.fakes.profile_storage import InMemoryProfileStorage

PROFILE_ID = "00000000-0000-4000-8000-000000000001"
FROZEN_THRESHOLD_PACE = (
    "CREATE TABLE threshold_pace ("
    "valid_from TEXT NOT NULL PRIMARY KEY CHECK (date(valid_from) IS valid_from "
    "AND valid_from >= '1900-01-01'), "
    "s_per_km INTEGER NOT NULL CHECK (s_per_km BETWEEN 90 AND 3600)) STRICT"
)
META = {"profile_id": PROFILE_ID, "name": "Atleta di prova", "timezone": "Europe/Rome"}


def _squash(text: str) -> str:
    return "".join(text.split())


def _position() -> int:
    """The version this migration comes after (its place in the list, found by identity)."""
    return next(i for i, m in enumerate(MIGRATIONS) if m is THRESHOLD_PACE_MIGRATION)


def _write_database_before_the_migration(path: Path) -> None:
    """A profile database at the version just before this migration, written with the earlier
    migrations of the list (the ones this change did not touch)."""
    before = _position()
    with closing(sqlite3.connect(path)) as raw:
        for migration in MIGRATIONS[:before]:
            for statement in migration:
                raw.execute(statement)
        raw.executemany("INSERT INTO meta (key, value) VALUES (?, ?)", list(META.items()))
        raw.execute("INSERT INTO physiology VALUES ('2026-01-01', 50, 190, 170, 'female')")
        raw.execute("INSERT INTO weight VALUES ('2026-01-01', 700)")
        raw.execute(f"PRAGMA user_version = {before}")
        raw.commit()


def test_the_released_migration_has_not_changed() -> None:
    assert tuple(_squash(s) for s in THRESHOLD_PACE_MIGRATION) == (_squash(FROZEN_THRESHOLD_PACE),)


def test_a_profile_from_before_the_migration_gains_the_series_and_keeps_everything_else(
    tmp_path: Path,
) -> None:
    path = tmp_path / PROFILE_DATABASE_NAME
    _write_database_before_the_migration(path)

    opened = SqliteProfileStorage().open(tmp_path)
    try:
        assert opened.info.format_version == SCHEMA_VERSION
        assert list(opened.threshold_paces.list_entries()) == []
        assert len(opened.weights.list_entries()) == 1
        assert len(opened.physiology.list_entries()) == 1
    finally:
        opened.close()

    [copy] = sorted((tmp_path / "backup").glob("*"))
    assert copy.name == f"ponos-v{_position()}-v{SCHEMA_VERSION}.sqlite3"


def test_the_migration_leaves_the_load_cache_alone_because_the_pace_does_not_enter_the_load() -> (
    None
):
    assert all("load_cache" not in statement for statement in THRESHOLD_PACE_MIGRATION)


@pytest.mark.parametrize(
    "row",
    [
        ("2026-02-30", 300),  # not a date
        ("1899-12-31", 300),  # before the first allowed date
        ("2026-03-01", 89),  # below 1:30
        ("2026-03-01", 3601),  # above 60:00
        ("2026-03-01", 300.5),  # not a whole number of seconds
    ],
)
def test_the_database_itself_refuses_an_invalid_row(tmp_path: Path, row: tuple[str, float]) -> None:
    path = tmp_path / PROFILE_DATABASE_NAME
    create_database(path, META).close()

    with closing(sqlite3.connect(path)) as raw, pytest.raises(sqlite3.IntegrityError):
        raw.execute("INSERT INTO threshold_pace VALUES (?, ?)", row)


# --- The store contract: fake and SQLite ---------------------------------------------------------
@pytest.fixture(params=["fake", "sqlite"])
def store(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[ThresholdPaceStore]:
    if request.param == "fake":
        opened = InMemoryProfileStorage().create(
            absolute_path("ritmo"), ProfileId.parse(PROFILE_ID), ProfileName.parse("Prova"), "UTC"
        )
        yield opened.threshold_paces
        return
    database = create_database(tmp_path / PROFILE_DATABASE_NAME, META)
    try:
        yield SqliteThresholdPaceStore(database)
    finally:
        database.close()


def _entry(day: date, seconds: int) -> ThresholdPaceEntry:
    return ThresholdPaceEntry(day, ThresholdPace(seconds))


def test_an_empty_series_lists_nothing(store: ThresholdPaceStore) -> None:
    assert list(store.list_entries()) == []


def test_entries_come_back_by_ascending_date_and_the_same_date_is_replaced(
    store: ThresholdPaceStore,
) -> None:
    store.put(_entry(date(2026, 3, 1), 330))
    store.put(_entry(date(2026, 1, 1), 345))
    store.put(_entry(date(2026, 3, 1), 320))

    assert list(store.list_entries()) == [
        _entry(date(2026, 1, 1), 345),
        _entry(date(2026, 3, 1), 320),
    ]


def test_a_failed_write_is_a_profile_data_error_that_does_not_repeat_the_value(
    tmp_path: Path,
) -> None:
    database = create_database(tmp_path / PROFILE_DATABASE_NAME, META)
    try:
        database.connection.execute("DROP TABLE threshold_pace")

        with pytest.raises(ProfileDataError) as error:
            SqliteThresholdPaceStore(database).put(_entry(date(2026, 3, 1), 317))
    finally:
        database.close()

    assert "317" not in str(error.value)
    assert "2026" not in str(error.value)
