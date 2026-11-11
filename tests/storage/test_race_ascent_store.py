# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""The elevation gain of a race: the migration that adds the column (T-DATI-01) and the store.

The migration is found by name (`RACE_ASCENT_MIGRATION`), not by its number, so the tests stay
valid when other migrations are added before or after it.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import date
from pathlib import Path

import pytest

from ponos_coach.activities.domain import Sport
from ponos_coach.goals.domain import FitnessGoal, GoalId, Priority, RaceGoal
from ponos_coach.profiles.domain import PROFILE_DATABASE_NAME
from ponos_coach.storage.schema_race_ascent import RACE_ASCENT_MIGRATION
from ponos_coach.storage.sqlite_database import create_database
from ponos_coach.storage.sqlite_goals import SqliteGoalStore
from ponos_coach.storage.sqlite_profile import SqliteProfileStorage
from ponos_coach.storage.sqlite_schema import MIGRATIONS, SCHEMA_VERSION

PROFILE_ID = "00000000-0000-4000-8000-000000000001"
META = {"profile_id": PROFILE_ID, "name": "Atleta di prova", "timezone": "Europe/Rome"}
FROZEN_ASCENT = (
    "ALTER TABLE goal ADD COLUMN ascent_m INTEGER NOT NULL DEFAULT 0 "
    "CHECK (ascent_m BETWEEN 0 AND 30000)"
)
RACE_DAY = date(2026, 9, 20)


def _squash(text: str) -> str:
    return "".join(text.split())


def _position() -> int:
    """The version this migration comes after (its place in the list, found by identity)."""
    return next(i for i, m in enumerate(MIGRATIONS) if m is RACE_ASCENT_MIGRATION)


def _write_database_before_the_migration(path: Path) -> None:
    """A profile database at the version just before this migration, with a saved race."""
    before = _position()
    with closing(sqlite3.connect(path)) as raw:
        for migration in MIGRATIONS[:before]:
            for statement in migration:
                raw.execute(statement)
        raw.executemany("INSERT INTO meta (key, value) VALUES (?, ?)", list(META.items()))
        raw.execute(
            "INSERT INTO goal (goal_id, kind, goal_date, priority, sport, distance_m, "
            "target_time_s, ctl) VALUES ('old', 'race', '2026-09-20', 'A', 'running', 42195, "
            "12000, NULL)"
        )
        raw.execute(
            "INSERT INTO goal (goal_id, kind, goal_date, priority, sport, distance_m, "
            "target_time_s, ctl) VALUES ('form', 'fitness', '2026-10-20', NULL, NULL, NULL, "
            "NULL, 55)"
        )
        raw.execute(f"PRAGMA user_version = {before}")
        raw.commit()


def test_the_released_migration_has_not_changed() -> None:
    assert tuple(_squash(s) for s in RACE_ASCENT_MIGRATION) == (_squash(FROZEN_ASCENT),)


def test_a_profile_from_before_the_migration_keeps_its_races_as_flat_ones_and_gets_a_copy(
    tmp_path: Path,
) -> None:
    path = tmp_path / PROFILE_DATABASE_NAME
    _write_database_before_the_migration(path)

    opened = SqliteProfileStorage().open(tmp_path)
    try:
        assert opened.info.format_version == SCHEMA_VERSION
        goals = {g.goal_id.value: g for g in opened.goals.list_goals()}
        old = goals["old"]
        assert isinstance(old, RaceGoal)
        assert (old.distance_m, old.target_time_s, old.ascent_m) == (42_195, 12_000, 0)
        assert isinstance(goals["form"], FitnessGoal)
    finally:
        opened.close()

    [copy] = sorted((tmp_path / "backup").glob("*"))
    assert copy.name == f"ponos-v{_position()}-v{SCHEMA_VERSION}.sqlite3"
    with closing(sqlite3.connect(copy)) as raw:
        columns = [row[1] for row in raw.execute("PRAGMA table_info(goal)")]
    assert "ascent_m" not in columns  # the copy is the database as it was


@pytest.mark.parametrize("ascent", [0, 1, 30_000])
def test_the_database_accepts_the_limits_of_the_elevation_gain(tmp_path: Path, ascent: int) -> None:
    path = tmp_path / PROFILE_DATABASE_NAME
    create_database(path, META).close()

    with closing(sqlite3.connect(path)) as raw:
        raw.execute(
            "INSERT INTO goal (goal_id, kind, goal_date, priority, sport, distance_m, ascent_m) "
            "VALUES ('a', 'race', '2026-09-20', 'A', 'trail', 100000, ?)",
            (ascent,),
        )


@pytest.mark.parametrize("ascent", [-1, 30_001, 100.5, "high", None])
def test_the_database_itself_refuses_an_invalid_elevation_gain(
    tmp_path: Path, ascent: object
) -> None:
    path = tmp_path / PROFILE_DATABASE_NAME
    create_database(path, META).close()

    with closing(sqlite3.connect(path)) as raw, pytest.raises(sqlite3.IntegrityError):
        raw.execute(
            "INSERT INTO goal (goal_id, kind, goal_date, priority, sport, distance_m, ascent_m) "
            "VALUES ('a', 'race', '2026-09-20', 'A', 'trail', 100000, ?)",
            (ascent,),
        )


def test_the_store_saves_and_reads_back_the_elevation_gain_and_replaces_it(
    tmp_path: Path,
) -> None:
    database = create_database(tmp_path / PROFILE_DATABASE_NAME, META)
    try:
        store = SqliteGoalStore(database)
        race = RaceGoal(GoalId("a"), RACE_DAY, Priority.A, Sport.TRAIL, 30_000, 14_400, 1_500)
        store.add(race)
        store.add(FitnessGoal(GoalId("f"), date(2026, 10, 1), 60.0))

        assert store.get(GoalId("a")) == race
        assert [g.goal_id.value for g in store.list_goals()] == ["a", "f"]

        flat = RaceGoal(GoalId("a"), RACE_DAY, Priority.A, Sport.TRAIL, 30_000, 14_400)
        store.replace(flat)
        got = store.get(GoalId("a"))
        assert isinstance(got, RaceGoal)
        assert got.ascent_m == 0
    finally:
        database.close()
