# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""SQLite stores of a profile: activity index, physiological parameters, load cache.

Three classes on the same `SqliteDatabase`: the transaction of `SqliteActivityIndex` also covers
the other two. Only `?` parameters; no text from the database or from the input ends up in the
messages
of errors (positions and health data stay out of the logs).
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
    GeoBox,
    GeoPoint,
    Sport,
    identity_from_key,
    identity_key,
    is_possible_duplicate,
    track_box_of,
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
    ZoneSeconds,
)
from ponos_coach.load.ports import (
    LoadStoreError,
    PhysiologyEntryNotFoundError,
    UnknownActivityError,
)
from ponos_coach.storage.sqlite_database import SqliteDatabase

_EPOCH: Final = datetime(1970, 1, 1, tzinfo=UTC)
_MILLISECOND: Final = timedelta(milliseconds=1)

ACTIVITY_COLUMNS: Final = (
    "identity_key, file_format, sport, start_ms, elapsed_s, timer_s, distance_m, ascent_m, "
    "start_lat, start_lon, track_polyline, archive_path, archive_sha256, archive_size_bytes, "
    "origin, imported_ms, garmin_activity_id, calories_kcal"
)
# Only module constants in the composed queries: no external data (S608).
_SELECT_ACTIVITIES: Final = "SELECT " + ACTIVITY_COLUMNS + " FROM activities "  # noqa: S608
_BOX_COLUMNS: Final = "track_south, track_west, track_north, track_east"
_INSERT_ACTIVITY: Final = (
    "INSERT INTO activities ("  # noqa: S608
    + ACTIVITY_COLUMNS
    + ", "
    + _BOX_COLUMNS
    + ") VALUES ("
    + ", ".join("?" * 22)
    + ")"
)


def to_ms(moment: datetime) -> int:
    """Whole UTC milliseconds since the epoch.

    Raises:
        ValueError: the instant has a resolution below the millisecond (it is not rounded).
    """
    delta = moment - _EPOCH
    if delta % _MILLISECOND:
        raise ValueError("instant with resolution below the millisecond")
    return delta // _MILLISECOND


def floor_ms(moment: datetime) -> int:
    """Whole UTC milliseconds since the epoch, rounded down (for comparisons, never for saving)."""
    return (moment - _EPOCH) // _MILLISECOND


def _ceil_ms(moment: datetime) -> int:
    """Whole UTC milliseconds since the epoch, rounded up."""
    whole, rest = divmod(moment - _EPOCH, _MILLISECOND)
    return whole + (1 if rest else 0)


def from_ms(milliseconds: int) -> datetime:
    """UTC instant from whole milliseconds."""
    return _EPOCH + timedelta(milliseconds=milliseconds)


@contextmanager
def translating(error: type[Exception]) -> Iterator[None]:
    """SQLite errors become the port's error, with the cause but without data."""
    try:
        yield
    except sqlite3.Error as exc:
        raise error("database operation failed") from exc


def _is_unique_violation(exc: sqlite3.Error) -> bool:
    return getattr(exc, "sqlite_errorcode", None) == sqlite3.SQLITE_CONSTRAINT_UNIQUE


def box_values(box: GeoBox | None) -> tuple[float | None, ...]:
    """The four columns of a track box (all `None` without a box), in column order."""
    if box is None:
        return (None, None, None, None)
    return (box.south, box.west, box.north, box.east)


def _derived_box(summary: ActivitySummary) -> GeoBox | None:
    """Box saved with a new row; an unreadable polyline gives no box (the fill retries it)."""
    try:
        return track_box_of(summary.start_point, summary.track_polyline)
    except ValueError:
        return None


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
        summary.calories_kcal,
        *box_values(_derived_box(summary)),
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
        summary=ActivitySummary(row[6], row[7], point, row[10], row[17]),
        archived=ArchivedFile(PurePosixPath(row[11]), row[12], row[13]),
        origin=ActivityOrigin(row[14]),
        imported_at=from_ms(row[15]),
        garmin_activity_id=row[16],
    )


