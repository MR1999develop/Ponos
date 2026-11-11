# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""SQLite store of the threshold test history.

Same connection as the other stores: the index transaction covers it too. Only `?` parameters.
These are health-related data: never in error messages.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from datetime import date

from ponos_coach.activities.domain import ActivityIdentity, identity_from_key, identity_key
from ponos_coach.assessment.domain import ThresholdProtocol, ThresholdTest
from ponos_coach.assessment.ports import (
    AssessmentStoreError,
    ThresholdTestAlreadyStoredError,
    ThresholdTestNotFoundError,
)
from ponos_coach.storage.sqlite_database import SqliteDatabase
from ponos_coach.storage.sqlite_stores import from_ms, to_ms

# Only module constants in the composed queries: no external data (S608).
_COLUMNS = (
    "activity_key, test_date, segment_start_ms, threshold_bpm, pace_s_per_km, "
    "method_version, protocol"
)
_SELECT = "SELECT " + _COLUMNS + " FROM threshold_test "  # noqa: S608
_INSERT = "INSERT INTO threshold_test (" + _COLUMNS + ") VALUES (?, ?, ?, ?, ?, ?, ?)"  # noqa: S608


def _test_from(row: Sequence[object]) -> ThresholdTest:
    """A history entry from a row; a column of the wrong type or an invalid value is a
    `ValueError`."""
    key, day, start_ms, bpm, pace, version, protocol = row
    if not (
        isinstance(key, str)
        and isinstance(day, str)
        and isinstance(start_ms, int)
        and isinstance(bpm, int)
        and isinstance(version, int)
        and isinstance(protocol, str)
        and (pace is None or isinstance(pace, int))
    ):
        raise ValueError("unexpected column types")
    return ThresholdTest(
        identity_from_key(key),
        date.fromisoformat(day),
        from_ms(start_ms),
        bpm,
        pace,
        version,
        ThresholdProtocol(protocol),
    )


class SqliteThresholdTestStore:
    """Threshold test history (`ThresholdTestStore` port) on SQLite."""

    def __init__(self, database: SqliteDatabase) -> None:
        self._db = database

    def add(self, test: ThresholdTest) -> None:
        """Saves a test; the same activity twice is `ThresholdTestAlreadyStoredError`."""
        try:
            self._db.connection.execute(
                _INSERT,
                (
                    identity_key(test.identity),
                    test.test_day.isoformat(),
                    to_ms(test.segment_start),
                    test.threshold_bpm,
                    test.pace_s_per_km,
                    test.method_version,
                    test.protocol.value,
                ),
            )
        except sqlite3.IntegrityError as exc:
            if getattr(exc, "sqlite_errorcode", None) in (
                sqlite3.SQLITE_CONSTRAINT_PRIMARYKEY,
                sqlite3.SQLITE_CONSTRAINT_UNIQUE,
            ):
                raise ThresholdTestAlreadyStoredError("the activity is already a test") from exc
            raise AssessmentStoreError("writing the threshold test failed") from exc
        except sqlite3.Error as exc:
            raise AssessmentStoreError("writing the threshold test failed") from exc

    def remove(self, identity: ActivityIdentity) -> None:
        """Deletes the test made on this activity."""
        try:
            cursor = self._db.connection.execute(
                "DELETE FROM threshold_test WHERE activity_key = ?", (identity_key(identity),)
            )
        except sqlite3.Error as exc:
            raise AssessmentStoreError("deleting the threshold test failed") from exc
        if cursor.rowcount == 0:
            raise ThresholdTestNotFoundError("the activity is not a test")

    def get(self, identity: ActivityIdentity) -> ThresholdTest | None:
        """The test made on this activity, or None."""
        try:
            row = self._db.connection.execute(
                _SELECT + "WHERE activity_key = ?", (identity_key(identity),)
            ).fetchone()
        except sqlite3.Error as exc:
            raise AssessmentStoreError("reading the threshold test failed") from exc
        return None if row is None else self._decoded([row])[0]

    def list_tests(self) -> Sequence[ThresholdTest]:
        """All the tests by ascending day, then segment start, then activity key."""
        try:
            rows = self._db.connection.execute(
                _SELECT + "ORDER BY test_date, segment_start_ms, activity_key"
            ).fetchall()
        except sqlite3.Error as exc:
            raise AssessmentStoreError("reading the threshold tests failed") from exc
        return self._decoded(rows)

    @staticmethod
    def _decoded(rows: Sequence[Sequence[object]]) -> list[ThresholdTest]:
        try:
            return [_test_from(row) for row in rows]
        except (ValueError, OverflowError, OSError):  # incl. an instant out of datetime's range;
            # the value read is not kept in the chain of causes
            raise AssessmentStoreError("saved threshold test invalid") from None
