# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Full track of an activity: positions and altitude on the heart-rate time axis.

The full track is not stored in the index (the simplified polyline of `ActivitySummary` is):
it is read again from the archived file when needed. Positions are sensitive personal data:
never in logs. A position gap (record without GPS, different GPX segment or a pause longer than
`TRACK_GAP_S`) splits the track into segments: nothing is drawn between two segments.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from ponos_coach.activities.domain.geo import (
    EARTH_RADIUS_M,
    MAX_POLYLINE_CHARS,
    GeoPoint,
    decode_polyline,
    encode_polyline,
    simplify_indices,
)
from ponos_coach.activities.domain.geo_box import GeoBox
from ponos_coach.activities.domain.limits import (
    MAX_RAW_TRACK_POINTS,
    MAX_TRACK_POINTS,
    MAX_TRACK_ZOOM,
    TRACK_GAP_S,
)

_TILE_PIXELS = 256  # side of a map tile
_SEGMENT_SEPARATOR = " "  # outside the polyline alphabet (ASCII 63-126)
_MAX_TOLERANCE_DOUBLINGS = 24
_MIN_SEGMENT_POINTS = 2


def _frozen(values: object) -> NDArray[np.float64]:
    array = np.array(values, dtype=np.float64)
    array.setflags(write=False)
    return array


@dataclass(frozen=True, slots=True, eq=False)
class TrackSegment:
    """A continuous stretch: seconds from the first record (like the HR series), position, altitude.

    Invariants: at least one point; columns of equal length; finite, strictly increasing
    seconds; finite latitude in [-90, 90] and longitude in [-180, 180]; altitude, if present, in
    metres, `NaN` where unknown but never infinite. Arrays are copied and read-only.
    """

    seconds: NDArray[np.float64]
    lat_deg: NDArray[np.float64]
    lon_deg: NDArray[np.float64]
    altitude_m: NDArray[np.float64] | None = None

    def __post_init__(self) -> None:
        seconds, lat, lon = _frozen(self.seconds), _frozen(self.lat_deg), _frozen(self.lon_deg)
        altitude = None if self.altitude_m is None else _frozen(self.altitude_m)
        if seconds.ndim != 1 or seconds.size == 0 or not (seconds.shape == lat.shape == lon.shape):
            raise ValueError("empty segment or columns of different length")
        if altitude is not None and altitude.shape != seconds.shape:
            raise ValueError("altitude has a different length from the positions")
        if not (np.isfinite(seconds).all() and np.isfinite(lat).all() and np.isfinite(lon).all()):
            raise ValueError("seconds and coordinates must be finite")
        if (np.abs(lat) > 90.0).any() or (np.abs(lon) > 180.0).any():
            raise ValueError("coordinates out of range")
        if seconds.size > 1 and not (np.diff(seconds) > 0).all():
            raise ValueError("seconds must be strictly increasing")
        if altitude is not None and np.isinf(altitude).any():
            raise ValueError("altitude cannot be infinite")
        object.__setattr__(self, "seconds", seconds)
        object.__setattr__(self, "lat_deg", lat)
        object.__setattr__(self, "lon_deg", lon)
        object.__setattr__(self, "altitude_m", altitude)

    def __len__(self) -> int:
        return int(self.seconds.size)


@dataclass(frozen=True, slots=True, eq=False)
class ActivityTrack:
    """Segments of an activity track, in time order.

    Invariants: at least one segment; at most `MAX_RAW_TRACK_POINTS` points overall; segments
    do not overlap in time (the first second of a segment follows the last of the previous one).
    """

    segments: tuple[TrackSegment, ...]

    def __post_init__(self) -> None:
        if not self.segments:
            raise ValueError("a track has at least one segment")
        if sum(len(s) for s in self.segments) > MAX_RAW_TRACK_POINTS:
            raise ValueError("too many points in the track")
        for before, after in zip(self.segments, self.segments[1:], strict=False):
            if after.seconds[0] <= before.seconds[-1]:
                raise ValueError("segments overlap in time")

    @property
    def point_count(self) -> int:
        """Number of points across all segments."""
        return sum(len(s) for s in self.segments)

    def bounding_box(self) -> GeoBox:
        """Minimal box of the track (antimeridian included)."""
        lat = np.concatenate([s.lat_deg for s in self.segments])
        lon = np.concatenate([s.lon_deg for s in self.segments])
        return GeoBox.of_points(lat, lon)


def build_track(
    seconds: NDArray[np.float64],
    lat_deg: NDArray[np.float64],
    lon_deg: NDArray[np.float64],
    altitude_m: NDArray[np.float64] | None,
    run_id: NDArray[np.intp],
    max_gap_s: float = TRACK_GAP_S,
) -> ActivityTrack | None:
    """Track from the columns of the points with a valid position, or `None` if there are none.

    `run_id` identifies the source stretch of each point (for example the `trkseg` of a GPX, or
    the number of position gaps already seen in a FIT): when it changes, the segment is split.
    It also splits when two consecutive points are more than `max_gap_s` seconds apart. A point
    whose seconds are not greater than all previous ones (repeated or going back) is dropped,
    as for the HR series: the first of a repetition wins.

    Raises:
        ValueError: columns of different length or values invalid for a `TrackSegment`.
    """
    if not (seconds.shape == lat_deg.shape == lon_deg.shape == run_id.shape):
        raise ValueError("columns of different length")
    if seconds.size == 0:
        return None
    keep = np.ones(seconds.size, dtype=bool)
    keep[1:] = seconds[1:] > np.maximum.accumulate(seconds)[:-1]
    seconds, lat_deg, lon_deg, run_id = seconds[keep], lat_deg[keep], lon_deg[keep], run_id[keep]
    altitude = None if altitude_m is None else altitude_m[keep]
    breaks = (run_id[1:] != run_id[:-1]) | (np.diff(seconds) > max_gap_s)
    starts = np.concatenate(([0], np.flatnonzero(breaks) + 1, [seconds.size]))
    segments = tuple(
        TrackSegment(
            seconds[a:b], lat_deg[a:b], lon_deg[a:b], None if altitude is None else altitude[a:b]
        )
        for a, b in zip(starts[:-1].tolist(), starts[1:].tolist(), strict=True)
    )
    return ActivityTrack(segments)


