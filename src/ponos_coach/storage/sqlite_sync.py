# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Synchronization journal on SQLite (`SyncJournal` port), tables of schema v2.

On the same `SqliteDatabase` as the index: the transaction of `SqliteActivityIndex` also covers the
journal. Only `?` parameters; no text from the database ends up in the error messages.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Collection, Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Any, Final

from ponos_coach.activities.domain import (
    FailureReason,
    GarminActivityId,
    InvalidTransitionError,
    JournalEntry,
    ListingCursor,
    ListingKind,
    RemoteActivity,
    SyncStatus,
    can_transition,
)
from ponos_coach.activities.ports import SyncJournalError, UnknownJournalEntryError
from ponos_coach.storage.sqlite_database import SqliteDatabase
from ponos_coach.storage.sqlite_stores import from_ms, to_ms

_JOURNAL_COLUMNS: Final = (
    "garmin_id, start_ms, duration_s, sport_key, has_heart_rate, status, failed_attempts, "
    "failure, updated_ms, unanswered_rounds"
)
# Only module constants in the composed queries: no external data (S608).
_SELECT_ENTRIES: Final = "SELECT " + _JOURNAL_COLUMNS + " FROM sync_journal "  # noqa: S608
_TO_DO_ORDER: Final = (
    "WHERE status IN ('pending', 'retryable') ORDER BY start_ms DESC, garmin_id DESC LIMIT ?"
)
_EXHAUSTED_ORDER: Final = (
    "WHERE status = 'failed' AND failure IN ('attempts_exhausted', 'remote_not_provided') "
    "ORDER BY start_ms DESC, garmin_id DESC LIMIT ?"
)


@contextmanager
def _translating() -> Iterator[None]:
    """SQLite errors become `SyncJournalError`, with the cause but without data."""
    try:
        yield
    except sqlite3.Error as exc:
        raise SyncJournalError("journal operation failed") from exc


def _utc_ms(moment: datetime) -> int:
    if moment.utcoffset() != timedelta(0):
        raise ValueError("instants must be UTC with time zone")
    return to_ms(moment)


def _entry_from_row(row: Sequence[Any]) -> JournalEntry:
    remote = RemoteActivity(
        GarminActivityId(row[0]),
        from_ms(row[1]),
        row[2],
        row[3],
        None if row[4] is None else bool(row[4]),
    )
    failure = None if row[7] is None else FailureReason(row[7])
    return JournalEntry(remote, SyncStatus(row[5]), from_ms(row[8]), row[6], failure, row[9])


def _entries(rows: Sequence[Sequence[Any]]) -> list[JournalEntry]:
    try:
        return [_entry_from_row(row) for row in rows]
    except ValueError as exc:
        raise SyncJournalError("invalid journal row") from exc


