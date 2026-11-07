# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""T-DATI-01 for phase 4b: from the frozen v3 database to v4 (calories, nutrition settings).

The v3 database below is written by hand, independent of the code (the released migrations v1 and
v2 are in `test_database_del_profilo.py`, v3 in `test_fase3_migrazione.py`). It is shown that v4
does not change after release, that a v3 profile migrates without losing anything, that the
migration is one transaction (killed at every statement the database is still v3) and that the
database itself refuses what the domain would refuse.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import date, time
from pathlib import Path

import pytest

from ponos_coach.nutrition.domain import (
    ActivityLevel,
    ActivityLevelEntry,
    GoalPeriod,
    MealTimes,
    PerformanceGoal,
    WeightLossGoal,
)
from ponos_coach.profiles.domain import PROFILE_DATABASE_NAME, Weight, WeightEntry
from ponos_coach.profiles.ports import ProfileStorageError
from ponos_coach.storage import sqlite_database, sqlite_schema
from ponos_coach.storage.sqlite_profile import SqliteProfileStorage
from tests.collaudo.test_database_del_profilo import rows, squash
from tests.collaudo.test_fase3_dati_personali import run_worker_until_death
from tests.collaudo.test_fase3_migrazione import (
    FROZEN_V3,
    table_names,
    user_version,
    write_frozen_v2,
)
from tests.fixtures.generators.collaudo_fase3_worker import DIED

# Migration v4 as it was released. It does not change, like v1, v2 and v3.
FROZEN_V4 = (
    """ALTER TABLE activities ADD COLUMN calories_kcal REAL
        CHECK (calories_kcal IS NULL OR (calories_kcal > 0 AND calories_kcal <= 50000))""",
    """CREATE TABLE activity_level (
        valid_from TEXT NOT NULL PRIMARY KEY
            CHECK (date(valid_from) IS valid_from AND valid_from >= '1900-01-01'),
        level TEXT NOT NULL CHECK (level IN ('sedentary','light','active','very_active'))
    ) STRICT""",
    """CREATE TABLE meal_times (
        id INTEGER NOT NULL PRIMARY KEY CHECK (id = 1),
        breakfast_min INTEGER NOT NULL CHECK (breakfast_min BETWEEN 0 AND 1439),
        lunch_min INTEGER NOT NULL CHECK (lunch_min BETWEEN 0 AND 1439),
        dinner_min INTEGER NOT NULL CHECK (dinner_min BETWEEN 0 AND 1439),
        CHECK (lunch_min - breakfast_min >= 120),
        CHECK (dinner_min - lunch_min >= 120)
    ) STRICT""",
    """CREATE TABLE nutrition_goal (
        start_date TEXT NOT NULL PRIMARY KEY
            CHECK (date(start_date) IS start_date AND start_date >= '1900-01-01'),
        end_date TEXT CHECK (end_date IS NULL
            OR (date(end_date) IS end_date AND end_date >= start_date)),
        kind TEXT NOT NULL CHECK (kind IN ('weight_loss','maintenance','performance')),
        weekly_loss_g INTEGER CHECK (weekly_loss_g IS NULL
            OR weekly_loss_g BETWEEN 100 AND 1000),
        CHECK ((kind = 'weight_loss') = (weekly_loss_g IS NOT NULL))
    ) STRICT""",
    """CREATE TRIGGER nutrition_goal_no_overlap BEFORE INSERT ON nutrition_goal
    WHEN EXISTS (
        SELECT 1 FROM nutrition_goal AS g
        WHERE g.start_date <= COALESCE(NEW.end_date, '9999-12-31')
          AND COALESCE(g.end_date, '9999-12-31') >= NEW.start_date
    )
    BEGIN
        SELECT RAISE(ABORT, 'nutrition goal periods overlap');
    END""",
    """CREATE TRIGGER nutrition_goal_no_overlap_on_update BEFORE UPDATE ON nutrition_goal
    WHEN EXISTS (
        SELECT 1 FROM nutrition_goal AS g
        WHERE g.start_date <> OLD.start_date
          AND g.start_date <= COALESCE(NEW.end_date, '9999-12-31')
          AND COALESCE(g.end_date, '9999-12-31') >= NEW.start_date
    )
    BEGIN
        SELECT RAISE(ABORT, 'nutrition goal periods overlap');
    END""",
)

