# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Store SQLite di un profilo: indice delle attività, parametri fisiologici, cache dei carichi.

Tre classi sulla stessa `SqliteDatabase`: la transazione di `SqliteActivityIndex` copre anche
gli altri due. Solo parametri `?`; nessun testo del database o dell'input finisce nei messaggi
d'errore (posizioni e salute restano fuori dai log).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import AbstractContextManager, contextmanager
from datetime import UTC, date, datetime, timedelta
from pathlib import PurePosixPath
from typing import Any, Final

from ponos_coach.activities.domain import (
    DUPLICATE_START_TOLERANCE,
    ActivityIdentity,
    ActivityOrigin,
    ActivityRecord,
    ActivitySummary,
    ArchivedFile,
    FileFormat,
    GarminActivityId,
    GeoPoint,
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
from ponos_coach.load.domain import (
    ActivityLoad,
    HeartRateLimits,
    LoadFingerprint,
    LoadSource,
    Physiology,
    PhysiologyEntry,
    Sex,
    ThresholdHeartRate,
)
from ponos_coach.load.ports import LoadStoreError, UnknownActivityError
from ponos_coach.storage.sqlite_database import SqliteDatabase

_EPOCH: Final = datetime(1970, 1, 1, tzinfo=UTC)
_MILLISECOND: Final = timedelta(milliseconds=1)

_ACTIVITY_COLUMNS: Final = (
    "identity_key, file_format, sport, start_ms, elapsed_s, timer_s, distance_m, ascent_m, "
    "start_lat, start_lon, track_polyline, archive_path, archive_sha256, archive_size_bytes, "
    "origin, imported_ms, garmin_activity_id"
)
# Solo costanti del modulo nelle query composte: nessun dato esterno (S608).
_SELECT_ACTIVITIES: Final = "SELECT " + _ACTIVITY_COLUMNS + " FROM activities "  # noqa: S608
_INSERT_ACTIVITY: Final = (
    "INSERT INTO activities ("  # noqa: S608
    + _ACTIVITY_COLUMNS
    + ") VALUES ("
    + ", ".join("?" * 17)
    + ")"
)


def to_ms(moment: datetime) -> int:
    """Millisecondi UTC interi dall'epoca.

    Raises:
        ValueError: l'istante ha una risoluzione sotto il millisecondo (non si arrotonda).
    """
    delta = moment - _EPOCH
    if delta % _MILLISECOND:
        raise ValueError("istante con risoluzione sotto il millisecondo")
    return delta // _MILLISECOND


def floor_ms(moment: datetime) -> int:
    """Millisecondi UTC interi dall'epoca, per difetto (per i confronti, mai per salvare)."""
    return (moment - _EPOCH) // _MILLISECOND


def from_ms(milliseconds: int) -> datetime:
    """Istante UTC da millisecondi interi."""
    return _EPOCH + timedelta(milliseconds=milliseconds)


@contextmanager
def _translating(error: type[Exception]) -> Iterator[None]:
    """Gli errori di SQLite diventano l'errore della porta, con la causa ma senza dati."""
    try:
        yield
    except sqlite3.Error as exc:
        raise error("operazione sul database non riuscita") from exc


def _is_unique_violation(exc: sqlite3.Error) -> bool:
    return getattr(exc, "sqlite_errorcode", None) == sqlite3.SQLITE_CONSTRAINT_UNIQUE


def _activity_params(record: ActivityRecord) -> tuple[Any, ...]:
    summary, archived = record.summary, record.archived
    point = summary.start_point
    return (
        identity_key(record.identity),
        record.file_format.value,
        record.sport.value,
        to_ms(record.start),
        record.elapsed_s,
        record.timer_s,
        summary.distance_m,
        summary.ascent_m,
        None if point is None else point.lat_deg,
        None if point is None else point.lon_deg,
        summary.track_polyline,
        str(archived.relative_path),
        archived.sha256_hex,
        archived.size_bytes,
        record.origin.value,
        to_ms(record.imported_at),
        record.garmin_activity_id,
    )


def _record_from_row(row: Sequence[Any]) -> ActivityRecord:
    point = None if row[8] is None else GeoPoint(row[8], row[9])
    return ActivityRecord(
        identity=identity_from_key(row[0]),
        file_format=FileFormat(row[1]),
        sport=Sport(row[2]),
        start=from_ms(row[3]),
        elapsed_s=row[4],
        timer_s=row[5],
        summary=ActivitySummary(row[6], row[7], point, row[10]),
        archived=ArchivedFile(PurePosixPath(row[11]), row[12], row[13]),
        origin=ActivityOrigin(row[14]),
        imported_at=from_ms(row[15]),
        garmin_activity_id=row[16],
    )


def _records(rows: Sequence[Sequence[Any]]) -> list[ActivityRecord]:
    try:
        return [_record_from_row(row) for row in rows]
    except ValueError as exc:
        raise ActivityIndexError("riga dell'indice non valida") from exc


class SqliteActivityIndex:
    """Indice delle attività (porta `ActivityIndex`) su SQLite."""

    def __init__(self, database: SqliteDatabase) -> None:
        self._db = database

    def transaction(self) -> AbstractContextManager[None]:
        """Vedi `ActivityIndex.transaction`: `BEGIN IMMEDIATE`, non annidabile."""
        return self._db.transaction()

    def exists(self, identity: ActivityIdentity) -> bool:
        """Vero se c'è un record con questa identità."""
        with _translating(ActivityIndexError):
            row = self._db.connection.execute(
                "SELECT 1 FROM activities WHERE identity_key = ?", (identity_key(identity),)
            ).fetchone()
        return row is not None

    def get(self, identity: ActivityIdentity) -> ActivityRecord | None:
        """Il record con questa identità, o None."""
        with _translating(ActivityIndexError):
            row = self._db.connection.execute(
                _SELECT_ACTIVITIES + "WHERE identity_key = ?", (identity_key(identity),)
            ).fetchone()
        return None if row is None else _records([row])[0]

    def find_possible_duplicates(
        self, start: datetime, elapsed_s: float
    ) -> Sequence[ActivityRecord]:
        """Prefiltro SQL sull'inizio (finestra un po' più larga), poi `is_possible_duplicate`."""
        margin_ms = DUPLICATE_START_TOLERANCE // _MILLISECOND + 1
        centre = floor_ms(start)
        with _translating(ActivityIndexError):
            rows = self._db.connection.execute(
                _SELECT_ACTIVITIES
                + "WHERE start_ms BETWEEN ? AND ? ORDER BY start_ms, identity_key",
                (centre - margin_ms, centre + margin_ms),
            ).fetchall()
        return [
            record
            for record in _records(rows)
            if is_possible_duplicate(start, elapsed_s, record.start, record.elapsed_s)
        ]

    def add(self, record: ActivityRecord) -> None:
        """Aggiunge un record; identità o id Garmin già presenti → `DuplicateActivityError`."""
        params = _activity_params(record)
        try:
            self._db.connection.execute(_INSERT_ACTIVITY, params)
        except sqlite3.IntegrityError as exc:
            if _is_unique_violation(exc):
                raise DuplicateActivityError("attività già presente") from exc
            raise ActivityIndexError("record rifiutato dai vincoli del database") from exc
        except sqlite3.Error as exc:
            raise ActivityIndexError("operazione sul database non riuscita") from exc

    def list_by_start(self) -> Sequence[ActivityRecord]:
        """Tutti i record per inizio crescente, a parità per chiave di identità."""
        with _translating(ActivityIndexError):
            rows = self._db.connection.execute(
                _SELECT_ACTIVITIES + "ORDER BY start_ms, identity_key"
            ).fetchall()
        return _records(rows)

    def find_by_garmin_id(self, garmin_id: GarminActivityId) -> ActivityRecord | None:
        """Il record con questo id Garmin, o None."""
        with _translating(ActivityIndexError):
            row = self._db.connection.execute(
                _SELECT_ACTIVITIES + "WHERE garmin_activity_id = ?", (garmin_id.value,)
            ).fetchone()
        return None if row is None else _records([row])[0]

    def link_garmin_id(self, identity: ActivityIdentity, garmin_id: GarminActivityId) -> None:
        """Collega l'id Garmin a un record che non ne ha ancora uno (idempotente)."""
        key = identity_key(identity)
        try:
            cursor = self._db.connection.execute(
                "UPDATE activities SET garmin_activity_id = ? WHERE identity_key = ? "
                "AND (garmin_activity_id IS NULL OR garmin_activity_id = ?)",
                (garmin_id.value, key, garmin_id.value),
            )
        except sqlite3.IntegrityError as exc:
            if _is_unique_violation(exc):
                raise DuplicateActivityError("id Garmin già assegnato") from exc
            raise ActivityIndexError("record rifiutato dai vincoli del database") from exc
        except sqlite3.Error as exc:
            raise ActivityIndexError("operazione sul database non riuscita") from exc
        if cursor.rowcount == 0:
            if not self.exists(identity):
                raise ActivityNotFoundError("attività non presente nell'indice")
            raise DuplicateActivityError("l'attività ha già un altro id Garmin")


class SqlitePhysiologyStore:
    """Parametri fisiologici (porta `PhysiologyStore`) su SQLite."""

    def __init__(self, database: SqliteDatabase) -> None:
        self._db = database

    def put(self, entry: PhysiologyEntry) -> None:
        """Salva i parametri validi da `entry.valid_from`, sostituendo la stessa data."""
        physiology = entry.physiology
        with _translating(LoadStoreError):
            self._db.connection.execute(
                "INSERT INTO physiology (valid_from, min_bpm, max_bpm, threshold_bpm, sex) "
                "VALUES (?, ?, ?, ?, ?) ON CONFLICT (valid_from) DO UPDATE SET "
                "min_bpm = excluded.min_bpm, max_bpm = excluded.max_bpm, "
                "threshold_bpm = excluded.threshold_bpm, sex = excluded.sex",
                (
                    entry.valid_from.isoformat(),
                    physiology.limits.min_bpm,
                    physiology.limits.max_bpm,
                    physiology.threshold.bpm,
                    physiology.sex.value,
                ),
            )

    def list_entries(self) -> Sequence[PhysiologyEntry]:
        """Tutte le voci per data crescente."""
        with _translating(LoadStoreError):
            rows = self._db.connection.execute(
                "SELECT valid_from, min_bpm, max_bpm, threshold_bpm, sex "
                "FROM physiology ORDER BY valid_from"
            ).fetchall()
        try:
            return [
                PhysiologyEntry(
                    date.fromisoformat(row[0]),
                    Physiology(
                        HeartRateLimits(row[1], row[2]), ThresholdHeartRate(row[3]), Sex(row[4])
                    ),
                )
                for row in rows
            ]
        except ValueError as exc:
            raise LoadStoreError("parametri fisiologici non validi") from exc


class SqliteLoadCache:
    """Cache dei carichi (porta `LoadCache`) su SQLite: derivata, rigenerabile."""

    def __init__(self, database: SqliteDatabase) -> None:
        self._db = database

    def get(self, identity: ActivityIdentity, fingerprint: LoadFingerprint) -> ActivityLoad | None:
        """Il carico salvato, solo se prodotto con questa impronta."""
        with _translating(LoadStoreError):
            row = self._db.connection.execute(
                "SELECT c.trimp, c.hr_tss, c.load, c.used_samples, c.discarded_samples, "
                "c.excluded_seconds, c.source FROM load_cache AS c "
                "JOIN activities AS a ON a.id = c.activity_id "
                "WHERE a.identity_key = ? AND c.fingerprint = ?",
                (identity_key(identity), fingerprint.hex),
            ).fetchone()
        if row is None:
            return None
        return ActivityLoad(row[0], row[1], row[2], row[3], row[4], row[5], LoadSource(row[6]))

    def put(
        self, identity: ActivityIdentity, fingerprint: LoadFingerprint, load: ActivityLoad
    ) -> None:
        """Salva o sostituisce il carico; attività sconosciuta → `UnknownActivityError`."""
        with _translating(LoadStoreError):
            cursor = self._db.connection.execute(
                "INSERT INTO load_cache (activity_id, fingerprint, trimp, hr_tss, load, "
                "used_samples, discarded_samples, excluded_seconds, source) "
                "SELECT id, ?, ?, ?, ?, ?, ?, ?, ? FROM activities WHERE identity_key = ? "
                "ON CONFLICT (activity_id) DO UPDATE SET fingerprint = excluded.fingerprint, "
                "trimp = excluded.trimp, hr_tss = excluded.hr_tss, load = excluded.load, "
                "used_samples = excluded.used_samples, "
                "discarded_samples = excluded.discarded_samples, "
                "excluded_seconds = excluded.excluded_seconds, source = excluded.source",
                (
                    fingerprint.hex,
                    load.trimp,
                    load.hr_tss,
                    load.load,
                    load.used_samples,
                    load.discarded_samples,
                    load.excluded_seconds,
                    load.source.value,
                    identity_key(identity),
                ),
            )
        if cursor.rowcount == 0:
            raise UnknownActivityError("attività non presente nell'indice")

    def invalidate(self, identity: ActivityIdentity) -> None:
        """Dimentica il carico di un'attività (nessun effetto se non c'è)."""
        with _translating(LoadStoreError):
            self._db.connection.execute(
                "DELETE FROM load_cache WHERE activity_id IN "
                "(SELECT id FROM activities WHERE identity_key = ?)",
                (identity_key(identity),),
            )

    def clear(self) -> None:
        """Dimentica tutti i carichi."""
        with _translating(LoadStoreError):
            self._db.connection.execute("DELETE FROM load_cache")