class SqliteSyncJournal:
    """Synchronization journal (`SyncJournal` port) on SQLite."""

    def __init__(self, database: SqliteDatabase) -> None:
        self._db = database

    def load_cursor(self, kind: ListingKind) -> ListingCursor | None:
        """The saved cursor for the list, or None."""
        with _translating():
            row = self._db.connection.execute(
                "SELECT next_offset, declared_total, completed FROM sync_cursor WHERE kind = ?",
                (kind.value,),
            ).fetchone()
        if row is None:
            return None
        try:
            return ListingCursor(row[0], row[1], bool(row[2]))
        except ValueError as exc:
            raise SyncJournalError("invalid cursor") from exc

    def save_cursor(self, kind: ListingKind, cursor: ListingCursor) -> None:
        """Saves or replaces the list cursor."""
        with _translating():
            self._db.connection.execute(
                "INSERT INTO sync_cursor (kind, next_offset, declared_total, completed) "
                "VALUES (?, ?, ?, ?) ON CONFLICT (kind) DO UPDATE SET "
                "next_offset = excluded.next_offset, declared_total = excluded.declared_total, "
                "completed = excluded.completed",
                (kind.value, cursor.next_offset, cursor.declared_total, int(cursor.completed)),
            )

    def known_ids(self, ids: Collection[GarminActivityId]) -> frozenset[GarminActivityId]:
        """The ids among `ids` that have an entry.

        The ids travel as a single parameter (a JSON array of integers, read by `json_each`):
        no composed SQL and no limit on the number of parameters.
        """
        values = sorted({item.value for item in ids})
        if not values:
            return frozenset()
        with _translating():
            rows = self._db.connection.execute(
                "SELECT garmin_id FROM sync_journal "
                "WHERE garmin_id IN (SELECT value FROM json_each(?))",
                (json.dumps(values),),
            ).fetchall()
        return frozenset(GarminActivityId(row[0]) for row in rows)

    def add_pending(self, items: Sequence[RemoteActivity], now: datetime) -> int:
        """Adds the new entries as `PENDING`; those already known do not change."""
        updated = _utc_ms(now)
        added = 0
        with _translating(), self._db.atomic():
            for item in items:
                cursor = self._db.connection.execute(
                    "INSERT OR IGNORE INTO sync_journal (" + _JOURNAL_COLUMNS + ") "
                    "VALUES (?, ?, ?, ?, ?, 'pending', 0, NULL, ?, 0)",
                    (
                        item.garmin_id.value,
                        to_ms(item.start),
                        item.duration_s,
                        item.sport_key,
                        None if item.has_heart_rate is None else int(item.has_heart_rate),
                        updated,
                    ),
                )
                added += cursor.rowcount
        return added

    def next_to_process(self, limit: int) -> Sequence[JournalEntry]:
        """The first `limit` entries to process, from the most recent."""
        if limit < 1:
            raise ValueError("the limit must be at least 1")
        with _translating():
            rows = self._db.connection.execute(_SELECT_ENTRIES + _TO_DO_ORDER, (limit,)).fetchall()
        return _entries(rows)

    def next_exhausted(self, limit: int) -> Sequence[JournalEntry]:
        """The first `limit` entries failed for exhausted attempts, from the most recent."""
        if limit < 1:
            raise ValueError("the limit must be at least 1")
        with _translating():
            rows = self._db.connection.execute(
                _SELECT_ENTRIES + _EXHAUSTED_ORDER, (limit,)
            ).fetchall()
        return _entries(rows)

    def get(self, garmin_id: GarminActivityId) -> JournalEntry | None:
        """The entry with this id, or None."""
        with _translating():
            row = self._db.connection.execute(
                _SELECT_ENTRIES + "WHERE garmin_id = ?", (garmin_id.value,)
            ).fetchone()
        return None if row is None else _entries([row])[0]

    def update(self, entry: JournalEntry) -> None:
        """Moves the entry to the new state, if the transition is allowed."""
        updated = _utc_ms(entry.updated_at)
        with _translating(), self._db.atomic():
            row = self._db.connection.execute(
                "SELECT status FROM sync_journal WHERE garmin_id = ?", (entry.garmin_id.value,)
            ).fetchone()
            if row is None:
                raise UnknownJournalEntryError("entry missing from the journal")
            current = SyncStatus(row[0])
            if not can_transition(current, entry.status):
                raise InvalidTransitionError(f"{current.value} -> {entry.status.value}")
            self._db.connection.execute(
                "UPDATE sync_journal SET status = ?, failed_attempts = ?, failure = ?, "
                "updated_ms = ?, unanswered_rounds = ? WHERE garmin_id = ?",
                (
                    entry.status.value,
                    entry.failed_attempts,
                    None if entry.failure is None else entry.failure.value,
                    updated,
                    entry.unanswered_rounds,
                    entry.garmin_id.value,
                ),
            )

    def counts(self) -> Mapping[SyncStatus, int]:
        """Entries per state, with all the states present."""
        with _translating():
            rows = self._db.connection.execute(
                "SELECT status, COUNT(*) FROM sync_journal GROUP BY status"
            ).fetchall()
        counts = dict.fromkeys(SyncStatus, 0)
        try:
            counts.update({SyncStatus(status): int(count) for status, count in rows})
        except ValueError as exc:
            raise SyncJournalError("invalid journal state") from exc
        return counts

    def last_completed_at(self) -> datetime | None:
        """End of the last synchronization, or None."""
        with _translating():
            row = self._db.connection.execute(
                "SELECT last_completed_ms FROM sync_state WHERE id = 1"
            ).fetchone()
        return None if row is None else from_ms(row[0])

    def record_completed(self, when: datetime) -> None:
        """Marks the end of a synchronization."""
        milliseconds = _utc_ms(when)
        with _translating():
            self._db.connection.execute(
                "INSERT INTO sync_state (id, last_completed_ms) VALUES (1, ?) "
                "ON CONFLICT (id) DO UPDATE SET last_completed_ms = excluded.last_completed_ms",
                (milliseconds,),
            )