def records_from_rows(rows: Sequence[Sequence[Any]]) -> list[ActivityRecord]:
    try:
        return [_record_from_row(row) for row in rows]
    except ValueError as exc:
        raise ActivityIndexError("invalid index row") from exc


class SqliteActivityIndex:
    """Activity index (`ActivityIndex` port) on SQLite."""

    def __init__(self, database: SqliteDatabase) -> None:
        self._db = database

    def transaction(self) -> AbstractContextManager[None]:
        """See `ActivityIndex.transaction`: `BEGIN IMMEDIATE`, not nestable."""
        return self._db.transaction()

    def exists(self, identity: ActivityIdentity) -> bool:
        """True if there is a record with this identity."""
        with translating(ActivityIndexError):
            row = self._db.connection.execute(
                "SELECT 1 FROM activities WHERE identity_key = ?", (identity_key(identity),)
            ).fetchone()
        return row is not None

    def get(self, identity: ActivityIdentity) -> ActivityRecord | None:
        """The record with this identity, or None."""
        with translating(ActivityIndexError):
            row = self._db.connection.execute(
                _SELECT_ACTIVITIES + "WHERE identity_key = ?", (identity_key(identity),)
            ).fetchone()
        return None if row is None else records_from_rows([row])[0]

    def find_possible_duplicates(
        self, start: datetime, elapsed_s: float
    ) -> Sequence[ActivityRecord]:
        """SQL prefilter on the start (a slightly wider window), then `is_possible_duplicate`."""
        margin_ms = DUPLICATE_START_TOLERANCE // _MILLISECOND + 1
        centre = floor_ms(start)
        with translating(ActivityIndexError):
            rows = self._db.connection.execute(
                _SELECT_ACTIVITIES
                + "WHERE start_ms BETWEEN ? AND ? ORDER BY start_ms, identity_key",
                (centre - margin_ms, centre + margin_ms),
            ).fetchall()
        return [
            record
            for record in records_from_rows(rows)
            if is_possible_duplicate(start, elapsed_s, record.start, record.elapsed_s)
        ]

    def add(self, record: ActivityRecord) -> None:
        """Adds a record; identity or Garmin id already present → `DuplicateActivityError`."""
        params = _activity_params(record)
        try:
            self._db.connection.execute(_INSERT_ACTIVITY, params)
        except sqlite3.IntegrityError as exc:
            if _is_unique_violation(exc):
                raise DuplicateActivityError("activity already present") from exc
            raise ActivityIndexError("record rejected by the database constraints") from exc
        except sqlite3.Error as exc:
            raise ActivityIndexError("database operation failed") from exc

    def list_by_start(self) -> Sequence[ActivityRecord]:
        """All the records by ascending start, ties by identity key."""
        with translating(ActivityIndexError):
            rows = self._db.connection.execute(
                _SELECT_ACTIVITIES + "ORDER BY start_ms, identity_key"
            ).fetchall()
        return records_from_rows(rows)

    def list_started_between(self, first: datetime, last: datetime) -> Sequence[ActivityRecord]:
        """Records with `first <= start < last` by ascending start (uses the start index).

        The stored starts are whole milliseconds, so the bounds are rounded up to the next
        millisecond for the comparison to be exact.
        """
        with translating(ActivityIndexError):
            rows = self._db.connection.execute(
                _SELECT_ACTIVITIES
                + "WHERE start_ms >= ? AND start_ms < ? ORDER BY start_ms, identity_key",
                (_ceil_ms(first), _ceil_ms(last)),
            ).fetchall()
        return records_from_rows(rows)

    def first_start_at_or_after(self, instant: datetime) -> datetime | None:
        """The earliest start not before `instant`, or None."""
        with translating(ActivityIndexError):
            row = self._db.connection.execute(
                "SELECT MIN(start_ms) FROM activities WHERE start_ms >= ?", (_ceil_ms(instant),)
            ).fetchone()
        return None if row is None or row[0] is None else from_ms(row[0])

    def find_by_garmin_id(self, garmin_id: GarminActivityId) -> ActivityRecord | None:
        """The record with this Garmin id, or None."""
        with translating(ActivityIndexError):
            row = self._db.connection.execute(
                _SELECT_ACTIVITIES + "WHERE garmin_activity_id = ?", (garmin_id.value,)
            ).fetchone()
        return None if row is None else records_from_rows([row])[0]

    def link_garmin_id(self, identity: ActivityIdentity, garmin_id: GarminActivityId) -> None:
        """Links the Garmin id to a record that does not have one yet (idempotent)."""
        key = identity_key(identity)
        try:
            cursor = self._db.connection.execute(
                "UPDATE activities SET garmin_activity_id = ? WHERE identity_key = ? "
                "AND (garmin_activity_id IS NULL OR garmin_activity_id = ?)",
                (garmin_id.value, key, garmin_id.value),
            )
        except sqlite3.IntegrityError as exc:
            if _is_unique_violation(exc):
                raise DuplicateActivityError("Garmin id already assigned") from exc
            raise ActivityIndexError("record rejected by the database constraints") from exc
        except sqlite3.Error as exc:
            raise ActivityIndexError("database operation failed") from exc
        if cursor.rowcount == 0:
            if not self.exists(identity):
                raise ActivityNotFoundError("activity not in the index")
            raise DuplicateActivityError("the activity already has another Garmin id")


