# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Schema migration (v9) for the local copy of workouts deleted from Garmin (ADR 013).

Kept in a module of its own; `sqlite_schema.MIGRATIONS` has it as version 9, which is final.
No imports: `sqlite_schema` imports this module.

`workout_backup`: one row per deleted workout, with the definition as JSON text (a Garmin library
definition, opaque to the app) and its SHA-256, the day it was on, when it was saved and, once
created again, when. Times are whole UTC milliseconds. The copy exists so that a deletion can be
undone: it is the only place that holds the definition once Garmin has deleted it.
"""

from __future__ import annotations

from typing import Final

MAX_NAME_CHARS: Final = 200
MAX_PAYLOAD_CHARS: Final = 200_000  # same as `planning.workout_calendar.MAX_BACKUP_PAYLOAD_CHARS`
MIN_DATE: Final = "2000-01-01"

_WORKOUT_BACKUP = f"""
CREATE TABLE workout_backup (
    backup_id      INTEGER NOT NULL PRIMARY KEY,
    name           TEXT    NOT NULL CHECK (length(name) BETWEEN 1 AND {MAX_NAME_CHARS}),
    calendar_date  TEXT    NOT NULL CHECK (date(calendar_date) IS calendar_date
                                           AND calendar_date >= '{MIN_DATE}'),
    payload        TEXT    NOT NULL CHECK (length(payload) BETWEEN 2 AND {MAX_PAYLOAD_CHARS}),
    payload_sha256 TEXT    NOT NULL CHECK (length(payload_sha256) = 64),
    saved_at       INTEGER NOT NULL CHECK (saved_at >= 0),
    restored_at    INTEGER CHECK (restored_at IS NULL OR restored_at >= saved_at)
) STRICT
"""

_WORKOUT_BACKUP_PENDING = (
    "CREATE INDEX workout_backup_pending ON workout_backup (saved_at, backup_id) "
    "WHERE restored_at IS NULL"
)

WORKOUT_BACKUP_MIGRATION: Final[tuple[str, ...]] = (_WORKOUT_BACKUP, _WORKOUT_BACKUP_PENDING)
