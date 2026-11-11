# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Schema migration for the activities the user deleted (ADR 018).

Kept in a module of its own, like the LT1's; `sqlite_schema.MIGRATIONS` lists it in its place. No
imports from the project: `sqlite_schema` imports this module.

- `deleted_activity`: one row per deleted activity (the "tombstone"): the identity key, the Garmin
  id if there was one, what the "Deleted activities" list shows (sport, start, elapsed time,
  distance), the reference to the file that stays in the archive, and the origin and import instant
  of the activity so that a restored one is as it was. No position, no heart rate.
- Two triggers keep an identity either in `activities` or in `deleted_activity`, never in both: a
  deleted activity cannot be inserted into the index (so a code path that forgets to look at the
  tombstones fails instead of bringing the activity back), and an activity still in the index
  cannot be marked deleted.
- `sync_journal` admits the new state `skipped` (the activity of the entry was deleted by the
  user). SQLite cannot change a CHECK in place, so the table is rebuilt with the checks of the
  previous rebuild plus the new state (the rows are copied as they are) and its partial index is
  created again. `activities` is not rebuilt: a later rebuild of `activities` must create the
  trigger again.

Frozen here on purpose: a released migration never changes.
"""

from __future__ import annotations

from typing import Final

_SPORTS = "'running','trail','cycling','strength','hiking','walking','other'"
_STATUSES = "'pending','downloaded','matched','retryable','failed','skipped'"
SYNC_STATUSES_V19: Final = frozenset(_STATUSES.replace("'", "").split(","))
_RETRYABLE = "'transient_error','rate_limited'"
_FAILURES = (
    _RETRYABLE + ",'unsupported_format','corrupt_file','file_too_large','not_an_activity',"
    "'archive_invalid','remote_rejected','attempts_exhausted','remote_not_provided'"
)

_DELETED_ACTIVITY = f"""
CREATE TABLE deleted_activity (
    identity_key       TEXT    NOT NULL PRIMARY KEY
                               CHECK (length(identity_key) BETWEEN 5 AND 128),
    file_format        TEXT    NOT NULL CHECK (file_format IN ('fit')),
    sport              TEXT    NOT NULL CHECK (sport IN ({_SPORTS})),
    start_ms           INTEGER NOT NULL,
    elapsed_s          REAL    NOT NULL CHECK (elapsed_s > 0),
    distance_m         REAL    CHECK (distance_m IS NULL OR distance_m >= 0),
    archive_path       TEXT    NOT NULL CHECK (length(archive_path) BETWEEN 1 AND 1024),
    archive_sha256     TEXT    NOT NULL CHECK (length(archive_sha256) = 64),
    archive_size_bytes INTEGER NOT NULL CHECK (archive_size_bytes >= 0),
    origin             TEXT    NOT NULL CHECK (origin IN ('import','garmin')),
    imported_ms        INTEGER NOT NULL,
    garmin_activity_id INTEGER UNIQUE CHECK (garmin_activity_id IS NULL OR garmin_activity_id > 0),
    deleted_ms         INTEGER NOT NULL,
    CHECK (origin <> 'garmin' OR garmin_activity_id IS NOT NULL),
    CHECK (substr(identity_key, 1, 4) = file_format || ':')
) STRICT
"""

_BY_START = "CREATE INDEX deleted_activity_by_start ON deleted_activity (start_ms)"

_NOT_DELETED = """
CREATE TRIGGER activities_not_deleted BEFORE INSERT ON activities
WHEN EXISTS (SELECT 1 FROM deleted_activity WHERE identity_key = NEW.identity_key)
BEGIN
    SELECT RAISE(ABORT, 'activity is deleted');
END
"""

_NOT_IN_INDEX = """
CREATE TRIGGER deleted_activity_not_in_index BEFORE INSERT ON deleted_activity
WHEN EXISTS (SELECT 1 FROM activities WHERE identity_key = NEW.identity_key)
BEGIN
    SELECT RAISE(ABORT, 'activity is still in the index');
END
"""

_JOURNAL_COLUMNS = (
    "garmin_id, start_ms, duration_s, sport_key, has_heart_rate, status, failed_attempts, "
    "failure, updated_ms, unanswered_rounds"
)
_JOURNAL = f"""
CREATE TABLE sync_journal_v19 (
    garmin_id         INTEGER NOT NULL PRIMARY KEY CHECK (garmin_id > 0),
    start_ms          INTEGER NOT NULL,
    duration_s        REAL    NOT NULL CHECK (duration_s > 0),
    sport_key         TEXT    NOT NULL CHECK (length(sport_key) BETWEEN 1 AND 64),
    has_heart_rate    INTEGER CHECK (has_heart_rate IS NULL OR has_heart_rate IN (0, 1)),
    status            TEXT    NOT NULL CHECK (status IN ({_STATUSES})),
    failed_attempts   INTEGER NOT NULL CHECK (failed_attempts >= 0),
    failure           TEXT    CHECK (failure IS NULL OR failure IN ({_FAILURES})),
    updated_ms        INTEGER NOT NULL,
    unanswered_rounds INTEGER NOT NULL DEFAULT 0 CHECK (unanswered_rounds >= 0),
    CHECK ((status IN ('retryable', 'failed')) = (failure IS NOT NULL)),
    CHECK (status <> 'pending' OR (failed_attempts = 0 AND unanswered_rounds = 0)),
    CHECK (status NOT IN ('retryable', 'failed') OR failed_attempts + unanswered_rounds >= 1),
    CHECK (status <> 'retryable' OR failure IN ({_RETRYABLE})),
    CHECK (status <> 'failed' OR failure NOT IN ({_RETRYABLE}))
) STRICT
"""
_COPY_JOURNAL = (
    f"INSERT INTO sync_journal_v19 ({_JOURNAL_COLUMNS}) "  # noqa: S608 - module constants only
    f"SELECT {_JOURNAL_COLUMNS} FROM sync_journal"
)
_DROP_JOURNAL = "DROP TABLE sync_journal"
_RENAME_JOURNAL = "ALTER TABLE sync_journal_v19 RENAME TO sync_journal"
_JOURNAL_TO_DO_INDEX = (
    "CREATE INDEX sync_journal_to_do ON sync_journal (start_ms DESC, garmin_id DESC) "
    "WHERE status IN ('pending', 'retryable')"
)

DELETED_ACTIVITY_MIGRATION: Final[tuple[str, ...]] = (
    _DELETED_ACTIVITY,
    _BY_START,
    _NOT_DELETED,
    _NOT_IN_INDEX,
    _JOURNAL,
    _COPY_JOURNAL,
    _DROP_JOURNAL,
    _RENAME_JOURNAL,
    _JOURNAL_TO_DO_INDEX,
)
