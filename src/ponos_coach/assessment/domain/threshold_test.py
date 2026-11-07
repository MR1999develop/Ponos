# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""The threshold tests (30 or 20 minutes): from a running activity to a threshold HR and pace.

The user says which activity is the test and with which protocol (it is never recognized by
itself). The segment is the window of elapsed time (30 or 20 minutes) with the greatest distance
covered; without a usable distance, the one with the highest mean valid HR. Protocol of 30
minutes (FRI): the threshold HR is the time-weighted mean of the valid HR samples of the last 20
minutes of the segment, rounded to a whole bpm; the threshold pace is the time over the distance
of the same 20 minutes, in whole seconds per kilometre (none without distance). Protocol of 20
minutes (FRI5): the HR is the mean of the whole segment times 0.95, rounded; the pace is the mean
pace plus 5% of the time per kilometre (the book applies the 5% to the time, not to the speed). A
test that cannot be trusted is rejected with a reason, never repaired. Pure and deterministic:
the same activity and limits always give the same outcome. Health data: never in logs or error
messages.
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
    truncate_to_millisecond,
)
from ponos_coach.assessment.domain.defaults import (
    MAX_PACE_S_PER_KM,
    MAX_PAUSE_S,
    METERS_PER_KM,
    METHOD_VERSION,
    MIN_PACE_S_PER_KM,
    THRESHOLD_TEST_SPORTS,
    TWENTY_MINUTE_HR_PERCENT,
    TWENTY_MINUTE_PACE_ADD_PERCENT,
)
from ponos_coach.assessment.domain.pace_zones import pace_zone_starts_s_per_km
from ponos_coach.assessment.domain.protocol import DEFAULT_PROTOCOL, ThresholdProtocol
from ponos_coach.assessment.domain.segment import (
    HeartRateTimeline,
    best_start_by_distance,
    distance_between,
    enough_valid_seconds,
    pause_seconds,
)
from ponos_coach.load.domain import HeartRateLimits, ThresholdHeartRate, zone_starts_bpm

_MS_PER_S = 1000
_NOISE_DECIMALS = 9
_PERCENT = 100


class ThresholdRejection(Enum):
    """Why an activity cannot be used as the threshold test. The value is stable."""

    WRONG_SPORT = "wrong_sport"  # not a run
    TOO_SHORT = "too_short"  # under the protocol duration, or no stretch of data that long
    TOO_MANY_PAUSES = "too_many_pauses"  # over 60 s of pause inside the segment
    TOO_FEW_HEART_RATE_SAMPLES = "too_few_heart_rate_samples"  # valid HR under 90% of last 20'
    THRESHOLD_OUT_OF_RANGE = "threshold_out_of_range"  # not between min HR and max HR


class SegmentSelection(Enum):
    """How the segment was chosen."""

    BY_DISTANCE = "by_distance"  # the window with the greatest distance covered
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
    Z1 is slower than the first). `protocol` is the one the test was run with.
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
    protocol: ThresholdProtocol = DEFAULT_PROTOCOL

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


def _pace_s_per_km(activity: Activity, start_s: float, protocol: ThresholdProtocol) -> int | None:
    """Threshold pace of the segment (the last 20 minutes of it; with the 20-minute protocol,
    plus 5% of the time per kilometre), or `None` without a trustworthy distance."""
    if activity.distance is None:
        return None
    end_s = start_s + protocol.duration_s
    meters = distance_between(activity.distance, end_s - protocol.window_s, end_s)
    if meters is None or meters <= 0:
        return None
    mean_pace = protocol.window_s / (meters / METERS_PER_KM)
    if protocol is ThresholdProtocol.TWENTY_MINUTES:
        mean_pace = mean_pace * (_PERCENT + TWENTY_MINUTE_PACE_ADD_PERCENT) / _PERCENT
    pace = _round_half_up(mean_pace)
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
    return not bool(enough_valid_seconds(np.array([valid_s]))[0])


def _unfit(activity: Activity, protocol: ThresholdProtocol) -> ThresholdRejection | None:
    """Rejection that needs nothing but the header of the activity: sport and elapsed time."""
    if activity.sport not in THRESHOLD_TEST_SPORTS:
        return ThresholdRejection.WRONG_SPORT
    if activity.elapsed_s < protocol.duration_s:
        return ThresholdRejection.TOO_SHORT
    return None


