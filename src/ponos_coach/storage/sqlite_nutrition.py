# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""SQLite stores of the nutrition settings: activity level, meal times, goal periods.

Same connection as the other stores: the index transaction covers these too. Only `?`
parameters. These are health-related data: never in error messages.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from datetime import date

from ponos_coach.nutrition.domain import (
    ActivityLevel,
    ActivityLevelEntry,
    GoalPeriod,
    LeanMass,
    LeanMassEntry,
    MaintenanceGoal,
    MealTimes,
    NutritionGoal,
    PerformanceGoal,
    WeightLossGoal,
)
from ponos_coach.nutrition.ports import (
    ActivityLevelNotFoundError,
    GoalPeriodNotFoundError,
    GoalPeriodOverlapError,
    LeanMassNotFoundError,
    NutritionStoreError,
)
from ponos_coach.storage.sqlite_database import SqliteDatabase

_KIND_LOSS = "weight_loss"
_KIND_MAINTENANCE = "maintenance"
_KIND_PERFORMANCE = "performance"


class SqliteActivityLevelStore:
    """Activity level series (`ActivityLevelStore` port) on SQLite."""

    def __init__(self, database: SqliteDatabase) -> None:
        self._db = database

    def put(self, entry: ActivityLevelEntry) -> None:
        """Saves the level valid from `entry.valid_from`, replacing the same date."""
        try:
            self._db.connection.execute(
                "INSERT INTO activity_level (valid_from, level) VALUES (?, ?) "
                "ON CONFLICT (valid_from) DO UPDATE SET level = excluded.level",
                (entry.valid_from.isoformat(), entry.level.value),
            )
        except sqlite3.Error as exc:
            raise NutritionStoreError("writing the activity level failed") from exc

    def remove(self, valid_from: date) -> None:
        """Deletes the entry that starts on `valid_from`."""
        try:
            cursor = self._db.connection.execute(
                "DELETE FROM activity_level WHERE valid_from = ?", (valid_from.isoformat(),)
            )
        except sqlite3.Error as exc:
            raise NutritionStoreError("deleting the activity level failed") from exc
        if cursor.rowcount == 0:
            raise ActivityLevelNotFoundError("no activity level starts on that day")

    def list_entries(self) -> Sequence[ActivityLevelEntry]:
        """All the entries by ascending date."""
        try:
            rows = self._db.connection.execute(
                "SELECT valid_from, level FROM activity_level ORDER BY valid_from"
            ).fetchall()
        except sqlite3.Error as exc:
            raise NutritionStoreError("reading the activity level failed") from exc
        try:
            return [ActivityLevelEntry(date.fromisoformat(r[0]), ActivityLevel(r[1])) for r in rows]
        except ValueError:  # the text read is not kept in the chain of causes
            raise NutritionStoreError("saved activity level invalid") from None


class SqliteLeanMassStore:
    """Measured lean mass series (`LeanMassStore` port) on SQLite."""

    def __init__(self, database: SqliteDatabase) -> None:
        self._db = database

    def put(self, entry: LeanMassEntry) -> None:
        """Saves the lean mass valid from `entry.valid_from`, replacing the same date."""
        try:
            self._db.connection.execute(
                "INSERT INTO lean_mass (valid_from, tenths_kg) VALUES (?, ?) "
                "ON CONFLICT (valid_from) DO UPDATE SET tenths_kg = excluded.tenths_kg",
                (entry.valid_from.isoformat(), entry.lean_mass.tenths_kg),
            )
        except sqlite3.Error as exc:
            raise NutritionStoreError("writing the lean mass failed") from exc

    def remove(self, valid_from: date) -> None:
        """Deletes the entry that starts on `valid_from`."""
        try:
            cursor = self._db.connection.execute(
                "DELETE FROM lean_mass WHERE valid_from = ?", (valid_from.isoformat(),)
            )
        except sqlite3.Error as exc:
            raise NutritionStoreError("deleting the lean mass failed") from exc
        if cursor.rowcount == 0:
            raise LeanMassNotFoundError("no lean mass starts on that day")

    def list_entries(self) -> Sequence[LeanMassEntry]:
        """All the entries by ascending date."""
        try:
            rows = self._db.connection.execute(
                "SELECT valid_from, tenths_kg FROM lean_mass ORDER BY valid_from"
            ).fetchall()
        except sqlite3.Error as exc:
            raise NutritionStoreError("reading the lean mass failed") from exc
        try:
            return [LeanMassEntry(date.fromisoformat(r[0]), LeanMass(r[1])) for r in rows]
        except (ValueError, TypeError):  # the value read is not kept in the chain of causes
            raise NutritionStoreError("saved lean mass invalid") from None


