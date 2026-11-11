# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Geographic points, track simplification and encoded polylines.

GPS positions are sensitive personal data: this module neither logs nor prints them
(the `repr` of `GeoPoint` shows them, so callers must not put it in logs).
"""

from __future__ import annotations

import heapq
import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from numpy.typing import ArrayLike, NDArray

from ponos_coach.activities.domain.limits import MAX_TRACK_POINTS, TRACK_TOLERANCE_M

EARTH_RADIUS_M = 6_371_008.8  # mean Earth radius (IUGG), in meters
_POLYLINE_SCALE = 1e5  # standard polyline precision: 1e-5 degrees
_POLYLINE_FIRST_CHAR = 63  # '?'
_POLYLINE_LAST_CHAR = 126  # '~'
_CHUNK_BITS = 5
_CHUNK_MASK = 0x1F
_CONTINUATION = 0x20
_MAX_CHUNKS_PER_VALUE = 7  # 35 bits: beyond any valid coordinate delta
# A value takes at most 6 characters for valid coordinates (delta up to 360 degrees).
MAX_POLYLINE_CHARS = MAX_TRACK_POINTS * 2 * 6


@dataclass(frozen=True, slots=True)
class GeoPoint:
    """A position in decimal degrees: latitude in [-90, 90], longitude in [-180, 180]."""

    lat_deg: float
    lon_deg: float

    def __post_init__(self) -> None:
        if not (math.isfinite(self.lat_deg) and math.isfinite(self.lon_deg)):
            raise ValueError("coordinates must be finite")
        if not -90.0 <= self.lat_deg <= 90.0:
            raise ValueError("latitude outside [-90, 90]")
        if not -180.0 <= self.lon_deg <= 180.0:
            raise ValueError("longitude outside [-180, 180]")


def _farthest(
    x: NDArray[np.float64], y: NDArray[np.float64], first: int, last: int
) -> tuple[float, int]:
    """Distance (m) and index of the interior point farthest from the first-last segment."""
    px, py = x[first + 1 : last] - x[first], y[first + 1 : last] - y[first]
    sx, sy = x[last] - x[first], y[last] - y[first]
    length_sq = sx * sx + sy * sy
    if length_sq == 0.0:
        distance = np.hypot(px, py)
    else:
        t = np.clip((px * sx + py * sy) / length_sq, 0.0, 1.0)
        distance = np.hypot(px - t * sx, py - t * sy)
    offset = int(np.argmax(distance))
    return float(distance[offset]), first + 1 + offset


def _projected(
    lat_deg: NDArray[np.float64], lon_deg: NDArray[np.float64]
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """Planar coordinates in meters (equirectangular around the mean latitude)."""
    lat = np.radians(lat_deg)
    lon = np.unwrap(np.radians(lon_deg))  # crosses the antimeridian without jumps
    scale = math.cos(float(lat.mean()))
    x = (lon - lon[0]) * scale * EARTH_RADIUS_M
    y = (lat - lat[0]) * EARTH_RADIUS_M
    return x, y


def _columns(
    lat_deg: ArrayLike, lon_deg: ArrayLike, tolerance_m: float, max_points: int
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    lat = np.asarray(lat_deg, dtype=np.float64)
    lon = np.asarray(lon_deg, dtype=np.float64)
    if lat.ndim != 1 or lat.shape != lon.shape:
        raise ValueError("two one-dimensional columns of the same length are required")
    if not (math.isfinite(tolerance_m) and tolerance_m >= 0.0) or max_points < 2:
        raise ValueError("invalid tolerance or fewer than 2 points allowed")
    if not (np.isfinite(lat).all() and np.isfinite(lon).all()):
        raise ValueError("coordinates must be finite")
    return lat, lon


def _split_until(
    x: NDArray[np.float64], y: NDArray[np.float64], tolerance_m: float, max_points: int
) -> NDArray[np.bool_]:
    """Mask of the points kept: the stretch with the largest deviation is always split."""
    keep = np.zeros(x.size, dtype=bool)
    keep[0] = keep[-1] = True
    kept = 2
    # Heap of (-deviation, first, last, index): on ties the leftmost stretch wins.
    heap: list[tuple[float, int, int, int]] = []

    def push(first: int, last: int) -> None:
        if last - first >= 2:  # at least one interior point is needed
            distance, index = _farthest(x, y, first, last)
            heapq.heappush(heap, (-distance, first, last, index))

    push(0, x.size - 1)
    while heap and kept < max_points:
        negative, first, last, index = heapq.heappop(heap)
        if -negative <= tolerance_m:
            break
        keep[index] = True
        kept += 1
        push(first, index)
        push(index, last)
    return keep


def simplify_indices(
    lat_deg: ArrayLike,
    lon_deg: ArrayLike,
    tolerance_m: float = TRACK_TOLERANCE_M,
    max_points: int = MAX_TRACK_POINTS,
) -> NDArray[np.intp]:
    """Indexes (ascending) of the points kept by Ramer-Douglas-Peucker, in vectorized time.

    Like `simplify_track` but on coordinate columns, without creating an object per point.
    First and last point always stay. The algorithm is iterative (no recursion): it always splits
    the stretch with the largest deviation and stops when it does not exceed
    `tolerance_m` or `max_points` points have been kept; the cap therefore keeps the
    most significant points. Deterministic.

    Raises:
        ValueError: columns of different length or not one-dimensional, non-finite values,
            negative or non-finite tolerance, `max_points` < 2.
    """
    lat, lon = _columns(lat_deg, lon_deg, tolerance_m, max_points)
    if lat.size <= 2:
        return np.arange(lat.size, dtype=np.intp)
    x, y = _projected(lat, lon)
    return np.flatnonzero(_split_until(x, y, tolerance_m, max_points))


def simplify_track(
    points: Sequence[GeoPoint],
    tolerance_m: float = TRACK_TOLERANCE_M,
    max_points: int = MAX_TRACK_POINTS,
) -> tuple[GeoPoint, ...]:
    """Track simplified with Ramer-Douglas-Peucker (iterative) and a cap on points.

    Each discarded point is at most `tolerance_m` meters from the stretch that replaces it, except
    when the `max_points` cap (project default: 2000) forces discarding more.
    First and last point stay; order is preserved; a track that is already short and without points
    redundant is returned unchanged. Deterministic.

    Raises:
        ValueError: negative or non-finite tolerance, `max_points` < 2.
    """
    indices = simplify_indices(
        [p.lat_deg for p in points], [p.lon_deg for p in points], tolerance_m, max_points
    )
    return tuple(points[int(i)] for i in indices)


def _encode_value(value: int, out: list[str]) -> None:
    value = ~(value << 1) if value < 0 else value << 1
    while value >= _CONTINUATION:
        out.append(chr(((value & _CHUNK_MASK) | _CONTINUATION) + _POLYLINE_FIRST_CHAR))
        value >>= _CHUNK_BITS
    out.append(chr(value + _POLYLINE_FIRST_CHAR))


def encode_polyline(points: Sequence[GeoPoint]) -> str:
    """Encoded polyline (standard algorithm, 1e-5 degree precision, about 1 m).

    Each coordinate is rounded to 1e-5 degrees; decoding recovers the point within
    0.5e-5 degrees. An empty sequence gives the empty string.

    Raises:
        ValueError: more than `MAX_TRACK_POINTS` points (the result could not be decoded).
    """
    if len(points) > MAX_TRACK_POINTS:
        raise ValueError("too many points for a polyline")
    scaled = np.rint(
        np.array([[p.lat_deg, p.lon_deg] for p in points], dtype=np.float64).reshape(-1, 2)
        * _POLYLINE_SCALE
    ).astype(np.int64)
    deltas = np.diff(scaled, axis=0, prepend=np.zeros((1, 2), dtype=np.int64))
    out: list[str] = []
    for value in deltas.ravel().tolist():
        _encode_value(int(value), out)
    return "".join(out)


def _decode_values(encoded: str) -> list[int]:
    """Integer values (signed) of a polyline, in order."""
    values: list[int] = []
    accumulated = shift = chunks = 0
    for char in encoded:
        code = ord(char) - _POLYLINE_FIRST_CHAR
        if not 0 <= code <= _POLYLINE_LAST_CHAR - _POLYLINE_FIRST_CHAR:
            raise ValueError("invalid character in a polyline")
        chunks += 1
        if chunks > _MAX_CHUNKS_PER_VALUE:
            raise ValueError("value too large in a polyline")
        accumulated |= (code & _CHUNK_MASK) << shift
        shift += _CHUNK_BITS
        if code < _CONTINUATION:
            values.append(~(accumulated >> 1) if accumulated & 1 else accumulated >> 1)
            accumulated = shift = chunks = 0
    if chunks:
        raise ValueError("incomplete polyline")
    return values


def decode_polyline(encoded: str) -> tuple[GeoPoint, ...]:
    """Points of a polyline encoded with `encode_polyline`.

    Raises:
        ValueError: string longer than `MAX_POLYLINE_CHARS`, characters outside the
            ASCII range 63-126, incomplete value or pair, values too large or coordinates outside
            range.
    """
    if len(encoded) > MAX_POLYLINE_CHARS:
        raise ValueError("polyline too long")
    values = _decode_values(encoded)
    if len(values) % 2:
        raise ValueError("incomplete polyline")
    lat = lon = 0
    points: list[GeoPoint] = []
    for d_lat, d_lon in zip(values[0::2], values[1::2], strict=True):
        lat += d_lat
        lon += d_lon
        points.append(GeoPoint(lat / _POLYLINE_SCALE, lon / _POLYLINE_SCALE))
    return tuple(points)
