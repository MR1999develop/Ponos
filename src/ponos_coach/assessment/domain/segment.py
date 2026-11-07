# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Numerics of the threshold test: where the 30-minute segment is and what it contains.

Everything is vectorized and exact on the piecewise-linear cumulative functions of the series:
the best window of a piecewise-linear gain starts at a breakpoint, so only breakpoints are
tried (no sliding over every second). Ties go to the earliest window; values are rounded far
below any meaningful difference before comparing, so rounding noise never decides.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from ponos_coach.activities.domain import DistanceSeries, HeartRateSeries
from ponos_coach.assessment.domain.defaults import PAUSE_GAP_S, TEST_DURATION_S
from ponos_coach.load.domain import HeartRateLimits, sample_validity_s

_EPSILON_S = 1e-9  # tolerance on window bounds (times come from float division)
_METER_DECIMALS = 6  # distances compared to a micrometre
_BPM_DECIMALS = 9


def _candidate_starts(
    breakpoints: NDArray[np.float64], first: float, last: float, length_s: float
) -> NDArray[np.float64]:
    """Sorted starts of windows of `length_s` seconds inside `[first, last]`, at a breakpoint
    (the window starts or ends on it)."""
    ends = np.array([first, last - length_s])  # the bounds of the domain are candidates too
    starts = np.unique(np.concatenate((breakpoints, breakpoints - length_s, ends)))
    return starts[(starts >= first - _EPSILON_S) & (starts + length_s <= last + _EPSILON_S)]


def best_start_by_distance(distance: DistanceSeries) -> float | None:
    """Start (seconds) of the 30-minute window with the greatest distance covered.

    The window lies inside the distance series. `None` if the series is shorter than the window
    or covers no distance in any window. A tie goes to the earliest window.
    """
    seconds, meters = distance.seconds, distance.meters
    if seconds[-1] - seconds[0] < TEST_DURATION_S - _EPSILON_S:
        return None
    starts = _candidate_starts(seconds, float(seconds[0]), float(seconds[-1]), TEST_DURATION_S)
    if starts.size == 0:
        return None
    gain = np.interp(starts + TEST_DURATION_S, seconds, meters) - np.interp(starts, seconds, meters)
    gain = np.round(gain, _METER_DECIMALS)
    best = int(np.argmax(gain))
    return float(starts[best]) if gain[best] > 0 else None


def distance_between(distance: DistanceSeries, start_s: float, end_s: float) -> float | None:
    """Metres covered between two instants, or `None` if the series does not cover them both."""
    if start_s < distance.seconds[0] - _EPSILON_S or end_s > distance.seconds[-1] + _EPSILON_S:
        return None
    at_end = np.interp(end_s, distance.seconds, distance.meters)
    at_start = np.interp(start_s, distance.seconds, distance.meters)
    return float(at_end - at_start)


@dataclass(frozen=True, slots=True, eq=False)
class HeartRateTimeline:
    """The HR samples that count: each is valid until the next one (the last, the median of the
    intervals), and counts only if its bpm lies in `[min, max]` and the interval is not a pause
    (over 10 s). The same rule as the load.

    The cumulative counted seconds and bpm-seconds up to any instant are exact (piecewise linear).
    """

    starts: NDArray[np.float64]
    ends: NDArray[np.float64]
    bpm: NDArray[np.float64]
    counted: NDArray[np.bool_]
    full_seconds: NDArray[np.float64]  # counted seconds of all the samples before i
    full_weighted: NDArray[np.float64]  # the same, times bpm

    @classmethod
    def of(cls, series: HeartRateSeries, limits: HeartRateLimits) -> HeartRateTimeline:
        """Timeline of a series with the HR limits valid that day."""
        validity = sample_validity_s(series.seconds)
        in_range = (series.bpm >= limits.min_bpm) & (series.bpm <= limits.max_bpm)
        counted = in_range & (validity <= PAUSE_GAP_S)
        seconds = np.where(counted, validity, 0.0)
        return cls(
            series.seconds,
            series.seconds + validity,
            series.bpm,
            counted,
            np.concatenate(([0.0], np.cumsum(seconds)[:-1])),
            np.concatenate(([0.0], np.cumsum(seconds * series.bpm)[:-1])),
        )

    def _before(
        self, instants: NDArray[np.float64]
    ) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
        """Cumulative counted seconds and bpm-seconds up to each instant."""
        index = np.searchsorted(self.starts, instants, side="right") - 1
        inside = index >= 0
        at = np.where(inside, index, 0)
        partial = np.clip(instants - self.starts[at], 0.0, self.ends[at] - self.starts[at])
        partial = np.where(inside & self.counted[at], partial, 0.0)
        seconds = np.where(inside, self.full_seconds[at] + partial, 0.0)
        weighted = np.where(inside, self.full_weighted[at] + partial * self.bpm[at], 0.0)
        return seconds, weighted

    def window(
        self, starts: NDArray[np.float64], length_s: float
    ) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
        """Counted seconds and bpm-seconds inside each window `[start, start + length_s]`."""
        seconds_end, weighted_end = self._before(starts + length_s)
        seconds_start, weighted_start = self._before(starts)
        return seconds_end - seconds_start, weighted_end - weighted_start

    def valid_seconds(self, start_s: float, length_s: float) -> float:
        """Counted seconds inside one window."""
        seconds, _ = self.window(np.array([start_s]), length_s)
        return float(seconds[0])

    def mean_bpm(self, start_s: float, length_s: float) -> float | None:
        """Mean bpm of the counted seconds of one window, weighted on time; `None` if none."""
        seconds, weighted = self.window(np.array([start_s]), length_s)
        return float(weighted[0] / seconds[0]) if seconds[0] > 0 else None

    def counted_samples(self, start_s: float, length_s: float) -> int:
        """How many counted samples overlap the window."""
        overlapping = (self.starts < start_s + length_s) & (self.ends > start_s)
        return int(np.count_nonzero(overlapping & self.counted))

    def best_start_by_mean_bpm(self) -> float | None:
        """Start of the 30-minute window with the highest mean of the counted bpm, on time.

        The window lies between the first record of the activity (second 0, even if the HR starts
        later) and the end of the last HR sample. `None` if that is shorter than the window or no
        window has a counted second. A tie goes to the earliest.
        """
        first, last = min(0.0, float(self.starts[0])), float(self.ends[-1])
        if last - first < TEST_DURATION_S - _EPSILON_S:
            return None
        breakpoints = np.concatenate((self.starts, self.ends))
        starts = _candidate_starts(breakpoints, first, last, TEST_DURATION_S)
        if starts.size == 0:
            return None
        seconds, weighted = self.window(starts, TEST_DURATION_S)
        usable = seconds > 0
        if not usable.any():
            return None
        mean = np.full(starts.shape, -np.inf)
        mean[usable] = np.round(weighted[usable] / seconds[usable], _BPM_DECIMALS)
        return float(starts[int(np.argmax(mean))])


def pause_seconds(record_seconds: NDArray[np.float64], start_s: float, length_s: float) -> float:
    """Seconds of pause inside a window: gaps over 10 s between consecutive records, clipped.

    `record_seconds` is every record time of the activity (increasing).
    """
    gap_start, gap_end = record_seconds[:-1], record_seconds[1:]
    paused = (gap_end - gap_start) > PAUSE_GAP_S
    clipped = np.minimum(gap_end[paused], start_s + length_s) - np.maximum(
        gap_start[paused], start_s
    )
    return float(np.clip(clipped, 0.0, None).sum())
