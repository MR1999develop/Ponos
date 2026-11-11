# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""SQLite stores of the goals, the weekly availability and the chosen session types.

Same connection as the other stores: the index transaction covers them too. Only `?` parameters.
These are health-related data: never in error messages.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from datetime import date

from ponos_coach.activities.domain import Sport
from ponos_coach.goals.domain import (
    FitnessGoal,
    GoalId,
    Priority,
    RaceGoal,
    SessionChoices,
    SessionType,
    WeeklyAvailability,
)
from ponos_coach.goals.domain.availability import WEEKDAYS
from ponos_coach.goals.ports import (
    Goal,
    GoalAlreadyExistsError,
    GoalNotFoundError,
    GoalStoreError,
    RaceDayTakenError,
)
from ponos_coach.storage.sqlite_database import SqliteDatabase

_KIND_RACE = "race"
_KIND_FITNESS = "fitness"
# Only module constants in the composed queries: no external data (S608).
_COLUMNS = "goal_id, kind, goal_date, priority, sport, distance_m, target_time_s, ctl, ascent_m"
_SELECT = "SELECT " + _COLUMNS + " FROM goal "  # noqa: S608
_INSERT = "INSERT INTO goal (" + _COLUMNS + ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"  # noqa: S608
_UPDATE = (
    "UPDATE goal SET kind = ?, goal_date = ?, priority = ?, sport = ?, distance_m = ?, "
    "target_time_s = ?, ctl = ?, ascent_m = ? WHERE goal_id = ?"
)
_AVAILABILITY_COLUMNS = ("days_mask", "sessions_per_week", "long_day", "max_week_min")
_AVAILABILITY_SELECT = (
    "SELECT " + ", ".join(_AVAILABILITY_COLUMNS) + " FROM weekly_availability WHERE id = 1"  # noqa: S608
)
_AVAILABILITY_UPSERT = (
    "INSERT INTO weekly_availability (id, "  # noqa: S608
    + ", ".join(_AVAILABILITY_COLUMNS)
    + ") VALUES (1, "
    + ", ".join("?" for _ in _AVAILABILITY_COLUMNS)
    + ") ON CONFLICT (id) DO UPDATE SET "
    + ", ".join(f"{c} = excluded.{c}" for c in _AVAILABILITY_COLUMNS)
)


def _columns_of(goal: Goal) -> tuple[object, ...]:
    """The values of a goal after its id: kind, date, priority, sport, distance, time, CTL and
    ascent."""
    if isinstance(goal, RaceGoal):
        return (
            _KIND_RACE,
            goal.day.isoformat(),
            goal.priority.value,
            goal.sport.value,
            goal.distance_m,
            goal.target_time_s,
            None,
            goal.ascent_m,
        )
    return (_KIND_FITNESS, goal.day.isoformat(), None, None, None, None, goal.ctl, 0)


def _goal_from(row: Sequence[object]) -> Goal:
    """A goal from a row; a column of the wrong type or an invalid value is a `ValueError`."""
    goal_id, kind, day, priority, sport, distance, target, ctl, ascent = row
    if not (isinstance(goal_id, str) and isinstance(day, str)):
        raise TypeError("unexpected column types")
    if kind == _KIND_RACE and (
        isinstance(priority, str)
        and isinstance(sport, str)
        and isinstance(distance, int)
        and isinstance(ascent, int)
        and (target is None or isinstance(target, int))
        and ctl is None
    ):
        return RaceGoal(
            GoalId(goal_id),
            date.fromisoformat(day),
            Priority(priority),
            Sport(sport),
            distance,
            target,
            ascent,
        )
    if kind == _KIND_FITNESS and isinstance(ctl, int | float):
        return FitnessGoal(GoalId(goal_id), date.fromisoformat(day), float(ctl))
    raise ValueError("unexpected column types")


def _write_error(exc: sqlite3.Error, *, adding: bool) -> GoalStoreError:
    """The port's error for a failed write of a goal."""
    code = getattr(exc, "sqlite_errorcode", None)
    if adding and code in (sqlite3.SQLITE_CONSTRAINT_PRIMARYKEY, sqlite3.SQLITE_CONSTRAINT_UNIQUE):
        # A text primary key and the partial index both are "unique": the message tells them apart
        # without keeping any value.
        if "goal_id" in str(exc):
            return GoalAlreadyExistsError("a goal with this id exists")
        return RaceDayTakenError("another race is on this day")
    if code == sqlite3.SQLITE_CONSTRAINT_UNIQUE:
        return RaceDayTakenError("another race is on this day")
    return GoalStoreError("writing the goal failed")