def zoom_tolerance_m(zoom: int, lat_deg: float) -> float:
    """Simplification tolerance for a zoom level: one map pixel, in metres.

    Metres per pixel of a 256 px tile map at the given latitude (Web Mercator):
    `2π R cos(lat) / (256 · 2^zoom)`. Zoom is clamped to [0, `MAX_TRACK_ZOOM`]; near the poles
    the tolerance does not go below the one at 85 degrees.

    Raises:
        ValueError: non-finite latitude.
    """
    if not math.isfinite(lat_deg):
        raise ValueError("latitude must be finite")
    level = max(0, min(MAX_TRACK_ZOOM, zoom))
    scale = math.cos(math.radians(min(abs(lat_deg), 85.0)))
    return 2.0 * math.pi * EARTH_RADIUS_M * scale / (_TILE_PIXELS * 2.0**level)


def _simplified_segments(track: ActivityTrack, tolerance_m: float) -> list[tuple[GeoPoint, ...]]:
    result: list[tuple[GeoPoint, ...]] = []
    for segment in track.segments:
        kept = simplify_indices(segment.lat_deg, segment.lon_deg, tolerance_m, MAX_TRACK_POINTS)
        if len(kept) >= _MIN_SEGMENT_POINTS:  # a single point draws nothing
            result.append(
                tuple(GeoPoint(float(segment.lat_deg[i]), float(segment.lon_deg[i])) for i in kept)
            )
    return result


def _fit_budget(segments: list[tuple[GeoPoint, ...]]) -> list[tuple[GeoPoint, ...]]:
    """Keeps the longest segments until the total fits `MAX_TRACK_POINTS` (time order kept)."""
    order = sorted(range(len(segments)), key=lambda i: (-len(segments[i]), i))
    chosen: set[int] = set()
    total = 0
    for index in order:
        if total + len(segments[index]) <= MAX_TRACK_POINTS:
            chosen.add(index)
            total += len(segments[index])
    return [segments[i] for i in sorted(chosen)]


def simplify_for_zoom(track: ActivityTrack, zoom: int) -> tuple[tuple[GeoPoint, ...], ...]:
    """Segments simplified (Douglas-Peucker) with a one-pixel tolerance at the zoom level.

    Each segment keeps its first and last point; single-point segments are omitted. If the total
    exceeds `MAX_TRACK_POINTS` the tolerance doubles until it fits (at most 24 times), then the
    longest segments are kept: the result can always be encoded with `encode_segments`.
    Deterministic; a track that vanishes entirely gives an empty tuple.
    """
    lat = float(np.mean(np.concatenate([s.lat_deg for s in track.segments])))
    tolerance = zoom_tolerance_m(zoom, lat)
    segments = _simplified_segments(track, tolerance)
    for _ in range(_MAX_TOLERANCE_DOUBLINGS):
        if sum(len(s) for s in segments) <= MAX_TRACK_POINTS:
            break
        tolerance *= 2.0
        segments = _simplified_segments(track, tolerance)
    return tuple(_fit_budget(segments))


def encode_segments(segments: Sequence[Sequence[GeoPoint]]) -> str:
    """Compact encoding of segments: one polyline (1e-5 degrees, about 1 m) per segment,
    separated by a space. No segments gives the empty string.

    Raises:
        ValueError: more than `MAX_TRACK_POINTS` points overall, or an empty segment.
    """
    if sum(len(s) for s in segments) > MAX_TRACK_POINTS:
        raise ValueError("too many points for an encoded track")
    if any(len(s) == 0 for s in segments):
        raise ValueError("empty segment")
    return _SEGMENT_SEPARATOR.join(encode_polyline(s) for s in segments)


def decode_segments(encoded: str) -> tuple[tuple[GeoPoint, ...], ...]:
    """Segments of a string produced by `encode_segments` (validated like a trust boundary).

    Raises:
        ValueError: string too long, empty segment, invalid characters or values, more than
            `MAX_TRACK_POINTS` points overall.
    """
    if len(encoded) > MAX_POLYLINE_CHARS + MAX_TRACK_POINTS:
        raise ValueError("encoded track too long")
    if not encoded:
        return ()
    segments = tuple(decode_polyline(part) for part in encoded.split(_SEGMENT_SEPARATOR))
    if any(len(s) == 0 for s in segments):
        raise ValueError("empty segment")
    if sum(len(s) for s in segments) > MAX_TRACK_POINTS:
        raise ValueError("too many points in an encoded track")
    return segments