def _bpm_or_none(threshold: ThresholdHeartRate | None) -> int | None:
    return None if threshold is None else threshold.bpm


def _optional_threshold(bpm: int | None) -> ThresholdHeartRate | None:
    return None if bpm is None else ThresholdHeartRate(bpm)


class SqlitePhysiologyStore:
    """Physiological parameters (`PhysiologyStore` port) on SQLite."""

    def __init__(self, database: SqliteDatabase) -> None:
        self._db = database

    def put(self, entry: PhysiologyEntry) -> None:
        """Saves the parameters valid from `entry.valid_from`, replacing the same date."""
        physiology = entry.physiology
        with translating(LoadStoreError):
            self._db.connection.execute(
                "INSERT INTO physiology (valid_from, min_bpm, max_bpm, threshold_bpm, sex, "
                "bike_threshold_bpm, other_threshold_bpm) "
                "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT (valid_from) DO UPDATE SET "
                "min_bpm = excluded.min_bpm, max_bpm = excluded.max_bpm, "
                "threshold_bpm = excluded.threshold_bpm, sex = excluded.sex, "
                "bike_threshold_bpm = excluded.bike_threshold_bpm, "
                "other_threshold_bpm = excluded.other_threshold_bpm",
                (
                    entry.valid_from.isoformat(),
                    physiology.limits.min_bpm,
                    physiology.limits.max_bpm,
                    physiology.threshold.bpm,
                    physiology.sex.value,
                    _bpm_or_none(physiology.bike_threshold),
                    _bpm_or_none(physiology.other_threshold),
                ),
            )

    def remove(self, valid_from: date) -> None:
        """Deletes the entry that starts on `valid_from`."""
        with translating(LoadStoreError):
            cursor = self._db.connection.execute(
                "DELETE FROM physiology WHERE valid_from = ?", (valid_from.isoformat(),)
            )
        if cursor.rowcount == 0:
            raise PhysiologyEntryNotFoundError("no physiological parameters start on that day")

    def list_entries(self) -> Sequence[PhysiologyEntry]:
        """All the entries by ascending date."""
        with translating(LoadStoreError):
            rows = self._db.connection.execute(
                "SELECT valid_from, min_bpm, max_bpm, threshold_bpm, sex, "
                "bike_threshold_bpm, other_threshold_bpm "
                "FROM physiology ORDER BY valid_from"
            ).fetchall()
        try:
            return [
                PhysiologyEntry(
                    date.fromisoformat(row[0]),
                    Physiology(
                        HeartRateLimits(row[1], row[2]),
                        ThresholdHeartRate(row[3]),
                        Sex(row[4]),
                        _optional_threshold(row[5]),
                        _optional_threshold(row[6]),
                    ),
                )
                for row in rows
            ]
        except ValueError:  # the text read (a date) is not kept in the chain of causes
            raise LoadStoreError("physiological parameters invalid") from None