def _choose_segment(
    activity: Activity, timeline: HeartRateTimeline, protocol: ThresholdProtocol
) -> tuple[SegmentSelection, float] | None:
    """How and where the segment is: by distance if the distance allows it and can be trusted
    (no implausible step, a running pace), otherwise by HR."""
    if activity.distance is not None:
        by_distance = best_start_by_distance(activity.distance, protocol.duration_s)
        if by_distance is not None:
            return SegmentSelection.BY_DISTANCE, by_distance
    by_heart_rate = timeline.best_start_by_mean_bpm(protocol.duration_s)
    return None if by_heart_rate is None else (SegmentSelection.BY_HEART_RATE, by_heart_rate)


def _threshold_bpm(
    activity: Activity,
    timeline: HeartRateTimeline,
    limits: HeartRateLimits,
    start_s: float,
    protocol: ThresholdProtocol,
) -> int | ThresholdRejection:
    """Threshold HR of the segment starting at `start_s`, after the pause, coverage and range
    checks (with the 20-minute protocol, the mean times 95%)."""
    paused_ms = round(
        pause_seconds(_record_seconds(activity), start_s, protocol.duration_s) * _MS_PER_S
    )
    if paused_ms > MAX_PAUSE_S * _MS_PER_S:
        return ThresholdRejection.TOO_MANY_PAUSES
    last_start_s = start_s + protocol.duration_s - protocol.window_s
    mean = timeline.mean_bpm(last_start_s, protocol.window_s)
    if mean is None or _too_few_valid_seconds(
        timeline.valid_seconds(last_start_s, protocol.window_s)
    ):
        return ThresholdRejection.TOO_FEW_HEART_RATE_SAMPLES
    if protocol is ThresholdProtocol.TWENTY_MINUTES:
        mean = mean * TWENTY_MINUTE_HR_PERCENT / _PERCENT
    bpm = _round_half_up(mean)
    return bpm if _is_threshold_for(limits, bpm) else ThresholdRejection.THRESHOLD_OUT_OF_RANGE


def evaluate_threshold_test(
    activity: Activity,
    limits: HeartRateLimits,
    test_day: date,
    protocol: ThresholdProtocol = DEFAULT_PROTOCOL,
) -> ThresholdTestResult | ThresholdRejection:
    """Threshold HR and pace of a run chosen by the user as the test, or why it is rejected.

    `limits` are the profile's HR limits valid on `test_day`; `protocol` says whether the test
    was run for 30 or for 20 minutes. Checks, in order: sport (run or trail), elapsed time of at
    least the protocol duration, an HR series, a stretch of data that long, pauses in the segment
    (gaps over 10 s between records, 60 s in total at most), valid HR at least 90% of the time of
    the 20 minutes that give the threshold (the last 20 of the 30, or the whole 20), threshold
    strictly between min HR and max HR.
    """
    rejection = _unfit(activity, protocol)
    if rejection is not None:
        return rejection
    if activity.heart_rate is None:
        return ThresholdRejection.TOO_FEW_HEART_RATE_SAMPLES
    timeline = HeartRateTimeline.of(activity.heart_rate, limits)
    segment = _choose_segment(activity, timeline, protocol)
    if segment is None:
        return ThresholdRejection.TOO_SHORT
    selection, start_s = segment
    bpm = _threshold_bpm(activity, timeline, limits, start_s, protocol)
    if isinstance(bpm, ThresholdRejection):
        return bpm
    # a segment chosen without trusting the distance gives no pace
    pace = (
        _pace_s_per_km(activity, start_s, protocol)
        if selection is SegmentSelection.BY_DISTANCE
        else None
    )
    return ThresholdTestResult(
        identity=activity.identity,
        test_day=test_day,
        selection=selection,
        segment_start_s=start_s,
        segment_start=truncate_to_millisecond(
            activity.start
            + timedelta(milliseconds=round((activity.first_record_offset_s + start_s) * _MS_PER_S))
        ),
        threshold_bpm=bpm,
        pace_s_per_km=pace,
        valid_hr_samples=timeline.counted_samples(
            start_s + protocol.duration_s - protocol.window_s, protocol.window_s
        ),
        hr_zone_starts_bpm=zone_starts_bpm(bpm),
        pace_zone_starts_s_per_km=None if pace is None else pace_zone_starts_s_per_km(pace),
        protocol=protocol,
    )
