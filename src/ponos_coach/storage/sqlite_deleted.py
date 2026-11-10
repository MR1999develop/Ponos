# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""The deleted activities (tombstones) on SQLite (`DeletedActivities` port).

Table `deleted_activity`, on the same `SqliteDatabase` as the index: the transaction of
`SqliteActivityIndex` also covers this store. Only `?` parameters; no text from the database ends up
in the error messages.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from datetime import datetime, timedelta
from pathlib import PurePosixPath
from typing import Any, Final

from ponos_coach.activities.domain import (
    DUPLICATE_START_TOLERANCE,
    ActivityIdentity,
    ActivityOrigin,
    ArchivedFile,
    DeletedActivity,
    FileFormat,
    GarminActivityId,
    Sport,
    identity_from_key,
    identity_key,
    is_possible_duplicate,
)
from ponos_coach.activities.ports import (
    ActivityIndexError,
    ActivityNotFoundError,
    DuplicateActivityError,
)
from ponos_coach.storage.sqlite_database import SqliteDatabase
from ponos_coach.storage.sqlite_stores import floor_ms, from_ms, to_ms, translating

_WINDOW_MS: Final = DUPLICATE_START_TOLERANCE // timedelta(milliseconds=1) + 1
_COLUMNS: Final = (
    "identity_key, file_format, sport, start_ms, elapsed_s, distance_m, archive_path, "
    "archive_sha256, archive_size_bytes, origin, imported_ms, garmin_activity_id, deleted_ms"
)
# Only module constants in the composed queries: no external data (S608).
_SELECT: Final = "SELECT " + _COLUMNS + " FROM deleted_activity "  # noqa: S608
_INSERT: Final = (
    "INSERT INTO deleted_activity (" + _COLUMNS + ") VALUES (" + ", ".join("?" * 13) + ")"  # noqa: S608
)


def _params(deleted: DeletedActivity) -> tuple[Any, ...]:
    archived = deleted.archived
    return (
        identity_key(deleted.identity),
        deleted.file_format.value,
        deleted.sport.value,
        to_ms(deleted.start),
        deleted.elapsed_s,
        deleted.distance_m,
        str(archived.relative_path),
        archived.sha256_hex,
        archived.size_bytes,
        deleted.origin.value,
        to_ms(deleted.imported_at),
        deleted.garmin_activity_id,
        to_ms(deleted.deleted_at),
    )


def _from_row(row: Sequence[Any]) -> DeletedActivity:
    return DeletedActivity(
        identity=identity_from_key(row[0]),
        file_format=FileFormat(row[1]),
        sport=Sport(row[2]),
        start=from_ms(row[3]),
        elapsed_s=row[4],
        distance_m=row[5],
        archived=ArchivedFile(PurePosixPath(row[6]), row[7], row[8]),
        origin=ActivityOrigin(row[9]),
        imported_at=from_ms(row[10]),
        garmin_activity_id=row[11],
        deleted_at=from_ms(row[12]),
    )


def _deleted_from(rows: Sequence[Sequence[Any]]) -> list[DeletedActivity]:
    try:
        return [_from_row(row) for row in rows]
    except ValueError as exc:
        raise ActivityIndexError("invalid deleted-activity row") from exc


class SqliteDeletedActivities:
    """Deleted activities (`DeletedActivities` port) on SQLite."""

    def __init__(self, database: SqliteDatabase) -> None:
        self._db = database

    def add(self, deleted: DeletedActivity) -> None:
        """Records a deletion; the same identity or Garmin id twice is `DuplicateActivityError`;
        an identity still in the index is `ActivityIndexError` (the trigger refuses it)."""
        params = _params(deleted)
        try:
            self._db.connection.execute(_INSERT, params)
        except sqlite3.IntegrityError as exc:
            code = getattr(exc, "sqlite_errorcode", None)
            if code in (sqlite3.SQLITE_CONSTRAINT_PRIMARYKEY, sqlite3.SQLITE_CONSTRAINT_UNIQUE):
                raise DuplicateActivityError("activity already deleted") from exc
            raise ActivityIndexError("deletion rejected by the database constraints") from exc
        except sqlite3.Error as exc:
            raise ActivityIndexError("database operation failed") from exc

    def remove(self, identity: ActivityIdentity) -> None:
        """Forgets a deletion; an identity that is not deleted is `ActivityNotFoundError`."""
        with translating(ActivityIndexError):
            cursor = self._db.connection.execute(
                "DELETE FROM deleted_activity WHERE identity_key = ?", (identity_key(identity),)
            )
        if cursor.rowcount == 0:
            raise ActivityNotFoundError("activity is not deleted")

    def get(self, identity: ActivityIdentity) -> DeletedActivity | None:
        """The tombstone with this identity, or None."""
        with translating(ActivityIndexError):
            row = self._db.connection.execute(
                _SELECT + "WHERE identity_key = ?", (identity_key(identity),)
            ).fetchone()
        return None if row is None else _deleted_from([row])[0]

    def find_by_garmin_id(self, garmin_id: GarminActivityId) -> DeletedActivity | None:
        """The tombstone of the activity that had this Garmin id, or None."""
        with translating(ActivityIndexError):
            row = self._db.connection.execute(
                _SELECT + "WHERE garmin_activity_id = ?", (garmin_id.value,)
            ).fetchone()
        return None if row is None else _deleted_from([row])[0]

    def find_possible_duplicates(
        self, start: datetime, elapsed_s: float
    ) -> Sequence[DeletedActivity]:
        """SQL prefilter on the start (a slightly wider window), then `is_possible_duplicate`."""
        centre = floor_ms(start)
        with translating(ActivityIndexError):
            rows = self._db.connection.execute(
                _SELECT + "WHERE start_ms BETWEEN ? AND ? ORDER BY start_ms, identity_key",
                (centre - _WINDOW_MS, centre + _WINDOW_MS),
            ).fetchall()
        return [
            deleted
            for deleted in _deleted_from(rows)
            if is_possible_duplicate(start, elapsed_s, deleted.start, deleted.elapsed_s)
        ]

    def list_latest_first(self) -> Sequence[DeletedActivity]:
        """All tombstones, the most recently deleted first (ties by identity key)."""
        with translating(ActivityIndexError):
            rows = self._db.connection.execute(
                _SELECT + "ORDER BY deleted_ms DESC, identity_key"
            ).fetchall()
        return _deleted_from(rows)
