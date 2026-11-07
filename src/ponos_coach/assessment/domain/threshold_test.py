# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""The 30-minute threshold test: from a running activity to a threshold HR and pace.

The user says which activity is the test (it is never recognized by itself). The segment is the
30-minute window of elapsed time with the greatest distance covered; without a usable distance,
the one with the highest mean valid HR. The threshold HR is the time-weighted mean of the valid
HR samples of the last 20 minutes of the segment, rounded to a whole bpm; the threshold pace is
the time over the distance of the same 20 minutes, in whole seconds per kilometre (none without
distance). A test that cannot be trusted is rejected with a reason, never repaired. Pure and
deterministic: the same activity and limits always give the same outcome. Health data: never in
logs or error messages.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import Enum

import numpy as np
from numpy.typing import NDArray

from ponos_coach.activities.domain import (
    Activity,
    ActivityIdentity,
    Sport,
    truncate_to_millisecond,
)
from ponos_coach.assessment.domain.defaults import (
    MAX_PACE_S_PER_KM,
    MAX_PAUSE_S,
    METERS_PER_KM,
    METHOD_VERSION,
    MIN_PACE_S_PER_KM,
    MIN_VALID_HR_PERCENT,
    TEST_DURATION_S,
    THRESHOLD_WINDOW_S,
)
from ponos_coach.assessment.domain.pace_zones import pace_zone_starts_s_per_km
from ponos_coach.assessment.domain.segment import (
    HeartRateTimeline,
    best_start_by_distance,
    distance_between,
    pause_seconds,
)
from ponos_coach.load.domain import HeartRateLimits, ThresholdHeartRate, zone_starts_bpm

_MS_PER_S = 1000
_PERCENT = 100
_NOISE_DECIMALS = 9
_TEST_SPORTS = frozenset({Sport.RUNNING, Sport.TRAIL})


class ThresholdRejection(Enum):
    """Why an activity cannot be used as the threshold test. The value is stable."""

    WRONG_SPORT = "wrong_sport"  # not a run
    TOO_SHORT = "too_short"  # under 30 minutes of elapsed time, or no 30-minute stretch of data
    TOO_MANY_PAUSES = "too_many_pauses"  # over 60 s of pause inside the segment
    TOO_FEW_HEART_RATE_SAMPLES = "too_few_heart_rate_samples"  # valid HR under 90% of last 20'
    THRESHOLD_OUT_OF_RANGE = "threshold_out_of_range"  # not between min HR and max HR


class SegmentSelection(Enum):
    """How the segment was chosen."""

    BY_DISTANCE = "by_distance"  # the 30 minutes with the greatest distance covered
    BY_HEART_RATE = "by_heart_rate"  # no usable distance: the highest mean valid HR


@dataclass(frozen=True, slots=True)
class ThresholdTestResult:
    """The outcome of an accepted test.

    `test_day` is the local day of the activity (given by the caller, who knows the time zone).
    `segment_start_s` is the start of the segment in seconds from the first record and
    `segment_start` the same instant in UTC (to the millisecond). `threshold_bpm` is whole bpm,
    strictly between min HR and max HR of the day; `pace_s_per_km` whole seconds, or `None`
    without distance. `valid_hr_samples` counts the HR samples used. The zone starts are the HR
    at which Z2..Z5 start, and the pace at which they start (from the slowest to the fastest;
    Z1 is slower than the first).
    """

    identity: ActivityIdentity
    test_day: date
    selection: SegmentSelection
    segment_start_s: float
    segment_start: datetime
    threshold_bpm: int
    pace_s_per_km: int | None
    valid_hr_samples: int
    hr_zone_starts_bpm: tuple[float, float, float, float]
    pace_zone_starts_s_per_km: tuple[float, float, float, float] | None
    method_version: int = METHOD_VERSION

    def __post_init__(self) -> None:
        if (self.pace_s_per_km is None) != (self.pace_zone_starts_s_per_km is None):
            raise ValueError("pace and pace zones go together")
        if self.pace_s_per_km is not None and not (
            MIN_PACE_S_PER_KM <= self.pace_s_per_km <= MAX_PACE_S_PER_KM
        ):
            raise ValueError("pace outside the plausible range")
        if self.valid_hr_samples < 1:
            raise ValueError("a test rests on at least one HR sample")


def _round_half_up(value: float) -> int:
    """Nearest whole number, halves up. The value is first cleaned of the rounding noise of the
    cumulative sums (a mean of 164.5 may come out as 164.49999999999997)."""
    return math.floor(round(value, _NOISE_DECIMALS) + 0.5)


def _record_seconds(activity: Activity) -> NDArray[np.float64]:
    """Every record time of the activity, increasing (HR and distance together)."""
    columns = [s.seconds for s in (activity.heart_rate, activity.distance) if s is not None]
    return np.unique(np.concatenate(columns))