class SqliteGoalStore:
    """The goals (`GoalStore` port) on SQLite; the partial unique index keeps one race a day."""

    def __init__(self, database: SqliteDatabase) -> None:
        self._db = database

    def add(self, goal: Goal) -> None:
        """Saves a new goal."""
        try:
            self._db.connection.execute(_INSERT, (goal.goal_id.value, *_columns_of(goal)))
        except sqlite3.Error as exc:
            raise _write_error(exc, adding=True) from exc

    def replace(self, goal: Goal) -> None:
        """Replaces the goal with the same id."""
        try:
            cursor = self._db.connection.execute(_UPDATE, (*_columns_of(goal), goal.goal_id.value))
        except sqlite3.Error as exc:
            raise _write_error(exc, adding=False) from exc
        if cursor.rowcount == 0:
            raise GoalNotFoundError("no goal with this id")

    def remove(self, goal_id: GoalId) -> None:
        """Deletes the goal."""
        try:
            cursor = self._db.connection.execute(
                "DELETE FROM goal WHERE goal_id = ?", (goal_id.value,)
            )
        except sqlite3.Error as exc:
            raise GoalStoreError("deleting the goal failed") from exc
        if cursor.rowcount == 0:
            raise GoalNotFoundError("no goal with this id")

    def get(self, goal_id: GoalId) -> Goal | None:
        """The goal with that id, or None."""
        try:
            row = self._db.connection.execute(
                _SELECT + "WHERE goal_id = ?", (goal_id.value,)
            ).fetchone()
        except sqlite3.Error as exc:
            raise GoalStoreError("reading the goal failed") from exc
        return None if row is None else self._decoded([row])[0]

    def list_goals(self) -> Sequence[Goal]:
        """All the goals by ascending date, then id."""
        try:
            rows = self._db.connection.execute(_SELECT + "ORDER BY goal_date, goal_id").fetchall()
        except sqlite3.Error as exc:
            raise GoalStoreError("reading the goals failed") from exc
        return self._decoded(rows)

    @staticmethod
    def _decoded(rows: Sequence[Sequence[object]]) -> list[Goal]:
        try:
            return [_goal_from(row) for row in rows]
        except (ValueError, TypeError):  # the value read is not kept in the chain of causes
            raise GoalStoreError("saved goal invalid") from None


class SqliteAvailabilityStore:
    """The weekly availability (`AvailabilityStore` port): a single row."""

    def __init__(self, database: SqliteDatabase) -> None:
        self._db = database

    def get(self) -> WeeklyAvailability | None:
        """The availability, or None if not set."""
        try:
            row = self._db.connection.execute(_AVAILABILITY_SELECT).fetchone()
        except sqlite3.Error as exc:
            raise GoalStoreError("reading the availability failed") from exc
        if row is None:
            return None
        if not all(isinstance(value, int) for value in row):
            raise GoalStoreError("saved availability invalid")
        try:
            mask, sessions, long_day, max_week_min = row
            days = frozenset(day for day in range(WEEKDAYS) if (mask >> day) & 1)
            return WeeklyAvailability(days, sessions, long_day, max_week_min)
        except ValueError:
            raise GoalStoreError("saved availability invalid") from None

    def set(self, availability: WeeklyAvailability | None) -> None:
        """Saves the availability; None forgets it."""
        try:
            if availability is None:
                self._db.connection.execute("DELETE FROM weekly_availability WHERE id = 1")
                return
            values = (
                sum(1 << day for day in availability.days),
                availability.sessions_per_week,
                availability.long_day,
                availability.max_week_minutes,
            )
            self._db.connection.execute(_AVAILABILITY_UPSERT, values)
        except sqlite3.Error as exc:
            raise GoalStoreError("writing the availability failed") from exc


class SqliteSessionChoicesStore:
    """The chosen session types (`SessionChoicesStore` port): a row per sport and type."""

    def __init__(self, database: SqliteDatabase) -> None:
        self._db = database

    def get(self) -> SessionChoices:
        """The choices (empty if none)."""
        try:
            rows = self._db.connection.execute(
                "SELECT sport, session_type FROM session_choice"
            ).fetchall()
        except sqlite3.Error as exc:
            raise GoalStoreError("reading the session types failed") from exc
        try:
            chosen: dict[Sport, set[SessionType]] = {}
            for sport, session_type in rows:
                chosen.setdefault(Sport(sport), set()).add(SessionType(session_type))
        except ValueError:
            raise GoalStoreError("saved session type invalid") from None
        return SessionChoices.of(chosen)

    def replace(self, choices: SessionChoices) -> None:
        """Replaces all the choices, all or nothing."""
        rows = [
            (sport.value, session_type.value)
            for sport, types in choices.by_sport
            for session_type in sorted(types, key=lambda t: t.value)
        ]
        try:
            with self._db.atomic():
                self._db.connection.execute("DELETE FROM session_choice")
                self._db.connection.executemany(
                    "INSERT INTO session_choice (sport, session_type) VALUES (?, ?)", rows
                )
        except sqlite3.Error as exc:
            raise GoalStoreError("writing the session types failed") from exc
