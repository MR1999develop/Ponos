# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Smoothed speed over time, derived from the cumulative distance of an activity.

Pure computation for the speed and pace chart of the activity detail. The distance of a FIT
file already covers the files that only record a speed (the reader integrates it), so this is the
single source. Pauses and stops are holes (NaN), never peaks.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from ponos_coach.activities.domain.distance_series import DistanceSeries

# Width of the window the speed is averaged over. GPS distance steps are noisy second by second
# (a 1 s pace swings by minutes per kilometre), and 30 s is long enough to settle that and short
# enough to follow an interval or a hill.
SPEED_WINDOW_S = 30.0
# Below this speed (1.8 km/h, a pace of 33:20 min/km) the athlete is standing or fidgeting: the
# pace there would run to infinity, so the point is a hole. Slower than any deliberate walk.
MIN_MOVING_SPEED_M_S = 0.5
# A longer interval between two records is a pause (the same limit as the heart-rate load and the
# reader's distance): nothing is drawn across it and the window never reaches over it.
PAUSE_GAP_S = 10.0
# Points kept for drawing; a long session is thinned, keeping the edges of every hole.
MAX_SPEED_POINTS = 3000


@dataclass(frozen=True, slots=True, eq=False)
class SpeedSeries:
    """Speed in metres per second over seconds from the first record, in columns.

    NaN marks a hole: a pause (a point is placed in the middle of it, so no line crosses it) or
    a stretch slower than `MIN_MOVING_SPEED_M_S`. Invariants: at least one finite speed, same
    length, strictly increasing seconds, speeds non-negative where finite. Arrays are copied
    and read-only.
    """

    seconds: NDArray[np.float64]
    meters_per_second: NDArray[np.float64]

    def __post_init__(self) -> None:
        seconds = np.array(self.seconds, dtype=np.float64)
        speed = np.array(self.meters_per_second, dtype=np.float64)
        if seconds.ndim != 1 or seconds.shape != speed.shape or seconds.size == 0:
            raise ValueError("empty series or columns of different length")
        if not np.isfinite(seconds).all():
            raise ValueError("the seconds must be finite")
        if seconds.size > 1 and not (np.diff(seconds) > 0).all():
            raise ValueError("seconds must be strictly increasing")
        finite = np.isfinite(speed)
        if not finite.any() or (speed[finite] < 0).any() or np.isinf(speed).any():
            raise ValueError("speeds must be non-negative and at least one must be finite")
        seconds.setflags(write=False)
        speed.setflags(write=False)
        object.__setattr__(self, "seconds", seconds)
        object.__setattr__(self, "meters_per_second", speed)

    def __len__(self) -> int:
        return int(self.seconds.size)


def _segment_of(seconds: NDArray[np.float64]) -> NDArray[np.intp]:
    """Index of the continuous stretch each sample belongs to (a pause starts a new one)."""
    breaks = np.diff(seconds) > PAUSE_GAP_S
    return np.concatenate(([0], np.cumsum(breaks))).astype(np.intp)


def _windowed_speed(distance: DistanceSeries, segment: NDArray[np.intp]) -> NDArray[np.float64]:
    """Mean speed over `SPEED_WINDOW_S` centred on each sample and cut at the edges of its
    stretch (NaN where the cut leaves no time)."""
    seconds, meters = distance.seconds, distance.meters
    first = np.flatnonzero(np.concatenate(([True], np.diff(segment) > 0)))
    last = np.concatenate((first[1:] - 1, [seconds.size - 1]))
    low = np.maximum(seconds - SPEED_WINDOW_S / 2, seconds[first][segment])
    high = np.minimum(seconds + SPEED_WINDOW_S / 2, seconds[last][segment])
    span = high - low
    gained = np.interp(high, seconds, meters) - np.interp(low, seconds, meters)
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(span > 0, gained / span, np.nan)


def _kept_indices(
    speed: NDArray[np.float64], segment: NDArray[np.intp], limit: int
) -> NDArray[np.intp]:
    """Indices to draw, at most about `limit`: evenly spaced ones plus the edges of every
    stretch and of every hole (so thinning never closes or opens a gap)."""
    count = int(speed.size)
    if count <= limit:
        return np.arange(count, dtype=np.intp)
    stride = math.ceil(count / (limit // 2))
    hole = np.isnan(speed)
    edges = np.flatnonzero(
        np.concatenate(([True], (hole[1:] != hole[:-1]) | (segment[1:] != segment[:-1])))
    )
    edges = np.unique(np.concatenate((edges, edges - 1, [count - 1])))
    edges = edges[edges >= 0]
    if edges.size > limit // 2:
        edges = edges[:: math.ceil(edges.size / (limit // 2))]
    return np.unique(np.concatenate((np.arange(0, count, stride), edges))).astype(np.intp)


def _with_pause_breaks(
    seconds: NDArray[np.float64], speed: NDArray[np.float64], segment: NDArray[np.intp]
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """A hole point in the middle of every pause, so a line is never drawn across it."""
    after = np.flatnonzero(segment[1:] != segment[:-1])
    middle = (seconds[after] + seconds[after + 1]) / 2
    return (
        np.insert(seconds, after + 1, middle),
        np.insert(speed, after + 1, np.nan),
    )


def smoothed_speed(
    distance: DistanceSeries, *, limit: int = MAX_SPEED_POINTS
) -> SpeedSeries | None:
    """Speed over time from the cumulative distance, or None if there is nothing to draw.

    The speed at each record is the distance covered in a window of `SPEED_WINDOW_S` centred on
    it, cut at the edges of its stretch, so a pause is never averaged in. A stretch slower than
    `MIN_MOVING_SPEED_M_S` is a hole. Up to `limit` points are returned (about: the edges of
    holes are always kept). None if the series has fewer than two records or never moves.
    """
    if limit < 4:
        raise ValueError("the limit must be at least 4")
    if len(distance) < 2:
        return None
    segment = _segment_of(distance.seconds)
    speed = _windowed_speed(distance, segment)
    speed[speed < MIN_MOVING_SPEED_M_S] = np.nan
    if np.isnan(speed).all():
        return None
    kept = _kept_indices(speed, segment, limit)
    seconds, values = _with_pause_breaks(distance.seconds[kept], speed[kept], segment[kept])
    return SpeedSeries(seconds, values)
