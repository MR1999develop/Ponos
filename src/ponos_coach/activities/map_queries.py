# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Use cases of the map view: `SearchActivities`, `ActivitiesInArea`, `backfill_track_boxes`.

Read-only on the index, except the fill of the track boxes, which writes derived data. They run on
the data thread (the one that owns the profile's connection). Positions are personal data: no log.

The index is read once per revision (`ActivitySnapshots`) and filtered once per filter, so that a
pan or a zoom is a vectorised mask over columns, not a read of 50,000 rows. Explicit size limits:
at most `MAX_MAP_TRACKS` tracks per answer, each with at most `MAX_TRACK_POINTS` points.
"""

from __future__ import annotations

import itertools
from collections import OrderedDict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from zoneinfo import ZoneInfo

import numpy as np
from numpy.typing import NDArray

from ponos_coach.activities.decoding import ActivityFileError
from ponos_coach.activities.domain import (
    MAX_ACTIVITY_FILE_BYTES,
    MAX_TRACK_POINTS,
    MAX_TRACK_ZOOM,
    TRACK_TOLERANCE_M,
    ActivityFilter,
    ActivityIdentity,
    ActivityRecord,
    ActivityTrack,
    AreaResult,
    AreaTotals,
    FileFormat,
    GeoBox,
    GeoPoint,
    Sport,
    StartIndex,
    decode_polyline,
    identity_key,
    simplify_for_zoom,
    simplify_indices,
    track_box_of,
    zoom_tolerance_m,
)
from ponos_coach.activities.ports import (
    ActivityArchive,
    ArchiveError,
    BoxCandidate,
    IndexedActivity,
    IndexRevision,
    TrackBoxIndex,
)

MAX_MAP_TRACKS = 300  # tracks in one answer: the most recent ones
TRACK_MIN_ZOOM = 11  # below this zoom the map shows grouped start points, no tracks
_TRACK_CACHE_SIZE = 600  # simplified tracks kept from files (two answers' worth)
_ANTIMERIDIAN_JUMP_DEG = 180.0
_MIN_DRAWABLE_POINTS = 2  # a single point draws nothing
_BACKFILL_BATCH = 500

type TrackDecoder = Callable[[bytes, FileFormat], ActivityTrack | None]
"""Full track of the bytes of an archived file; raises `ActivityFileError` if unusable."""

type Segments = tuple[tuple[GeoPoint, ...], ...]


# --- Snapshots of the index ---------------------------------------------------------------


@dataclass(frozen=True, slots=True, eq=False)
class SearchResult:
    """Activities that pass a filter, with what the map needs to show their start points.

    `activities` are by ascending start. `start_indices[i]` is the position in `activities` of
    the i-th located activity, whose start is `(start_lat_deg[i], start_lon_deg[i])`; the
    activities without a start position (indoor, strength) are only counted in
    `without_position`. Read-only columns.
    """

    activities: tuple[ActivityRecord, ...]
    totals: AreaTotals
    without_position: int
    start_indices: NDArray[np.intp]
    start_lat_deg: NDArray[np.float64]
    start_lon_deg: NDArray[np.float64]


class _Selection:
    """The activities that pass one filter, in columns (private to this module)."""

    def __init__(
        self, items: Sequence[IndexedActivity], criteria: ActivityFilter, tz: ZoneInfo
    ) -> None:
        kept = [item for item in items if criteria.matches(item.record, tz)]
        self.records = tuple(item.record for item in kept)
        self.starts = StartIndex(self.records)
        for column in (self.starts.located, self.starts.lat_deg, self.starts.lon_deg):
            column.setflags(write=False)
        count = len(kept)
        self.has_box = np.zeros(count, dtype=bool)
        self.south = np.zeros(count)
        self.west = np.zeros(count)
        self.north = np.zeros(count)
        self.east = np.zeros(count)
        self.boxes: dict[int, GeoBox] = {}
        for i, item in enumerate(kept):
            box = item.track_box
            if box is not None:
                self.boxes[i] = box
                self.has_box[i] = True
                self.south[i], self.west[i], self.north[i], self.east[i] = (
                    box.south,
                    box.west,
                    box.north,
                    box.east,
                )

    def touching(self, area: GeoBox) -> NDArray[np.intp]:
        """Positions of the activities whose start or track box touches the area."""
        mask = np.zeros(len(self.records), dtype=bool)
        mask[self.starts.located[self.starts.mask_in(area)]] = True
        mask |= area.intersects_many(self.south, self.west, self.north, self.east) & self.has_box
        return np.flatnonzero(mask)


class ActivitySnapshots:
    """The index read once per revision and the last selection per filter.

    Not thread-safe: use it only from the data thread. A change in the index (activity added,
    boxes filled) is noticed through `TrackBoxIndex.revision()`, which is one cheap query.
    """

    def __init__(self, index: TrackBoxIndex, timezone: ZoneInfo) -> None:
        self._index = index
        self._timezone = timezone
        self._revision: IndexRevision | None = None
        self._items: tuple[IndexedActivity, ...] = ()
        self._selected: tuple[ActivityFilter, _Selection] | None = None

    def select(self, criteria: ActivityFilter) -> _Selection:
        """The selection for the filter, reading the index only if it changed."""
        revision = self._index.revision()
        if revision != self._revision:
            self._items = tuple(self._index.list_by_start())
            self._revision = revision
            self._selected = None
        if self._selected is None or self._selected[0] != criteria:
            self._selected = (criteria, _Selection(self._items, criteria, self._timezone))
        return self._selected[1]


class SearchActivities:
    """Use case "search activities": the activities that pass the filter, with totals."""

    def __init__(self, snapshots: ActivitySnapshots) -> None:
        self._snapshots = snapshots

    def __call__(self, criteria: ActivityFilter) -> SearchResult:
        """Result for the filter (an empty filter gives every activity).

        Raises:
            ActivityIndexError: the index cannot be read.
        """
        selection = self._snapshots.select(criteria)
        starts = selection.starts
        return SearchResult(
            selection.records,
            AreaTotals.of(selection.records),
            starts.without_position,
            starts.located,
            starts.lat_deg,
            starts.lon_deg,
        )


# --- Activities in an area ----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MapTrack:
    """A track ready to draw: the simplified segments (nothing is drawn between two of them)."""

    identity: ActivityIdentity
    sport: Sport
    segments: Segments


@dataclass(frozen=True, slots=True)
class AreaView:
    """What the map and the list show for an area.

    `in_area` are the filtered activities that start in the area, their totals and the number of
    filtered activities without a position. `tracks` (only from zoom `TRACK_MIN_ZOOM`) are the
    most recent tracks that touch the area, at most `MAX_MAP_TRACKS`; `tracks_omitted` counts the
    older ones left out.
    """

    in_area: AreaResult
    tracks: tuple[MapTrack, ...]
    tracks_omitted: int


def _split_at_antimeridian(points: Sequence[GeoPoint]) -> Segments:
    """Cuts a polyline where it jumps across the antimeridian, so no line spans the world."""
    pieces: list[list[GeoPoint]] = [[points[0]]]
    for before, after in itertools.pairwise(points):
        if abs(after.lon_deg - before.lon_deg) > _ANTIMERIDIAN_JUMP_DEG:
            pieces.append([])
        pieces[-1].append(after)
    return tuple(tuple(p) for p in pieces if len(p) >= _MIN_DRAWABLE_POINTS)


def _simplified(points: Sequence[GeoPoint], tolerance_m: float) -> tuple[GeoPoint, ...]:
    kept = simplify_indices(
        [p.lat_deg for p in points], [p.lon_deg for p in points], tolerance_m, MAX_TRACK_POINTS
    )
    return tuple(points[int(i)] for i in kept)


def _from_index_polyline(polyline: str, zoom: int, box: GeoBox) -> Segments:
    """The index polyline cut at the antimeridian, then simplified for the zoom.

    Cutting first matters: simplified as a whole, a line that crosses the antimeridian would lose
    its inner points and become one jump that cannot be drawn. A damaged polyline draws nothing.
    """
    try:
        points = decode_polyline(polyline)
    except ValueError:
        return ()
    if len(points) < _MIN_DRAWABLE_POINTS:
        return ()
    tolerance = zoom_tolerance_m(zoom, box.center()[0])
    return tuple(_simplified(piece, tolerance) for piece in _split_at_antimeridian(points))


class ActivitiesInArea:
    """Use case "activities in an area": starts, totals and, when zoomed in, the tracks.

    Tracks come from the index polyline when the pixel tolerance of the zoom is not finer than
    the one the index was built with (`TRACK_TOLERANCE_M`); at finer zooms the original file is
    read (hash-verified, size-capped) and simplified for the zoom with its gaps preserved. Files
    that cannot be used fall back to the index polyline. File-derived tracks are kept in a bounded
    cache.
    """

    def __init__(
        self,
        snapshots: ActivitySnapshots,
        archive: ActivityArchive,
        decode_track: TrackDecoder,
        *,
        max_file_bytes: int = MAX_ACTIVITY_FILE_BYTES,
    ) -> None:
        if max_file_bytes <= 0:
            raise ValueError("the byte cap must be positive")
        self._snapshots = snapshots
        self._archive = archive
        self._decode_track = decode_track
        self._max_file_bytes = max_file_bytes
        self._cache: OrderedDict[tuple[str, int], Segments] = OrderedDict()

    def __call__(self, criteria: ActivityFilter, area: GeoBox, zoom: int) -> AreaView:
        """View for the filter, the area and the zoom (clamped to 0 .. `MAX_TRACK_ZOOM`).

        Raises:
            ActivityIndexError: the index cannot be read.
        """
        level = max(0, min(MAX_TRACK_ZOOM, zoom))
        selection = self._snapshots.select(criteria)
        in_area = selection.starts.in_area(area)
        if level < TRACK_MIN_ZOOM:
            return AreaView(in_area, (), 0)
        touching = selection.touching(area)
        chosen = touching[-MAX_MAP_TRACKS:]  # records are by ascending start: the last are recent
        tracks: list[MapTrack] = []
        for position in chosen.tolist():
            record = selection.records[position]
            segments = self._segments(record, selection.boxes.get(position), level)
            if segments:
                tracks.append(MapTrack(record.identity, record.sport, segments))
        return AreaView(in_area, tuple(tracks), int(touching.size - chosen.size))

    def _segments(self, record: ActivityRecord, box: GeoBox | None, zoom: int) -> Segments:
        polyline = record.summary.track_polyline
        if polyline is None or box is None:
            return ()
        if zoom_tolerance_m(zoom, box.center()[0]) >= TRACK_TOLERANCE_M:
            return _from_index_polyline(polyline, zoom, box)
        key = (identity_key(record.identity), zoom)
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
        segments = self._from_file(record, zoom) or _from_index_polyline(polyline, zoom, box)
        self._cache[key] = segments
        while len(self._cache) > _TRACK_CACHE_SIZE:
            self._cache.popitem(last=False)
        return segments

    def _from_file(self, record: ActivityRecord, zoom: int) -> Segments:
        try:
            data = self._archive.read(record.archived, self._max_file_bytes)
            track = self._decode_track(data, record.file_format)
        except (ArchiveError, ActivityFileError):
            return ()
        return () if track is None else simplify_for_zoom(track, zoom)


# --- Fill of the track boxes ----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BackfillSummary:
    """Outcome of a fill pass: boxes written, rows that could not get one, and why it stopped.

    `unresolved` counts rows whose position data is unusable (a damaged polyline and no start
    point): they stay without a box and the next pass finds them again.
    """

    filled: int
    unresolved: int
    cancelled: bool


def _ignore_progress(_done: int, _total: int) -> None:
    """Progress not requested."""


def _never_cancelled() -> bool:
    return False


def _box_of(candidate: BoxCandidate) -> GeoBox | None:
    try:
        return track_box_of(candidate.start_point, candidate.track_polyline)
    except ValueError:  # damaged polyline: the start point alone still locates the activity
        return track_box_of(candidate.start_point, None)


def backfill_track_boxes(
    index: TrackBoxIndex,
    *,
    batch_size: int = _BACKFILL_BATCH,
    report: Callable[[int, int], None] = _ignore_progress,
    is_cancelled: Callable[[], bool] = _never_cancelled,
) -> BackfillSummary:
    """Fills the track box of the activities indexed before schema v8, in resumable batches.

    Each batch is its own transaction, so an interruption (cancel, crash, closing the app) keeps
    what was done and the next call carries on from the rows still without a box. Reads only the
    index, never the archived files. `report(done, total)` is called before the first batch and
    after each one; `is_cancelled()` is checked before every batch. Calling it again on a
    complete index does nothing.

    Raises:
        ValueError: `batch_size` below 1.
        ActivityIndexError: the index cannot be read or written (what was done is kept).
    """
    if batch_size < 1:
        raise ValueError("the batch size must be at least 1")
    total = index.count_without_box()
    report(0, total)
    filled = unresolved = 0
    last_row_id = 0
    while not is_cancelled():
        batch = index.candidates_without_box(last_row_id, batch_size)
        if not batch:
            return BackfillSummary(filled, unresolved, cancelled=False)
        boxes = {c.row_id: box for c in batch if (box := _box_of(c)) is not None}
        with index.transaction():
            index.store_boxes(boxes)
        filled += len(boxes)
        unresolved += len(batch) - len(boxes)
        last_row_id = batch[-1].row_id
        report(filled + unresolved, total)
    return BackfillSummary(filled, unresolved, cancelled=True)
