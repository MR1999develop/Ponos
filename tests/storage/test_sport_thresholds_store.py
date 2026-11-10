# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""The cycling and other-activities thresholds: the migration that adds them and the store.

The migration is found by identity (`SPORT_THRESHOLDS_MIGRATION`), not by its number, so the tests
stay valid when other migrations are added before or after it. The same store contract runs on the
in-memory fake and on SQLite.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import closing
from datetime import date
from pathlib import Path

import pytest

from ponos_coach.load.domain import (
    HeartRateLimits,
    Physiology,
    PhysiologyEntry,
    Sex,
    ThresholdHeartRate,
)
from ponos_coach.load.ports import PhysiologyStore
from ponos_coach.profiles.domain import PROFILE_DATABASE_NAME, ProfileId, ProfileName
from ponos_coach.storage.schema_sport_thresholds import SPORT_THRESHOLDS_MIGRATION
from ponos_coach.storage.sqlite_database import create_database
from ponos_coach.storage.sqlite_profile import SqliteProfileStorage
from ponos_coach.storage.sqlite_schema import MIGRATIONS, SCHEMA_VERSION
from tests.builders import absolute_path
from tests.fakes.profile_storage import InMemoryProfileStorage

PROFILE_ID = "00000000-0000-4000-8000-000000000001"
META = {"profile_id": PROFILE_ID, "name": "Atleta di prova", "timezone": "Europe/Rome"}
FROZEN_BIKE = (
    "ALTER TABLE physiology ADD COLUMN bike_threshold_bpm INTEGER CHECK ("
    "bike_threshold_bpm IS NULL OR (bike_threshold_bpm >= 40 "
    "AND bike_threshold_bpm > min_bpm AND bike_threshold_bpm <= max_bpm))"
)
FROZEN_OTHER = FROZEN_BIKE.replace("bike_", "other_")
INSERT_WITH = {
    "bike_threshold_bpm": (
        "INSERT INTO physiology (valid_from, min_bpm, max_bpm, threshold_bpm, sex, "
        "bike_threshold_bpm) VALUES ('2026-01-01', 50, 190, 170, 'male', ?)"
    ),
    "other_threshold_bpm": (
        "INSERT INTO physiology (valid_from, min_bpm, max_bpm, threshold_bpm, sex, "
        "other_threshold_bpm) VALUES ('2026-01-01', 50, 190, 170, 'male', ?)"
    ),
}


def _position() -> int:
    """The version this migration comes after (its place in the list, found by identity)."""
    return next(i for i, m in enumerate(MIGRATIONS) if m is SPORT_THRESHOLDS_MIGRATION)


def _write_database_before_the_migration(path: Path) -> None:
    before = _position()
    with closing(sqlite3.connect(path)) as raw:
        for migration in MIGRATIONS[:before]:
            for statement in migration:
                raw.execute(statement)
        raw.executemany("INSERT INTO meta (key, value) VALUES (?, ?)", list(META.items()))
        raw.execute(
            "INSERT INTO physiology (valid_from, min_bpm, max_bpm, threshold_bpm, sex) "
            "VALUES ('2026-01-01', 50, 190, 170, 'female')"
        )
        raw.execute(f"PRAGMA user_version = {before}")
        raw.commit()


def test_the_released_migration_has_not_changed() -> None:
    assert SPORT_THRESHOLDS_MIGRATION == (FROZEN_BIKE, FROZEN_OTHER)


def test_a_profile_from_before_the_migration_keeps_its_entry_with_no_sport_thresholds(
    tmp_path: Path,
) -> None:
    path = tmp_path / PROFILE_DATABASE_NAME
    _write_database_before_the_migration(path)

    opened = SqliteProfileStorage().open(tmp_path)
    try:
        assert opened.info.format_version == SCHEMA_VERSION
        [entry] = opened.physiology.list_entries()
    finally:
        opened.close()

    assert entry == PhysiologyEntry(
        date(2026, 1, 1), Physiology(HeartRateLimits(50, 190), ThresholdHeartRate(170), Sex.FEMALE)
    )
    assert entry.physiology.bike_threshold is None
    assert entry.physiology.other_threshold is None
    [copy] = sorted((tmp_path / "backup").glob("*"))
    assert copy.name == f"ponos-v{_position()}-v{SCHEMA_VERSION}.sqlite3"


def test_the_migration_leaves_the_load_cache_alone_because_the_fingerprint_changes_on_its_own() -> (
    None
):
    assert all("load_cache" not in statement for statement in SPORT_THRESHOLDS_MIGRATION)


@pytest.mark.parametrize("column", ["bike_threshold_bpm", "other_threshold_bpm"])
@pytest.mark.parametrize(
    "value", [50, 49, 191, 39, 170.5], ids=["at-min", "below-min", "above-max", "under-40", "float"]
)
def test_the_database_itself_refuses_an_invalid_threshold(
    tmp_path: Path, column: str, value: float
) -> None:
    path = tmp_path / PROFILE_DATABASE_NAME
    create_database(path, META).close()

    with closing(sqlite3.connect(path)) as raw, pytest.raises(sqlite3.IntegrityError):
        raw.execute(
            INSERT_WITH[column],
            (value,),
        )


@pytest.mark.parametrize("column", ["bike_threshold_bpm", "other_threshold_bpm"])
@pytest.mark.parametrize("value", [51, 190], ids=["just-above-min", "at-max"])
def test_the_database_accepts_the_limit_values(tmp_path: Path, column: str, value: int) -> None:
    path = tmp_path / PROFILE_DATABASE_NAME
    create_database(path, META).close()

    with closing(sqlite3.connect(path)) as raw:
        raw.execute(
            INSERT_WITH[column],
            (value,),
        )


# --- The store contract: fake and SQLite ---------------------------------------------------------


@pytest.fixture(params=["fake", "sqlite"])
def store(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[PhysiologyStore]:
    profile_id = ProfileId.parse(PROFILE_ID)
    name = ProfileName.parse("Atleta di prova")
    if request.param == "fake":
        yield (
            InMemoryProfileStorage()
            .create(absolute_path("soglie"), profile_id, name, "Europe/Rome")
            .physiology
        )
        return
    opened = SqliteProfileStorage().create(tmp_path, profile_id, name, "Europe/Rome")
    try:
        yield opened.physiology
    finally:
        opened.close()


def _entry(day: date, bike: int | None, other: int | None) -> PhysiologyEntry:
    return PhysiologyEntry(
        day,
        Physiology(
            HeartRateLimits(50, 190),
            ThresholdHeartRate(170),
            Sex.MALE,
            None if bike is None else ThresholdHeartRate(bike),
            None if other is None else ThresholdHeartRate(other),
        ),
    )


def test_the_store_round_trips_set_and_unset_thresholds(store: PhysiologyStore) -> None:
    first = _entry(date(2026, 1, 1), None, None)
    second = _entry(date(2026, 2, 1), 160, None)
    third = _entry(date(2026, 3, 1), 158, 140)
    for entry in (third, first, second):
        store.put(entry)

    assert list(store.list_entries()) == [first, second, third]


def test_putting_the_same_day_again_replaces_and_can_clear_a_threshold(
    store: PhysiologyStore,
) -> None:
    day = date(2026, 2, 1)
    store.put(_entry(day, 160, 140))
    store.put(_entry(day, None, 140))

    assert list(store.list_entries()) == [_entry(day, None, 140)]
