# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Giornale della sincronizzazione su SQLite (porta `SyncJournal`), tabelle dello schema v2.

Sulla stessa `SqliteDatabase` dell'indice: la transazione di `SqliteActivityIndex` copre anche il
giornale. Solo parametri `?`; nessun testo del database finisce nei messaggi d'errore.
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
    "failure, updated_ms"
)
# Solo costanti del modulo nelle query composte: nessun dato esterno (S608).
_SELECT_ENTRIES: Final = "SELECT " + _JOURNAL_COLUMNS + " FROM sync_journal "  # noqa: S608
_TO_DO_ORDER: Final = (
    "WHERE status IN ('pending', 'retryable') ORDER BY start_ms DESC, garmin_id DESC LIMIT ?"
)
_EXHAUSTED_ORDER: Final = (
    "WHERE status = 'failed' AND failure = 'attempts_exhausted' "
    "ORDER BY start_ms DESC, garmin_id DESC LIMIT ?"
)


@contextmanager
def _translating() -> Iterator[None]:
    """Gli errori di SQLite diventano `SyncJournalError`, con la causa ma senza dati."""
    try:
        yield
    except sqlite3.Error as exc:
        raise SyncJournalError("operazione sul giornale non riuscita") from exc


def _utc_ms(moment: datetime) -> int:
    if moment.utcoffset() != timedelta(0):
        raise ValueError("gli istanti devono essere UTC con fuso")
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
    return JournalEntry(remote, SyncStatus(row[5]), from_ms(row[8]), row[6], failure)


def _entries(rows: Sequence[Sequence[Any]]) -> list[JournalEntry]:
    try:
        return [_entry_from_row(row) for row in rows]
    except ValueError as exc:
        raise SyncJournalError("riga del giornale non valida") from exc


class SqliteSyncJournal:
    """Giornale della sincronizzazione (porta `SyncJournal`) su SQLite."""

    def __init__(self, database: SqliteDatabase) -> None:
        self._db = database

    def load_cursor(self, kind: ListingKind) -> ListingCursor | None:
        """Il cursore salvato per l'elenco, o None."""
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
            raise SyncJournalError("cursore non valido") from exc

    def save_cursor(self, kind: ListingKind, cursor: ListingCursor) -> None:
        """Salva o sostituisce il cursore dell'elenco."""
        with _translating():
            self._db.connection.execute(
                "INSERT INTO sync_cursor (kind, next_offset, declared_total, completed) "
                "VALUES (?, ?, ?, ?) ON CONFLICT (kind) DO UPDATE SET "
                "next_offset = excluded.next_offset, declared_total = excluded.declared_total, "
                "completed = excluded.completed",
                (kind.value, cursor.next_offset, cursor.declared_total, int(cursor.completed)),
            )

    def known_ids(self, ids: Collection[GarminActivityId]) -> frozenset[GarminActivityId]:
        """Gli id tra `ids` che hanno una voce.

        Gli id viaggiano come un solo parametro (un array JSON di interi, letto da `json_each`):
        niente SQL composto e nessun limite sul numero di parametri.
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
        """Aggiunge le voci nuove come `PENDING`; quelle già note non cambiano."""
        updated = _utc_ms(now)
        added = 0
        with _translating(), self._db.atomic():
            for item in items:
                cursor = self._db.connection.execute(
                    "INSERT OR IGNORE INTO sync_journal (" + _JOURNAL_COLUMNS + ") "
                    "VALUES (?, ?, ?, ?, ?, 'pending', 0, NULL, ?)",
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
        """Le prime `limit` voci da lavorare, dalla più recente."""
        if limit < 1:
            raise ValueError("il limite deve essere almeno 1")
        with _translating():
            rows = self._db.connection.execute(_SELECT_ENTRIES + _TO_DO_ORDER, (limit,)).fetchall()
        return _entries(rows)

    def next_exhausted(self, limit: int) -> Sequence[JournalEntry]:
        """Le prime `limit` voci fallite per tentativi esauriti, dalla più recente."""
        if limit < 1:
            raise ValueError("il limite deve essere almeno 1")
        with _translating():
            rows = self._db.connection.execute(
                _SELECT_ENTRIES + _EXHAUSTED_ORDER, (limit,)
            ).fetchall()
        return _entries(rows)

    def get(self, garmin_id: GarminActivityId) -> JournalEntry | None:
        """La voce con questo id, o None."""
        with _translating():
            row = self._db.connection.execute(
                _SELECT_ENTRIES + "WHERE garmin_id = ?", (garmin_id.value,)
            ).fetchone()
        return None if row is None else _entries([row])[0]

    def update(self, entry: JournalEntry) -> None:
        """Porta la voce al nuovo stato, se la transizione è ammessa."""
        updated = _utc_ms(entry.updated_at)
        with _translating(), self._db.atomic():
            row = self._db.connection.execute(
                "SELECT status FROM sync_journal WHERE garmin_id = ?", (entry.garmin_id.value,)
            ).fetchone()
            if row is None:
                raise UnknownJournalEntryError("voce assente dal giornale")
            current = SyncStatus(row[0])
            if not can_transition(current, entry.status):
                raise InvalidTransitionError(f"{current.value} -> {entry.status.value}")
            self._db.connection.execute(
                "UPDATE sync_journal SET status = ?, failed_attempts = ?, failure = ?, "
                "updated_ms = ? WHERE garmin_id = ?",
                (
                    entry.status.value,
                    entry.failed_attempts,
                    None if entry.failure is None else entry.failure.value,
                    updated,
                    entry.garmin_id.value,
                ),
            )

    def counts(self) -> Mapping[SyncStatus, int]:
        """Voci per stato, con tutti gli stati presenti."""
        with _translating():
            rows = self._db.connection.execute(
                "SELECT status, COUNT(*) FROM sync_journal GROUP BY status"
            ).fetchall()
        counts = dict.fromkeys(SyncStatus, 0)
        try:
            counts.update({SyncStatus(status): int(count) for status, count in rows})
        except ValueError as exc:
            raise SyncJournalError("stato del giornale non valido") from exc
        return counts

    def last_completed_at(self) -> datetime | None:
        """Fine dell'ultima sincronizzazione, o None."""
        with _translating():
            row = self._db.connection.execute(
                "SELECT last_completed_ms FROM sync_state WHERE id = 1"
            ).fetchone()
        return None if row is None else from_ms(row[0])

    def record_completed(self, when: datetime) -> None:
        """Segna la fine di una sincronizzazione."""
        milliseconds = _utc_ms(when)
        with _translating():
            self._db.connection.execute(
                "INSERT INTO sync_state (id, last_completed_ms) VALUES (1, ?) "
                "ON CONFLICT (id) DO UPDATE SET last_completed_ms = excluded.last_completed_ms",
                (milliseconds,),
            )