V4_TABLES = {"activity_level", "meal_times", "nutrition_goal"}


def write_frozen_v3(path: Path) -> None:
    """A v3 database as the phase 3 app wrote it, with data in every table (without `src`)."""
    write_frozen_v2(path)
    with closing(sqlite3.connect(path)) as raw:
        for statement in FROZEN_V3:
            raw.execute(statement)
        raw.execute("INSERT INTO profile_body VALUES (1, 172, '1990-05-17')")
        raw.execute("INSERT INTO weight VALUES ('2026-01-01', 705)")
        raw.execute("INSERT INTO sport_weights VALUES ('cycling', 0.9)")
        raw.execute(
            "INSERT INTO load_cache VALUES (1, ?, 80.0, 70.0, 75.0, 3500, 3, 12.5, 'heart_rate', "
            "100.0, 200.0, 300.0, 400.0, 500.0)",
            ("ef" * 32,),
        )
        raw.execute("PRAGMA user_version = 3")
        raw.commit()


KEPT = (
    "meta", "physiology", "load_cache", "sync_journal", "sync_cursor", "sync_state",
    "profile_body", "weight", "sport_weights",
)  # fmt: skip


def test_t_dati_01_the_released_v4_migration_has_not_changed() -> None:
    released = tuple(squash(statement) for statement in sqlite_schema.MIGRATIONS[3])

    assert released == tuple(squash(statement) for statement in FROZEN_V4)


def test_t_dati_01_a_frozen_v3_database_is_migrated_to_v4_with_data_intact_and_a_copy(
    tmp_path: Path,
) -> None:
    path = tmp_path / PROFILE_DATABASE_NAME
    write_frozen_v3(path)
    before = {table: rows(path, table) for table in (*KEPT, "activities")}

    opened = SqliteProfileStorage().open(tmp_path)
    try:
        assert opened.info.format_version == sqlite_schema.SCHEMA_VERSION
        (record,) = opened.activities.list_by_start()
        assert record.summary.calories_kcal is None  # the index is derived: not known before
    finally:
        opened.close()

    after = {table: rows(path, table) for table in KEPT}
    assert after == {table: before[table] for table in KEPT}  # the load cache is not emptied
    assert [row[:-1] for row in rows(path, "activities")] == before["activities"]
    assert table_names(path) >= V4_TABLES
    [copy] = sorted((tmp_path / "backup").glob("*"))
    assert copy.name == f"ponos-v3-v{sqlite_schema.SCHEMA_VERSION}.sqlite3"
    assert user_version(copy) == 3
    assert V4_TABLES.isdisjoint(table_names(copy))
    assert {table: rows(copy, table) for table in (*KEPT, "activities")} == before


def test_t_dati_01_a_v3_profile_starts_with_empty_nutrition_settings(tmp_path: Path) -> None:
    write_frozen_v3(tmp_path / PROFILE_DATABASE_NAME)

    opened = SqliteProfileStorage().open(tmp_path)
    try:
        assert opened.activity_levels.list_entries() == []
        assert opened.meal_times.get() is None
        assert opened.goal_periods.list_periods() == []
        assert opened.weights.list_entries() == [WeightEntry(date(2026, 1, 1), Weight(705))]
    finally:
        opened.close()


def test_t_dati_01_a_migrated_profile_saves_and_rereads_the_new_settings(tmp_path: Path) -> None:
    write_frozen_v3(tmp_path / PROFILE_DATABASE_NAME)
    level = ActivityLevelEntry(date(2026, 3, 1), ActivityLevel.ACTIVE)
    times = MealTimes(time(7, 0), time(12, 30), time(19, 30))
    periods = [
        GoalPeriod(date(2026, 3, 1), date(2026, 3, 31), WeightLossGoal(500)),
        GoalPeriod(date(2026, 4, 1), None, PerformanceGoal()),
    ]
    opened = SqliteProfileStorage().open(tmp_path)
    try:
        opened.activity_levels.put(level)
        opened.meal_times.set(times)
        for period in periods:
            opened.goal_periods.add(period)
    finally:
        opened.close()

    reopened = SqliteProfileStorage().open(tmp_path)
    try:
        assert list(reopened.activity_levels.list_entries()) == [level]
        assert reopened.meal_times.get() == times
        assert list(reopened.goal_periods.list_periods()) == periods
    finally:
        reopened.close()