# `load_cache.hr_tss` is no longer used but the column stays, NOT NULL, until a later migration
# drops it; `load_cache.load` mirrors `trimp`, which holds the TRIMPeq, for the same reason.
_UNUSED_LOAD_COLUMN = 0.0


class SqliteLoadCache:
    """Load cache (`LoadCache` port) on SQLite: derived, regenerable."""

    def __init__(self, database: SqliteDatabase) -> None:
        self._db = database

    def get(self, identity: ActivityIdentity, fingerprint: LoadFingerprint) -> ActivityLoad | None:
        """The saved load, only if produced with this fingerprint."""
        with translating(LoadStoreError):
            row = self._db.connection.execute(
                "SELECT c.trimp, c.used_samples, c.discarded_samples, "
                "c.excluded_seconds, c.source, c.zone1_s, c.zone2_s, c.zone3_s, c.zone4_s, "
                "c.zone5_s FROM load_cache AS c "
                "JOIN activities AS a ON a.id = c.activity_id "
                "WHERE a.identity_key = ? AND c.fingerprint = ?",
                (identity_key(identity), fingerprint.hex),
            ).fetchone()
        if row is None:
            return None
        try:
            zones = ZoneSeconds((row[5], row[6], row[7], row[8], row[9]))
        except ValueError as exc:
            raise LoadStoreError("seconds in zone invalid") from exc
        return ActivityLoad(row[0], row[1], row[2], row[3], LoadSource(row[4]), zones)

    def put(
        self, identity: ActivityIdentity, fingerprint: LoadFingerprint, load: ActivityLoad
    ) -> None:
        """Saves or replaces the load; unknown activity → `UnknownActivityError`."""
        with translating(LoadStoreError):
            cursor = self._db.connection.execute(
                "INSERT INTO load_cache (activity_id, fingerprint, trimp, hr_tss, load, "
                "used_samples, discarded_samples, excluded_seconds, source, "
                "zone1_s, zone2_s, zone3_s, zone4_s, zone5_s) "
                "SELECT id, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ? FROM activities "
                "WHERE identity_key = ? "
                "ON CONFLICT (activity_id) DO UPDATE SET fingerprint = excluded.fingerprint, "
                "trimp = excluded.trimp, hr_tss = excluded.hr_tss, load = excluded.load, "
                "used_samples = excluded.used_samples, "
                "discarded_samples = excluded.discarded_samples, "
                "excluded_seconds = excluded.excluded_seconds, source = excluded.source, "
                "zone1_s = excluded.zone1_s, zone2_s = excluded.zone2_s, "
                "zone3_s = excluded.zone3_s, zone4_s = excluded.zone4_s, "
                "zone5_s = excluded.zone5_s",
                (
                    fingerprint.hex,
                    load.trimp_eq,
                    _UNUSED_LOAD_COLUMN,
                    load.trimp_eq,
                    load.used_samples,
                    load.discarded_samples,
                    load.excluded_seconds,
                    load.source.value,
                    *load.zone_seconds.values,
                    identity_key(identity),
                ),
            )
        if cursor.rowcount == 0:
            raise UnknownActivityError("activity not in the index")

    def invalidate(self, identity: ActivityIdentity) -> None:
        """Forgets the load of an activity (no effect if there is none)."""
        with translating(LoadStoreError):
            self._db.connection.execute(
                "DELETE FROM load_cache WHERE activity_id IN "
                "(SELECT id FROM activities WHERE identity_key = ?)",
                (identity_key(identity),),
            )

    def clear(self) -> None:
        """Forgets all the loads."""
        with translating(LoadStoreError):
            self._db.connection.execute("DELETE FROM load_cache")
