# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""LT1 of the two sport groups: the migration that adds the columns and the store.

The migration is found by identity (`LT1_MIGRATION`), not by its number, so the tests stay valid
when other migrations are added after it. The same store contract runs on the in-memory fake and
on SQLite.
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
from ponos_coach.storage.schema_lt1 import LT1_MIGRATION
from ponos_coach.storage.sqlite_database import create_database
from ponos_coach.storage.sqlite_profile import SqliteProfileStorage
from ponos_coach.storage.sqlite_schema import MIGRATIONS, SCHEMA_VERSION
from tests.builders import absolute_path
from tests.fakes.profile_storage import InMemoryProfileStorage

PROFILE_ID = "00000000-0000-4000-8000-000000000001"
META = {"profile_id": PROFILE_ID, "name": "Atleta di prova", "timezone": "Europe/Rome"}
FROZEN_RUN = (
    "ALTER TABLE physiology ADD COLUMN run_lt1_bpm INTEGER CHECK ("
    "run_lt1_bpm IS NULL OR (run_lt1_bpm > min_bpm AND run_lt1_bpm < threshold_bpm))"
)
FROZEN_OTHER = (
    "ALTER TABLE physiology ADD COLUMN other_lt1_bpm INTEGER CHECK ("
    "other_lt1_bpm IS NULL OR (other_lt1_bpm > min_bpm "
    "AND other_lt1_bpm < COALESCE(other_threshold_bpm, threshold_bpm)))"
)


def _position() -> int:
    """The version this migration comes after (its place in the list, found by identity)."""
    return next(i for i, m in enumerate(MIGRATIONS) if m is LT1_MIGRATION)


def _insert(raw: sqlite3.Connection, **columns: object) -> None:
    base: dict[str, object] = {
        "valid_from": "2026-01-01",
        "min_bpm": 50,
        "max_bpm": 190,
        "threshold_bpm": 170,
        "sex": "male",
    }
    values = base | columns
    names = ", ".join(values)
    marks = ", ".join("?" for _ in values)
    raw.execute(
        f"INSERT INTO physiology ({names}) VALUES ({marks})",  # noqa: S608 - test names only
        list(values.values()),
    )


def test_the_released_migration_has_not_changed() -> None:
    assert LT1_MIGRATION == (FROZEN_RUN, FROZEN_OTHER)


def test_the_migration_leaves_the_load_cache_alone() -> None:
    assert all("load_cache" not in statement for statement in LT1_MIGRATION)


def test_a_profile_from_before_the_migration_keeps_its_entry_with_no_lt1_and_the_bike_unread(
    tmp_path: Path,
) -> None:
    path = tmp_path / PROFILE_DATABASE_NAME
    before = _position()
    with closing(sqlite3.connect(path)) as raw:
        for migration in MIGRATIONS[:before]:
            for statement in migration:
                raw.execute(statement)
        raw.executemany("INSERT INTO meta (key, value) VALUES (?, ?)", list(META.items()))
        _insert(raw, sex="female", bike_threshold_bpm=160, other_threshold_bpm=165)
        raw.execute(f"PRAGMA user_version = {before}")
        raw.commit()

    opened = SqliteProfileStorage().open(tmp_path)
    try:
        assert opened.info.format_version == SCHEMA_VERSION
        [entry] = opened.physiology.list_entries()
    finally:
        opened.close()

    # The old bike value is not read any more; the other-sports LT2 is kept as it was.
    assert entry == PhysiologyEntry(
        date(2026, 1, 1),
        Physiology(
            HeartRateLimits(50, 190),
            ThresholdHeartRate(170),
            Sex.FEMALE,
            other_threshold=ThresholdHeartRate(165),
        ),
    )
    [copy] = sorted((tmp_path / "backup").glob("*"))
    assert copy.name == f"ponos-v{before}-v{SCHEMA_VERSION}.sqlite3"


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("run_lt1_bpm", 50),  # on the resting rate
        ("run_lt1_bpm", 170),  # on LT2
        ("run_lt1_bpm", 171),
        ("run_lt1_bpm", 140.5),
        ("other_lt1_bpm", 50),
        ("other_lt1_bpm", 170),  # no other LT2: compared with the running one
    ],
)
def test_the_database_itself_refuses_an_invalid_lt1(
    tmp_path: Path, column: str, value: float
) -> None:
    path = tmp_path / PROFILE_DATABASE_NAME
    create_database(path, META).close()

    with closing(sqlite3.connect(path)) as raw, pytest.raises(sqlite3.IntegrityError):
        _insert(raw, **{column: value})


def test_the_other_lt1_is_checked_against_the_other_lt2_when_there_is_one(tmp_path: Path) -> None:
    path = tmp_path / PROFILE_DATABASE_NAME
    create_database(path, META).close()

    with closing(sqlite3.connect(path)) as raw:
        _insert(raw, other_threshold_bpm=150, other_lt1_bpm=149)
        with pytest.raises(sqlite3.IntegrityError):
            _insert(raw, valid_from="2026-02-01", other_threshold_bpm=150, other_lt1_bpm=150)


@pytest.mark.parametrize("column", ["run_lt1_bpm", "other_lt1_bpm"])
@pytest.mark.parametrize("value", [51, 169], ids=["just-above-min", "just-below-lt2"])
def test_the_database_accepts_the_limit_values(tmp_path: Path, column: str, value: int) -> None:
    path = tmp_path / PROFILE_DATABASE_NAME
    create_database(path, META).close()

    with closing(sqlite3.connect(path)) as raw:
        _insert(raw, **{column: value})


# --- The store contract: fake and SQLite ---------------------------------------------------------


@pytest.fixture(params=["fake", "sqlite"])
def store(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[PhysiologyStore]:
    profile_id = ProfileId.parse(PROFILE_ID)
    name = ProfileName.parse("Atleta di prova")
    if request.param == "fake":
        yield (
            InMemoryProfileStorage()
            .create(absolute_path("lt1"), profile_id, name, "Europe/Rome")
            .physiology
        )
        return
    opened = SqliteProfileStorage().create(tmp_path, profile_id, name, "Europe/Rome")
    try:
        yield opened.physiology
    finally:
        opened.close()


def _entry(
    day: date, run_lt1: int | None, other: int | None, other_lt1: int | None
) -> PhysiologyEntry:
    def opt(bpm: int | None) -> ThresholdHeartRate | None:
        return None if bpm is None else ThresholdHeartRate(bpm)

    return PhysiologyEntry(
        day,
        Physiology(
            HeartRateLimits(50, 190),
            ThresholdHeartRate(170),
            Sex.MALE,
            other_threshold=opt(other),
            run_lt1=opt(run_lt1),
            other_lt1=opt(other_lt1),
        ),
    )


def test_the_store_round_trips_set_and_unset_lt1(store: PhysiologyStore) -> None:
    first = _entry(date(2026, 1, 1), None, None, None)
    second = _entry(date(2026, 2, 1), 140, None, None)
    third = _entry(date(2026, 3, 1), 142, 150, 125)
    for entry in (third, first, second):
        store.put(entry)

    assert list(store.list_entries()) == [first, second, third]


def test_putting_the_same_day_again_replaces_and_can_clear_an_lt1(store: PhysiologyStore) -> None:
    day = date(2026, 2, 1)
    store.put(_entry(day, 140, 150, 125))
    store.put(_entry(day, None, 150, None))

    assert list(store.list_entries()) == [_entry(day, None, 150, None)]