def _pace_s_per_km(activity: Activity, start_s: float) -> int | None:
    """Pace of the last 20 minutes of the segment, or `None` without a trustworthy distance."""
    if activity.distance is None:
        return None
    window_start = start_s + TEST_DURATION_S - THRESHOLD_WINDOW_S
    meters = distance_between(activity.distance, window_start, start_s + TEST_DURATION_S)
    if meters is None or meters <= 0:
        return None
    pace = _round_half_up(THRESHOLD_WINDOW_S / (meters / METERS_PER_KM))
    return pace if MIN_PACE_S_PER_KM <= pace <= MAX_PACE_S_PER_KM else None


def _is_threshold_for(limits: HeartRateLimits, bpm: int) -> bool:
    if not limits.min_bpm < bpm < limits.max_bpm:
        return False
    try:
        ThresholdHeartRate(bpm)
    except ValueError:
        return False
    return True


def _too_few_valid_seconds(valid_s: float) -> bool:
    """True if the valid seconds are under 90% of the last 20 minutes (whole milliseconds)."""
    return round(valid_s * _MS_PER_S) * _PERCENT < (
        MIN_VALID_HR_PERCENT * THRESHOLD_WINDOW_S * _MS_PER_S
    )


def _unfit(activity: Activity) -> ThresholdRejection | None:
    """Rejection that needs nothing but the header of the activity: sport and elapsed time."""
    if activity.sport not in _TEST_SPORTS:
        return ThresholdRejection.WRONG_SPORT
    if activity.elapsed_s < TEST_DURATION_S:
        return ThresholdRejection.TOO_SHORT
    return None


def _choose_segment(
    activity: Activity, timeline: HeartRateTimeline
) -> tuple[SegmentSelection, float] | None:
    """How and where the segment is: by distance if the distance allows it, otherwise by HR."""
    if activity.distance is not None:
        by_distance = best_start_by_distance(activity.distance)
        if by_distance is not None:
            return SegmentSelection.BY_DISTANCE, by_distance
    by_heart_rate = timeline.best_start_by_mean_bpm()
    return None if by_heart_rate is None else (SegmentSelection.BY_HEART_RATE, by_heart_rate)


def _threshold_bpm(
    activity: Activity, timeline: HeartRateTimeline, limits: HeartRateLimits, start_s: float
) -> int | ThresholdRejection:
    """Threshold HR of the segment starting at `start_s`, after the pause, coverage and range
    checks."""
    paused_ms = round(
        pause_seconds(_record_seconds(activity), start_s, TEST_DURATION_S) * _MS_PER_S
    )
    if paused_ms > MAX_PAUSE_S * _MS_PER_S:
        return ThresholdRejection.TOO_MANY_PAUSES
    last_start_s = start_s + TEST_DURATION_S - THRESHOLD_WINDOW_S
    mean = timeline.mean_bpm(last_start_s, THRESHOLD_WINDOW_S)
    if mean is None or _too_few_valid_seconds(
        timeline.valid_seconds(last_start_s, THRESHOLD_WINDOW_S)
    ):
        return ThresholdRejection.TOO_FEW_HEART_RATE_SAMPLES
    bpm = _round_half_up(mean)
    return bpm if _is_threshold_for(limits, bpm) else ThresholdRejection.THRESHOLD_OUT_OF_RANGE


def evaluate_threshold_test(
    activity: Activity, limits: HeartRateLimits, test_day: date
) -> ThresholdTestResult | ThresholdRejection:
    """Threshold HR and pace of a run chosen by the user as the test, or why it is rejected.

    `limits` are the profile's HR limits valid on `test_day`. Checks, in order: sport (run or
    trail), elapsed time of at least 30 minutes, an HR series, a 30-minute stretch of data,
    pauses in the segment (gaps over 10 s between records, 60 s in total at most), valid HR at
    least 90% of the time of the last 20 minutes, threshold strictly between min HR and max HR.
    """
    rejection = _unfit(activity)
    if rejection is not None:
        return rejection
    if activity.heart_rate is None:
        return ThresholdRejection.TOO_FEW_HEART_RATE_SAMPLES
    timeline = HeartRateTimeline.of(activity.heart_rate, limits)
    segment = _choose_segment(activity, timeline)
    if segment is None:
        return ThresholdRejection.TOO_SHORT
    selection, start_s = segment
    bpm = _threshold_bpm(activity, timeline, limits, start_s)
    if isinstance(bpm, ThresholdRejection):
        return bpm
    pace = _pace_s_per_km(activity, start_s)
    return ThresholdTestResult(
        identity=activity.identity,
        test_day=test_day,
        selection=selection,
        segment_start_s=start_s,
        segment_start=truncate_to_millisecond(
            activity.start + timedelta(milliseconds=round(start_s * _MS_PER_S))
        ),
        threshold_bpm=bpm,
        pace_s_per_km=pace,
        valid_hr_samples=timeline.counted_samples(
            start_s + TEST_DURATION_S - THRESHOLD_WINDOW_S, THRESHOLD_WINDOW_S
        ),
        hr_zone_starts_bpm=zone_starts_bpm(bpm),
        pace_zone_starts_s_per_km=None if pace is None else pace_zone_starts_s_per_km(pace),
    )
