# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Activities starting inside a map area, with the totals for the list on the right."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from ponos_coach.activities.domain.geo import GeoPoint, decode_polyline
from ponos_coach.activities.domain.geo_box import GeoBox
from ponos_coach.activities.domain.record import ActivityRecord
from ponos_coach.activities.domain.track import decode_segments

_SECONDS_PER_HOUR = 3600.0
_METERS_PER_KM = 1000.0


@dataclass(frozen=True, slots=True)
class AreaTotals:
    """Totals of a group of activities: count, kilometres, ascent (m) and moving hours.

    Distance and ascent add up only the activities that declare them.
    """

    count: int
    distance_km: float
    ascent_m: float
    moving_hours: float

    @classmethod
    def of(cls, records: Sequence[ActivityRecord]) -> AreaTotals:
        """Totals of the given activities (exact sum with `math.fsum`)."""
        distance = math.fsum(r.summary.distance_m or 0.0 for r in records)
        ascent = math.fsum(r.summary.ascent_m or 0.0 for r in records)
        moving = math.fsum(r.timer_s for r in records)
        return cls(len(records), distance / _METERS_PER_KM, ascent, moving / _SECONDS_PER_HOUR)


@dataclass(frozen=True, slots=True)
class AreaResult:
    """Outcome of an area search: activities in the area, totals and activities without position.

    `activities` keeps the input order. `without_position` counts, among the given activities,
    those with no start position (indoor, strength): they are not on the map whatever the
    area, and are not part of `activities` or of the totals.
    """

    activities: tuple[ActivityRecord, ...]
    totals: AreaTotals
    without_position: int


class StartIndex:
    """Start positions of a set of activities, in columns, for fast querying.

    Built once (for example at every filter change) and queried at every map move: the area
    mask is vectorised, with no per-activity loop. Immutable after construction.
    """

    def __init__(self, records: Sequence[ActivityRecord]) -> None:
        self._records = tuple(records)
        located: list[int] = []
        lat: list[float] = []
        lon: list[float] = []
        for index, record in enumerate(self._records):
            point = record.summary.start_point
            if point is not None:
                located.append(index)
                lat.append(point.lat_deg)
                lon.append(point.lon_deg)
        self._located = np.array(located, dtype=np.intp)
        self._lat = np.array(lat, dtype=np.float64)
        self._lon = np.array(lon, dtype=np.float64)
        self._without_position = len(self._records) - len(located)

    @property
    def without_position(self) -> int:
        """Activities without a start position."""
        return self._without_position

    @property
    def located(self) -> NDArray[np.intp]:
        """Positions (in the input order) of the activities that have a start position."""
        return self._located

    @property
    def lat_deg(self) -> NDArray[np.float64]:
        """Start latitudes of the located activities (column aligned with `located`)."""
        return self._lat

    @property
    def lon_deg(self) -> NDArray[np.float64]:
        """Start longitudes of the located activities (column aligned with `located`)."""
        return self._lon

    def mask_in(self, area: GeoBox) -> NDArray[np.bool_]:
        """Mask, over the located activities only, of those that start in the area."""
        return area.contains_many(self._lat, self._lon)

    def in_area(self, area: GeoBox) -> AreaResult:
        """The activities that start in the area (edges included), with totals."""
        chosen = self._located[self.mask_in(area)]
        found = tuple(self._records[int(i)] for i in chosen)
        return AreaResult(found, AreaTotals.of(found), self._without_position)


def activities_in_area(records: Sequence[ActivityRecord], area: GeoBox) -> AreaResult:
    """Activities (already filtered) starting in the area, with totals and the unlocated count.

    To query several areas on the same set, build a `StartIndex` instead.
    """
    return StartIndex(records).in_area(area)


def track_touches_area(track_polyline: str, area: GeoBox) -> bool:
    """Whether at least one point of the simplified track (polyline) falls in the area.

    For the "start or track in the area" search on the few activities near the edge.

    Raises:
        ValueError: the track is not a valid polyline.
    """
    for segment in decode_segments(track_polyline):
        lat = np.array([p.lat_deg for p in segment], dtype=np.float64)
        lon = np.array([p.lon_deg for p in segment], dtype=np.float64)
        if area.contains_many(lat, lon).any():
            return True
    return False


def track_box_of(start_point: GeoPoint | None, track_polyline: str | None) -> GeoBox | None:
    """Box of the indexed track: the start point and the points of the simplified polyline.

    This is what the index stores (schema v8) to find the tracks that touch a map area without
    reading files. It is derived from data already in the index, so it can always be rebuilt;
    being built on the simplified track (tolerance `TRACK_TOLERANCE_M`) it can be smaller than the
    box of the full track by at most that tolerance. `None` without any position.

    Raises:
        ValueError: the track is not a valid polyline.
    """
    points: list[GeoPoint] = [] if start_point is None else [start_point]
    if track_polyline is not None:
        points.extend(decode_polyline(track_polyline))
    if not points:
        return None
    return GeoBox.of_points([p.lat_deg for p in points], [p.lon_deg for p in points])
