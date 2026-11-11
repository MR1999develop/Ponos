# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""T-DATI-01 for the weekly availability: from the v11 database (minutes per day) to v12 (days,
sessions per week, long day, weekly maximum), in the same transaction as the other migrations."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from ponos_coach.goals.domain import WeeklyAvailability
from ponos_coach.profiles.domain import PROFILE_DATABASE_NAME
from ponos_coach.storage import sqlite_schema
from ponos_coach.storage.sqlite_profile import SqliteProfileStorage
from tests.collaudo.test_database_del_profilo import rows
from tests.collaudo.test_fase3_migrazione import user_version

V11 = 11


def write_v11(folder: Path, minutes: tuple[int, ...], long_day: int, max_week_min: int) -> Path:
    """A v11 profile with an availability row (the plan and the goals are left empty)."""
    path = folder / PROFILE_DATABASE_NAME
    with closing(sqlite3.connect(path)) as raw:
        for statements in sqlite_schema.MIGRATIONS[:V11]:
            for statement in statements:
                raw.execute(statement)
        raw.executemany(
            "INSERT INTO meta (key, value) VALUES (?, ?)",
            [
                ("profile_id", "00000000-0000-4000-8000-0000000000aa"),
                ("name", "Atleta di prova"),
                ("timezone", "Europe/Rome"),
            ],
        )
        raw.execute(
            "INSERT INTO weekly_availability VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (*minutes, long_day, max_week_min),
        )
        raw.execute("INSERT INTO session_choice VALUES ('running', 'easy')")
        raw.execute(f"PRAGMA user_version = {V11}")
        raw.commit()
    return path


def availability_after(folder: Path) -> WeeklyAvailability | None:
    opened = SqliteProfileStorage().open(folder)
    try:
        return opened.availability.get()
    finally:
        opened.close()


@pytest.mark.parametrize(
    ("minutes", "long_day", "expected"),
    [
        ((60, 0, 60, 0, 60, 90, 120), 6, WeeklyAvailability(frozenset({0, 2, 4, 5, 6}), 5, 6, 540)),
        ((60,) * 7, 3, WeeklyAvailability(frozenset(range(7)), 7, 3, 540)),
        # the long day had no time: it becomes one of the days
        ((60, 60, 60, 60, 60, 60, 0), 6, WeeklyAvailability(frozenset(range(7)), 7, 6, 540)),
        ((0, 0, 0, 0, 0, 90, 0), 1, WeeklyAvailability(frozenset({1, 5}), 2, 1, 540)),
        # no minutes at all meant "nothing available": the empty availability
        ((0,) * 7, 6, WeeklyAvailability(frozenset(), 0, 6, 0)),
    ],
    ids=["some days", "every day", "long day without time", "two days", "no day"],
)
def test_minutes_per_day_become_days_and_sessions(
    tmp_path: Path,
    minutes: tuple[int, ...],
    long_day: int,
    expected: WeeklyAvailability,
) -> None:
    write_v11(tmp_path, minutes, long_day, 540)

    assert availability_after(tmp_path) == expected


def test_the_migration_keeps_the_rest_makes_a_copy_and_sets_the_version(tmp_path: Path) -> None:
    path = write_v11(tmp_path, (60, 0, 60, 0, 60, 90, 120), 6, 540)

    availability_after(tmp_path)

    assert user_version(path) == sqlite_schema.SCHEMA_VERSION
    assert rows(path, "session_choice") == [("running", "easy")]
    [copy] = sorted((tmp_path / "backup").glob("*"))
    assert copy.name == f"ponos-v11-v{sqlite_schema.SCHEMA_VERSION}.sqlite3"
    assert user_version(copy) == V11
    with closing(sqlite3.connect(copy)) as raw:
        assert raw.execute("SELECT day2_min FROM weekly_availability").fetchone() == (60,)


def test_a_profile_without_availability_stays_without(tmp_path: Path) -> None:
    path = write_v11(tmp_path, (60,) * 7, 6, 540)
    with closing(sqlite3.connect(path)) as raw:
        raw.execute("DELETE FROM weekly_availability")
        raw.commit()

    assert availability_after(tmp_path) is None


def test_the_saved_availability_is_read_back_as_written(tmp_path: Path) -> None:
    write_v11(tmp_path, (60,) * 7, 6, 540)
    mine = WeeklyAvailability(frozenset({1, 3, 6}), 2, 3, 300)
    opened = SqliteProfileStorage().open(tmp_path)
    try:
        opened.availability.set(mine)
        assert opened.availability.get() == mine
    finally:
        opened.close()
    assert availability_after(tmp_path) == mine
