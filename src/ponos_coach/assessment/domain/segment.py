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
from ponos_coach.assessment.domain.defaults import (
    DISTANCE_END_TOLERANCE_S,
    MAX_PACE_S_PER_KM,
    MAX_PLAUSIBLE_SPEED_M_S,
    METERS_PER_KM,
    MIN_PACE_S_PER_KM,
    MIN_VALID_HR_PERCENT,
    PAUSE_GAP_S,
    TEST_DURATION_S,
    THRESHOLD_WINDOW_S,
)
from ponos_coach.load.domain import HeartRateLimits, sample_validity_s

_EPSILON_S = 1e-9  # tolerance on window bounds (times come from float division)
_METER_DECIMALS = 6  # distances compared to a micrometre
_BPM_DECIMALS = 9
_MS_PER_S = 1000
_PERCENT = 100


def _candidate_starts(
    breakpoints: NDArray[np.float64], first: float, last: float, length_s: float
) -> NDArray[np.float64]:
    """Sorted starts of windows of `length_s` seconds inside `[first, last]`, at a breakpoint
    (the window starts or ends on it)."""
    ends = np.array([first, last - length_s])  # the bounds of the domain are candidates too
    starts = np.unique(np.concatenate((breakpoints, breakpoints - length_s, ends)))
    return starts[(starts >= first - _EPSILON_S) & (starts + length_s <= last + _EPSILON_S)]


def _end_tolerance_s(seconds: NDArray[np.float64]) -> float:
    """How far past its last record the distance series still counts: one interval between
    samples (the last sample is valid until the next, as for the HR), at most
    `DISTANCE_END_TOLERANCE_S`."""
    if seconds.size < 2:
        return 0.0
    return min(float(seconds[-1] - seconds[-2]), DISTANCE_END_TOLERANCE_S)


def _implausible_steps(distance: DistanceSeries) -> NDArray[np.bool_]:
    """For each interval between two records, whether it is faster than a runner can be."""
    meters_per_step = np.diff(distance.meters)
    return meters_per_step > MAX_PLAUSIBLE_SPEED_M_S * np.diff(distance.seconds)


def _plausible_meters(distance: DistanceSeries) -> NDArray[np.float64]:
    """Cumulative distance without the implausible steps (they add nothing)."""
    steps = np.where(_implausible_steps(distance), 0.0, np.diff(distance.meters))
    return np.concatenate(([0.0], np.cumsum(steps)))


def best_start_by_distance(distance: DistanceSeries) -> float | None:
    """Start (seconds) of the 30-minute window with the greatest distance covered.

    The window lies inside the distance series, which counts one interval past its last record
    (see `_end_tolerance_s`). Steps faster than `MAX_PLAUSIBLE_SPEED_M_S` count for nothing. `None`
    if the series is shorter than the window or covers no distance in any window, and also if the
    best window cannot be trusted: it contains such a step or its pace over the 30 minutes is
    not a running pace (the caller then chooses by HR). A tie goes to the earliest window.
    """
    seconds = distance.seconds
    first, last = float(seconds[0]), float(seconds[-1]) + _end_tolerance_s(seconds)
    if last - first < TEST_DURATION_S - _EPSILON_S:
        return None
    starts = _candidate_starts(seconds, first, last, TEST_DURATION_S)
    if starts.size == 0:
        return None
    meters = _plausible_meters(distance)
    gain = np.interp(starts + TEST_DURATION_S, seconds, meters) - np.interp(starts, seconds, meters)
    gain = np.round(gain, _METER_DECIMALS)
    best = int(np.argmax(gain))
    if gain[best] <= 0:
        return None
    start = float(starts[best])
    return start if _is_running_window(distance, start) else None


def _is_running_window(distance: DistanceSeries, start_s: float) -> bool:
    """True if the 30 minutes from `start_s` have no implausible step and a plausible pace."""
    meters = distance_between(distance, start_s, start_s + TEST_DURATION_S)
    if meters is None or meters <= 0:
        return False
    pace_s_per_km = TEST_DURATION_S / (meters / METERS_PER_KM)
    return MIN_PACE_S_PER_KM <= pace_s_per_km <= MAX_PACE_S_PER_KM