class SqliteMealTimesStore:
    """Typical meal times (`MealTimesStore` port): one row, absent until first set."""

    def __init__(self, database: SqliteDatabase) -> None:
        self._db = database

    def get(self) -> MealTimes | None:
        """The saved times, or None."""
        try:
            row = self._db.connection.execute(
                "SELECT breakfast_min, lunch_min, dinner_min FROM meal_times WHERE id = 1"
            ).fetchone()
        except sqlite3.Error as exc:
            raise NutritionStoreError("reading the meal times failed") from exc
        if row is None:
            return None
        try:
            return MealTimes.from_minutes(row[0], row[1], row[2])
        except ValueError:
            raise NutritionStoreError("saved meal times invalid") from None

    def set(self, times: MealTimes) -> None:
        """Saves the times, replacing the previous ones."""
        breakfast, lunch, dinner = times.minutes
        try:
            self._db.connection.execute(
                "INSERT INTO meal_times (id, breakfast_min, lunch_min, dinner_min) "
                "VALUES (1, ?, ?, ?) ON CONFLICT (id) DO UPDATE SET "
                "breakfast_min = excluded.breakfast_min, lunch_min = excluded.lunch_min, "
                "dinner_min = excluded.dinner_min",
                (breakfast, lunch, dinner),
            )
        except sqlite3.Error as exc:
            raise NutritionStoreError("writing the meal times failed") from exc


def _goal_columns(goal: NutritionGoal) -> tuple[str, int | None]:
    if isinstance(goal, WeightLossGoal):
        return _KIND_LOSS, goal.weekly_loss_g
    if isinstance(goal, PerformanceGoal):
        return _KIND_PERFORMANCE, None
    return _KIND_MAINTENANCE, None


def _goal_from(kind: str, weekly_loss_g: int | None) -> NutritionGoal:
    if kind == _KIND_LOSS and weekly_loss_g is not None:
        return WeightLossGoal(weekly_loss_g)
    if kind == _KIND_PERFORMANCE and weekly_loss_g is None:
        return PerformanceGoal()
    if kind == _KIND_MAINTENANCE and weekly_loss_g is None:
        return MaintenanceGoal()
    raise ValueError("inconsistent goal columns")


class SqliteGoalPeriodStore:
    """Goal periods (`GoalPeriodStore` port); the trigger rejects overlapping ones."""

    def __init__(self, database: SqliteDatabase) -> None:
        self._db = database

    def list_periods(self) -> Sequence[GoalPeriod]:
        """All the periods by ascending first day."""
        try:
            rows = self._db.connection.execute(
                "SELECT start_date, end_date, kind, weekly_loss_g FROM nutrition_goal "
                "ORDER BY start_date"
            ).fetchall()
        except sqlite3.Error as exc:
            raise NutritionStoreError("reading the goal periods failed") from exc
        try:
            return [
                GoalPeriod(
                    date.fromisoformat(r[0]),
                    None if r[1] is None else date.fromisoformat(r[1]),
                    _goal_from(r[2], r[3]),
                )
                for r in rows
            ]
        except ValueError:  # the text read is not kept in the chain of causes
            raise NutritionStoreError("saved goal period invalid") from None

    def add(self, period: GoalPeriod) -> None:
        """Saves a period; one that overlaps a saved one is `GoalPeriodOverlapError`."""
        kind, weekly_loss_g = _goal_columns(period.goal)
        try:
            self._db.connection.execute(
                "INSERT INTO nutrition_goal (start_date, end_date, kind, weekly_loss_g) "
                "VALUES (?, ?, ?, ?)",
                (
                    period.first_day.isoformat(),
                    None if period.last_day is None else period.last_day.isoformat(),
                    kind,
                    weekly_loss_g,
                ),
            )
        except sqlite3.IntegrityError as exc:
            if getattr(exc, "sqlite_errorcode", None) in (
                sqlite3.SQLITE_CONSTRAINT_TRIGGER,
                sqlite3.SQLITE_CONSTRAINT_PRIMARYKEY,
                sqlite3.SQLITE_CONSTRAINT_UNIQUE,
            ):
                raise GoalPeriodOverlapError("the period overlaps a saved one") from exc
            raise NutritionStoreError("writing the goal period failed") from exc
        except sqlite3.Error as exc:
            raise NutritionStoreError("writing the goal period failed") from exc

    def remove(self, first_day: date) -> None:
        """Deletes the period that starts on `first_day`."""
        try:
            cursor = self._db.connection.execute(
                "DELETE FROM nutrition_goal WHERE start_date = ?", (first_day.isoformat(),)
            )
        except sqlite3.Error as exc:
            raise NutritionStoreError("deleting the goal period failed") from exc
        if cursor.rowcount == 0:
            raise GoalPeriodNotFoundError("no period starts on that day")