def test_t_dati_01_a_v4_migration_that_fails_halfway_leaves_the_v3_database_as_it_was(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / PROFILE_DATABASE_NAME
    write_frozen_v3(path)
    tables_before = table_names(path)
    broken_v4 = (
        sqlite_schema.MIGRATIONS[3][0],  # a new column that succeeds...
        sqlite_schema.MIGRATIONS[3][1],  # ...a new table...
        "CREATE TABLE questa_no (",  # ...then a syntax error
    )
    monkeypatch.setattr(sqlite_database, "MIGRATIONS", (*sqlite_schema.MIGRATIONS[:3], broken_v4))

    with pytest.raises((ProfileStorageError, sqlite3.Error)):
        SqliteProfileStorage().open(tmp_path)

    assert user_version(path) == 3
    assert table_names(path) == tables_before  # no half table
    with closing(sqlite3.connect(path)) as raw:
        columns = {r[1] for r in raw.execute("PRAGMA table_info(activities)")}
    assert "calories_kcal" not in columns  # not even the new column
    monkeypatch.undo()
    SqliteProfileStorage().open(tmp_path).close()  # the good round succeeds
    assert user_version(path) == sqlite_schema.SCHEMA_VERSION


@pytest.mark.parametrize("kill_at", range(1, len(sqlite_schema.MIGRATIONS[3]) + 1))
def test_t_dati_01_a_v3_database_killed_at_every_migration_statement_is_still_v3_then_migrates(
    tmp_path: Path, kill_at: int
) -> None:
    path = tmp_path / PROFILE_DATABASE_NAME
    write_frozen_v3(path)
    before = {table: rows(path, table) for table in (*KEPT, "activities")}

    assert run_worker_until_death(tmp_path, kill_at) == DIED

    assert user_version(path) == 3  # the transaction was not committed
    assert V4_TABLES.isdisjoint(table_names(path))
    assert {table: rows(path, table) for table in (*KEPT, "activities")} == before
    SqliteProfileStorage().open(tmp_path).close()
    assert user_version(path) == sqlite_schema.SCHEMA_VERSION


# --- The database refuses what the domain refuses -----------------------------------------


def migrated(tmp_path: Path) -> sqlite3.Connection:
    write_frozen_v3(tmp_path / PROFILE_DATABASE_NAME)
    SqliteProfileStorage().open(tmp_path).close()
    return sqlite3.connect(tmp_path / PROFILE_DATABASE_NAME)


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE activities SET calories_kcal = 0",  # off point: positive only
        "UPDATE activities SET calories_kcal = -1",
        "UPDATE activities SET calories_kcal = 50000.5",  # off point: 50000 is the maximum
        "INSERT INTO activity_level VALUES ('2026-01-01', 'sporty')",  # unknown level
        "INSERT INTO activity_level VALUES ('2026-02-30', 'light')",  # non-existent day
        "INSERT INTO activity_level VALUES ('1899-12-31', 'light')",  # before 1900-01-01
        "INSERT INTO meal_times VALUES (2, 450, 780, 1200)",  # a single row
        "INSERT INTO meal_times VALUES (1, 450, 569, 1200)",  # lunch 119 min after breakfast
        "INSERT INTO meal_times VALUES (1, 450, 780, 899)",  # dinner 119 min after lunch
        "INSERT INTO meal_times VALUES (1, 780, 450, 1200)",  # out of order
        "INSERT INTO meal_times VALUES (1, -1, 450, 1200)",  # before midnight
        "INSERT INTO meal_times VALUES (1, 450, 780, 1440)",  # after the last minute
        "INSERT INTO nutrition_goal VALUES ('2026-03-01', NULL, 'diet', NULL)",  # unknown kind
        "INSERT INTO nutrition_goal VALUES ('2026-03-01', NULL, 'weight_loss', NULL)",  # no pace
        "INSERT INTO nutrition_goal VALUES ('2026-03-01', NULL, 'maintenance', 500)",  # pace
        "INSERT INTO nutrition_goal VALUES ('2026-03-01', NULL, 'weight_loss', 99)",  # too slow
        "INSERT INTO nutrition_goal VALUES ('2026-03-01', NULL, 'weight_loss', 1001)",  # too fast
        "INSERT INTO nutrition_goal VALUES ('2026-03-02', '2026-03-01', 'maintenance', NULL)",
        "INSERT INTO nutrition_goal VALUES ('2026-03-01', '2026-04-31', 'maintenance', NULL)",
        "INSERT INTO nutrition_goal VALUES ('1899-12-31', NULL, 'maintenance', NULL)",
    ],
)
def test_the_database_refuses_nutrition_data_outside_the_domain_limits(
    tmp_path: Path, statement: str
) -> None:
    with closing(migrated(tmp_path)) as raw, pytest.raises(sqlite3.IntegrityError):
        raw.execute(statement)


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE activities SET calories_kcal = 0.1",
        "UPDATE activities SET calories_kcal = 50000",  # on the maximum
        "INSERT INTO activity_level VALUES ('1900-01-01', 'very_active')",
        "INSERT INTO meal_times VALUES (1, 0, 120, 240)",  # exactly two hours apart
        "INSERT INTO meal_times VALUES (1, 450, 780, 1439)",
        "INSERT INTO nutrition_goal VALUES ('2026-03-01', '2026-03-01', 'performance', NULL)",
        "INSERT INTO nutrition_goal VALUES ('2026-03-01', NULL, 'weight_loss', 100)",
        "INSERT INTO nutrition_goal VALUES ('2026-03-01', NULL, 'weight_loss', 1000)",
    ],
)
def test_the_database_accepts_nutrition_data_on_the_limits(tmp_path: Path, statement: str) -> None:
    with closing(migrated(tmp_path)) as raw:
        raw.execute(statement)