def distance_between(distance: DistanceSeries, start_s: float, end_s: float) -> float | None:
    """Metres covered between two instants, or `None` if the series does not cover them both or
    a step faster than `MAX_PLAUSIBLE_SPEED_M_S` lies between them (a glitch: no pace is given).

    The series covers up to one interval past its last record (see `_end_tolerance_s`).
    """
    seconds = distance.seconds
    if (
        start_s < seconds[0] - _EPSILON_S
        or end_s > seconds[-1] + _end_tolerance_s(seconds) + _EPSILON_S
    ):
        return None
    overlapping = (seconds[:-1] < end_s) & (seconds[1:] > start_s)
    if (overlapping & _implausible_steps(distance)).any():
        return None
    at_end = np.interp(end_s, seconds, distance.meters)
    at_start = np.interp(start_s, seconds, distance.meters)
    return float(at_end - at_start)


def enough_valid_seconds(valid_s: NDArray[np.float64]) -> NDArray[np.bool_]:
    """Whether the valid HR seconds of the last 20 minutes of a segment reach
    `MIN_VALID_HR_PERCENT` of them (compared in whole milliseconds: exactly 90% is enough)."""
    milliseconds = np.round(valid_s * _MS_PER_S).astype(np.int64)
    return milliseconds * _PERCENT >= MIN_VALID_HR_PERCENT * THRESHOLD_WINDOW_S * _MS_PER_S


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

    def _coverage(self, starts: NDArray[np.float64]) -> NDArray[np.float64]:
        """Counted seconds in the last 20 minutes of each 30-minute window."""
        seconds, _ = self.window(
            starts + (TEST_DURATION_S - THRESHOLD_WINDOW_S), THRESHOLD_WINDOW_S
        )
        return seconds

    def _with_coverage_limit(self, starts: NDArray[np.float64]) -> NDArray[np.float64]:
        """The sorted candidate starts plus the points where the counted seconds of the last 20
        minutes cross the 90% limit: between two candidates the coverage is linear, so the
        best usable window may sit exactly where the limit is reached."""
        limit = MIN_VALID_HR_PERCENT * THRESHOLD_WINDOW_S / _PERCENT
        coverage = self._coverage(starts)
        before, after = coverage[:-1] - limit, coverage[1:] - limit
        crossing = np.flatnonzero(before * after < 0)
        fraction = before[crossing] / (before[crossing] - after[crossing])
        roots = starts[crossing] + fraction * (starts[crossing + 1] - starts[crossing])
        return np.unique(np.concatenate((starts, roots)))

    def best_start_by_mean_bpm(self) -> float | None:
        """Start of the 30-minute window with the highest mean of the counted bpm, on time,
        among the windows that the test would accept as to coverage (valid seconds at least 90%
        of the last 20 minutes: a window with few valid seconds and a high HR does not win).

        The window lies between the first record of the activity (second 0, even if the HR starts
        later) and the end of the last HR sample. `None` if that is shorter than the window or no
        window has a counted second. If no window has enough valid seconds, the one with the highest
        mean is returned all the same and the test is rejected for its coverage. A tie goes to the
        earliest.
        """
        first, last = min(0.0, float(self.starts[0])), float(self.ends[-1])
        if last - first < TEST_DURATION_S - _EPSILON_S:
            return None
        breakpoints = np.concatenate((self.starts, self.ends))
        # the last 20 minutes of a window start 10 minutes in: their breakpoints count too
        breakpoints = np.concatenate(
            (breakpoints, breakpoints - (TEST_DURATION_S - THRESHOLD_WINDOW_S))
        )
        starts = _candidate_starts(breakpoints, first, last, TEST_DURATION_S)
        if starts.size == 0:
            return None
        starts = self._with_coverage_limit(starts)
        seconds, weighted = self.window(starts, TEST_DURATION_S)
        usable = seconds > 0
        if not usable.any():
            return None
        covered = usable & enough_valid_seconds(self._coverage(starts))
        usable = covered if covered.any() else usable  # none covered: rejected later, as before
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
