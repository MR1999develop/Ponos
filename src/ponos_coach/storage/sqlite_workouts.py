# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""SQLite store of the workouts deleted from the Garmin calendar (`WorkoutBackupStore` port).

Same connection as the other stores. The definition is saved with its SHA-256 and checked when
read: a copy that fails the check is left out of `pending` (it could not be created again
faithfully). Only `?` parameters; no definition text in error messages.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from datetime import date, datetime, timedelta

from ponos_coach.planning.workout_calendar import (
    KEEP_RESTORED_BACKUPS_DAYS,
    MAX_PENDING_BACKUPS,
    NewBackup,
    StoredBackup,
    WorkoutBackupStoreError,
)
from ponos_coach.storage.sqlite_database import SqliteDatabase
from ponos_coach.storage.sqlite_stores import from_ms, to_ms, translating


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _stored(row: Sequence[object]) -> StoredBackup | None:
    """A copy from its row; None if the definition does not match its checksum."""
    backup_id, name, day, payload, sha256, saved_at, restored_at = row
    if (
        not isinstance(backup_id, int)
        or not isinstance(name, str)
        or not isinstance(day, str)
        or not isinstance(payload, str)
        or not isinstance(saved_at, int)
        or sha256 != _digest(payload)
    ):
        return None
    restored = from_ms(restored_at) if isinstance(restored_at, int) else None
    return StoredBackup(
        backup_id, name, date.fromisoformat(day), payload, from_ms(saved_at), restored
    )


class SqliteWorkoutBackupStore:
    """The local copy of deleted workouts on SQLite."""

    def __init__(self, database: SqliteDatabase) -> None:
        self._db = database

    def add(self, backup: NewBackup) -> StoredBackup:
        """Keeps a copy and forgets the restored ones older than `KEEP_RESTORED_BACKUPS_DAYS`."""
        connection = self._db.connection
        saved_ms = to_ms(backup.saved_at)
        cutoff = to_ms(backup.saved_at - timedelta(days=KEEP_RESTORED_BACKUPS_DAYS))
        with translating(WorkoutBackupStoreError), self._db.atomic():
            connection.execute(
                "DELETE FROM workout_backup WHERE restored_at IS NOT NULL AND restored_at < ?",
                (cutoff,),
            )
            (waiting,) = connection.execute(
                "SELECT count(*) FROM workout_backup WHERE restored_at IS NULL"
            ).fetchone()
            if waiting >= MAX_PENDING_BACKUPS:
                raise WorkoutBackupStoreError("too many copies are waiting")
            cursor = connection.execute(
                "INSERT INTO workout_backup (name, calendar_date, payload, payload_sha256, "
                "saved_at) VALUES (?, ?, ?, ?, ?)",
                (
                    backup.name,
                    backup.day.isoformat(),
                    backup.payload,
                    _digest(backup.payload),
                    saved_ms,
                ),
            )
            new_id = cursor.lastrowid
        if new_id is None:
            raise WorkoutBackupStoreError("the copy was not saved")
        return StoredBackup(new_id, backup.name, backup.day, backup.payload, from_ms(saved_ms))

    def pending(self) -> Sequence[StoredBackup]:
        """The copies not yet restored, oldest first."""
        with translating(WorkoutBackupStoreError):
            rows = self._db.connection.execute(
                "SELECT backup_id, name, calendar_date, payload, payload_sha256, saved_at, "
                "restored_at FROM workout_backup WHERE restored_at IS NULL "
                "ORDER BY saved_at, backup_id"
            ).fetchall()
        return [stored for stored in map(_stored, rows) if stored is not None]

    def mark_restored(self, backup_id: int, when: datetime) -> None:
        """Records that the copy was created again; marking twice keeps the first time."""
        with translating(WorkoutBackupStoreError):
            cursor = self._db.connection.execute(
                "UPDATE workout_backup SET restored_at = max(saved_at, ?) "
                "WHERE backup_id = ? AND restored_at IS NULL",
                (to_ms(when), backup_id),
            )
            if cursor.rowcount == 0:
                known = self._db.connection.execute(
                    "SELECT 1 FROM workout_backup WHERE backup_id = ?", (backup_id,)
                ).fetchone()
                if known is None:
                    raise WorkoutBackupStoreError("unknown copy")