@pytest.mark.parametrize(
    ("first", "second", "overlaps"),
    [
        (("2026-03-01", "2026-03-31"), ("2026-03-31", "2026-04-30"), True),  # one shared day
        (("2026-03-01", "2026-03-31"), ("2026-04-01", "2026-04-30"), False),  # touching
        (("2026-03-01", "2026-03-31"), ("2026-02-01", "2026-03-01"), True),  # one shared day
        (("2026-03-01", None), ("2030-01-01", None), True),  # open-ended blocks the future
        (("2026-03-01", "2026-03-31"), ("2026-01-01", None), True),  # open-ended around it
        (("2026-03-01", "2026-03-31"), ("2026-03-10", "2026-03-12"), True),  # inside
    ],
)
def test_the_database_itself_rejects_overlapping_goal_periods(
    tmp_path: Path, first: tuple[str, str | None], second: tuple[str, str | None], overlaps: bool
) -> None:
    insert = "INSERT INTO nutrition_goal VALUES (?, ?, 'maintenance', NULL)"
    with closing(migrated(tmp_path)) as raw:
        raw.execute(insert, first)

        if overlaps:
            with pytest.raises(sqlite3.IntegrityError, match="overlap"):
                raw.execute(insert, second)
        else:
            raw.execute(insert, second)


@pytest.mark.parametrize(
    ("new_start", "new_end", "overlaps"),
    [
        ("2026-03-15", "2026-04-25", True),  # slides into the second period
        ("2026-03-01", None, True),  # becomes open-ended over the second
        ("2026-04-01", "2026-04-10", False),  # moves to the free gap
        ("2026-03-01", "2026-03-31", False),  # unchanged by itself
    ],
)
def test_the_database_rejects_a_goal_period_moved_over_another_by_an_update(
    tmp_path: Path, new_start: str, new_end: str | None, overlaps: bool
) -> None:
    insert = "INSERT INTO nutrition_goal VALUES (?, ?, 'maintenance', NULL)"
    update = "UPDATE nutrition_goal SET start_date = ?, end_date = ? WHERE start_date = ?"
    with closing(migrated(tmp_path)) as raw:
        raw.execute(insert, ("2026-03-01", "2026-03-31"))
        raw.execute(insert, ("2026-04-20", "2026-04-30"))

        if overlaps:
            with pytest.raises(sqlite3.IntegrityError, match="overlap"):
                raw.execute(update, (new_start, new_end, "2026-03-01"))
            assert rows_of(raw) == [("2026-03-01", "2026-03-31"), ("2026-04-20", "2026-04-30")]
        else:
            raw.execute(update, (new_start, new_end, "2026-03-01"))


def rows_of(raw: sqlite3.Connection) -> list[tuple[str, str | None]]:
    return list(raw.execute("SELECT start_date, end_date FROM nutrition_goal ORDER BY start_date"))
