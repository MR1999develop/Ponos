# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Geographic bounding box (bbox) with antimeridian support: containment and minimal box."""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from numpy.typing import ArrayLike, NDArray

_FULL_TURN = 360.0


def _wrap_longitude(lon_deg: float) -> float:
    """Longitude brought into [-180, 180] (a map shows copies of the world).

    A value already in range is returned unchanged: the modulo would add 1e-14 degree errors.
    """
    if -180.0 <= lon_deg <= 180.0:
        return lon_deg
    return (lon_deg + 180.0) % _FULL_TURN - 180.0


def _clamp_latitude(lat_deg: float) -> float:
    return max(-90.0, min(90.0, lat_deg))


@dataclass(frozen=True, slots=True)
class GeoBox:
    """A box in degrees: `south <= north` in [-90, 90], `west` and `east` in [-180, 180].

    If `west > east` the box crosses the antimeridian (from `west` eastwards to 180 and from
    -180 to `east`). `west == -180` and `east == 180` is the whole globe in longitude. Edges are
    included; longitude 180 and -180 are the same meridian.
    """

    south: float
    west: float
    north: float
    east: float

    def __post_init__(self) -> None:
        if not all(math.isfinite(v) for v in (self.south, self.west, self.north, self.east)):
            raise ValueError("box limits must be finite")
        if not -90.0 <= self.south <= self.north <= 90.0:
            raise ValueError("invalid box latitudes")
        if not (-180.0 <= self.west <= 180.0 and -180.0 <= self.east <= 180.0):
            raise ValueError("box longitudes outside [-180, 180]")

    @property
    def crosses_antimeridian(self) -> bool:
        """Whether the box crosses the antimeridian (west greater than east)."""
        return self.west > self.east

    @classmethod
    def from_view(cls, south: float, west: float, north: float, east: float) -> GeoBox:
        """Box from the area shown by a map, which may extend beyond the globe.

        Latitudes are clamped to [-90, 90] (and swapped if inverted); a width of 360 degrees or
        more is the whole globe; otherwise longitudes are brought back into [-180, 180].

        Raises:
            ValueError: non-finite values, or an east longitude smaller than the west one.
        """
        if not all(math.isfinite(v) for v in (south, west, north, east)):
            raise ValueError("view limits must be finite")
        if east < west:
            raise ValueError("the east of the view precedes the west")
        low, high = sorted((_clamp_latitude(south), _clamp_latitude(north)))
        if east - west >= _FULL_TURN:
            return cls(low, -180.0, high, 180.0)
        return cls(low, _wrap_longitude(west), high, _wrap_longitude(east))

    @classmethod
    def of_points(cls, lat_deg: ArrayLike, lon_deg: ArrayLike) -> GeoBox:
        """Minimal box containing the points, choosing the short side of the antimeridian.

        The longitude span is the complement of the widest point-free interval on the circle.

        Raises:
            ValueError: no points, columns of different length, non-finite or out-of-range
                values.
        """
        lat = np.asarray(lat_deg, dtype=np.float64)
        lon = np.asarray(lon_deg, dtype=np.float64)
        if lat.ndim != 1 or lat.shape != lon.shape or lat.size == 0:
            raise ValueError("two non-empty columns of the same length are required")
        if not (np.isfinite(lat).all() and np.isfinite(lon).all()):
            raise ValueError("coordinates must be finite")
        if (np.abs(lat) > 90.0).any() or (np.abs(lon) > 180.0).any():
            raise ValueError("coordinates out of range")
        ordered = np.unique(lon)
        gaps = np.append(np.diff(ordered), ordered[0] + _FULL_TURN - ordered[-1])
        widest = int(np.argmax(gaps))  # on a tie the first wins: deterministic
        if widest == ordered.size - 1:  # the widest empty interval is the one on the antimeridian
            west, east = float(ordered[0]), float(ordered[-1])
        else:
            west, east = float(ordered[widest + 1]), float(ordered[widest])
        return cls(float(lat.min()), west, float(lat.max()), east)

    def contains(self, lat_deg: float, lon_deg: float) -> bool:
        """Whether the point is inside the box (edges included)."""
        mask = self.contains_many(
            np.array([lat_deg], dtype=np.float64), np.array([lon_deg], dtype=np.float64)
        )
        return bool(mask[0])

    def contains_many(
        self, lat_deg: NDArray[np.float64], lon_deg: NDArray[np.float64]
    ) -> NDArray[np.bool_]:
        """Mask of the points inside the box, vectorised."""
        in_lat = (lat_deg >= self.south) & (lat_deg <= self.north)
        # 180 and -180 are the same meridian: the equivalent value is tried as well.
        twin = np.where(lon_deg == 180.0, -180.0, np.where(lon_deg == -180.0, 180.0, lon_deg))
        return in_lat & (self._lon_inside(lon_deg) | self._lon_inside(twin))

    def _lon_inside(self, lon_deg: NDArray[np.float64]) -> NDArray[np.bool_]:
        if self.west <= self.east:
            return (lon_deg >= self.west) & (lon_deg <= self.east)
        return (lon_deg >= self.west) | (lon_deg <= self.east)

    def center(self) -> tuple[float, float]:
        """Centre (latitude, longitude), also across the antimeridian."""
        width = self.east - self.west + (_FULL_TURN if self.crosses_antimeridian else 0.0)
        return (self.south + self.north) / 2.0, _wrap_longitude(self.west + width / 2.0)
