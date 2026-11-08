# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Track boxes of the activity index (`TrackBoxIndex` port) on SQLite.

The box columns of schema v8 are derived from the start point and the simplified polyline of the
same row. Only `?` parameters; nothing read from the database ends up in error messages
(positions are personal data).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from contextlib import AbstractContextManager
from typing import Final

from ponos_coach.activities.domain import GeoBox, GeoPoint
from ponos_coach.activities.ports import (
    ActivityIndexError,
    BoxCandidate,
    IndexedActivity,
    IndexRevision,
)
from ponos_coach.storage.sqlite_database import SqliteDatabase
from ponos_coach.storage.sqlite_stores import (
    ACTIVITY_COLUMNS,
    box_values,
    records_from_rows,
    translating,
)

_HAS_POSITION: Final = "(start_lat IS NOT NULL OR track_polyline IS NOT NULL)"
_MAX_CANDIDATES: Final = 5000
_SELECT_WITH_BOXES: Final = (
    "SELECT "  # noqa: S608 - module constants only
    + ACTIVITY_COLUMNS
    + ", track_south, track_west, track_north, track_east FROM activities "
    "ORDER BY start_ms, identity_key"
)
_RECORD_COLUMNS: Final = ACTIVITY_COLUMNS.count(",") + 1


def _box_of_row(row: Sequence[float | None]) -> GeoBox | None:
    if row[0] is None or row[1] is None or row[2] is None or row[3] is None:
        return None
    try:
        return GeoBox(row[0], row[1], row[2], row[3])
    except ValueError as exc:
        raise ActivityIndexError("invalid track box in the index") from exc


class SqliteTrackBoxIndex:
    """Track boxes on the profile's connection (`TrackBoxIndex` port)."""

    def __init__(self, database: SqliteDatabase) -> None:
        self._db = database

    def transaction(self) -> AbstractContextManager[None]:
        """See `TrackBoxIndex.transaction`."""
        return self._db.transaction()

    def revision(self) -> IndexRevision:
        """See `TrackBoxIndex.revision`."""
        with translating(ActivityIndexError):
            row = self._db.connection.execute(
                "SELECT COUNT(*), COALESCE(MAX(id), 0), COUNT(track_south) FROM activities"
            ).fetchone()
        return IndexRevision(int(row[0]), int(row[1]), int(row[2]))

    def list_by_start(self) -> Sequence[IndexedActivity]:
        """See `TrackBoxIndex.list_by_start`."""
        with translating(ActivityIndexError):
            rows = self._db.connection.execute(_SELECT_WITH_BOXES).fetchall()
        records = records_from_rows([row[:_RECORD_COLUMNS] for row in rows])
        return [
            IndexedActivity(record, _box_of_row(row[_RECORD_COLUMNS:]))
            for record, row in zip(records, rows, strict=True)
        ]

    def count_without_box(self) -> int:
        """See `TrackBoxIndex.count_without_box`."""
        with translating(ActivityIndexError):
            row = self._db.connection.execute(
                "SELECT COUNT(*) FROM activities WHERE track_south IS NULL AND "  # noqa: S608
                + _HAS_POSITION
            ).fetchone()
        return int(row[0])

    def candidates_without_box(self, after_row_id: int, limit: int) -> Sequence[BoxCandidate]:
        """See `TrackBoxIndex.candidates_without_box`; `limit` is capped at 5000."""
        with translating(ActivityIndexError):
            rows = self._db.connection.execute(
                "SELECT id, start_lat, start_lon, track_polyline FROM activities "  # noqa: S608
                "WHERE id > ? AND track_south IS NULL AND "
                + _HAS_POSITION
                + " ORDER BY id LIMIT ?",
                (after_row_id, max(0, min(limit, _MAX_CANDIDATES))),
            ).fetchall()
        try:
            return [
                BoxCandidate(
                    int(row[0]), None if row[1] is None else GeoPoint(row[1], row[2]), row[3]
                )
                for row in rows
            ]
        except ValueError as exc:
            raise ActivityIndexError("invalid position in the index") from exc

    def store_boxes(self, boxes: Mapping[int, GeoBox]) -> None:
        """See `TrackBoxIndex.store_boxes`."""
        parameters = [(*box_values(box), row_id) for row_id, box in sorted(boxes.items())]
        with translating(ActivityIndexError):
            self._db.connection.executemany(
                "UPDATE activities SET track_south = ?, track_west = ?, track_north = ?, "
                "track_east = ? WHERE id = ? AND track_south IS NULL",
                parameters,
            )
