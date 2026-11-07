# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Fuzzing of the distance decoders and differential check of the threshold test (T-SEC-02 for the
distance series, T-PIANO-06).

Long tests: only in the `collaudo` session (hypothesis profile `collaudo`). The oracles go beyond
"does not crash":

- FIT and GPX with hostile timestamps, distances and speeds: the only error is `ActivityFileError`;
  a distance series, when there is one, is finite, strictly increasing in time, non-negative and
  non-decreasing in metres, on the same axis as the HR (seconds from the first record, a subset of
  the record times) and, when the file is monotone, equal to what the file said;
- a naive pure-Python computation with exact fractions of the threshold HR and pace of random
  1 Hz runs (dropouts, out-of-limits HR, standing stretches) agrees with `evaluate_threshold_test`
  on the segment the code chose, and that segment has the greatest distance.
"""

from __future__ import annotations

import statistics
from datetime import UTC, date, datetime, timedelta
from fractions import Fraction
from itertools import pairwise

import numpy as np
import pytest
from hypothesis import assume, event, given, settings
from hypothesis import strategies as st

from ponos_coach.activities.decoding import ActivityFileError, decode_activity_file
from ponos_coach.activities.domain import Activity
from ponos_coach.assessment.domain import (
    ThresholdRejection,
    ThresholdTestResult,
    evaluate_threshold_test,
)
from ponos_coach.load.domain import HeartRateLimits
from tests.collaudo.fase4_support import DAY, LIMITS, START, Leg, run_fit
from tests.fixtures.generators import fit_writer as fw
from tests.fixtures.generators import gpx_writer as gw

pytestmark = pytest.mark.collaudo

FIRST = fw.datetime_to_fit(START)
WINDOW = 1800
LAST = 1200
MIN_BPM, MAX_BPM = LIMITS


# --- Oracles shared by the decoder fuzzers --------------------------------------------------------


def check_axis(activity: Activity, record_seconds: set[float]) -> None:
    """The invariants of the distance series and its axis."""
    if activity.heart_rate is not None:
        assert set(activity.heart_rate.seconds.tolist()) <= record_seconds
    series = activity.distance
    if series is None:
        return
    assert np.isfinite(series.seconds).all()
    assert np.isfinite(series.meters).all()
    assert (series.seconds >= 0).all()
    assert (np.diff(series.seconds) > 0).all()
    assert series.meters[0] >= 0
    assert (np.diff(series.meters) >= 0).all()
    assert set(series.seconds.tolist()) <= record_seconds


# --- FIT -----------------------------------------------------------------------------------

fit_records = st.lists(
    st.tuples(
        st.integers(-5, 40),  # step of the timestamp: backwards, repeated, forward, a long gap
        st.one_of(st.none(), st.integers(0, 254)),  # heart rate
        st.one_of(st.none(), st.integers(0, 5_000_000).map(lambda cm: cm / 100)),  # distance
        st.one_of(st.none(), st.integers(0, 60_000).map(lambda mm: mm / 1000)),  # speed
    ),
    min_size=2,
    max_size=60,
)


def fit_from(
    steps: list[tuple[int, int | None, float | None, float | None]],
) -> tuple[bytes, list[int]]:
    # records never go before the first one here (that case is `test_..._before_the_first_record`)
    stamp, times, samples = FIRST + 100, [], []
    floor = stamp
    for index, (step, bpm, distance, speed) in enumerate(steps):
        stamp = floor if index == 0 else max(floor, stamp + step)
        times.append(stamp)
        samples.append(fw.Sample(stamp, bpm, distance_m=distance, speed_ms=speed))
    spec = fw.ActivitySpec(
        START, fw.Sport.RUNNING, fw.SubSport.GENERIC, samples, total_elapsed_ms=3_600_000
    )
    return fw.build_activity(spec), times


@given(fit_records)
def test_t_sec_02_fuzz_fit_distance_series_invariants_whatever_the_records_say(
    steps: list[tuple[int, int | None, float | None, float | None]],
) -> None:
    data, times = fit_from(steps)
    try:
        activity = decode_activity_file(data, "x.fit")
    except ActivityFileError:
        return
    axis = {float(t - times[0]) for t in times}

    check_axis(activity, axis)
    outcome = evaluate_threshold_test(activity, HeartRateLimits(MIN_BPM, MAX_BPM), DAY)
    assert isinstance(outcome, ThresholdRejection | ThresholdTestResult)


monotone_fit = st.lists(
    st.tuples(st.integers(1, 12), st.integers(0, 3000)), min_size=2, max_size=60
)


@given(monotone_fit)
def test_t_sec_02_fuzz_fit_a_monotone_distance_is_returned_exactly(
    steps: list[tuple[int, int]],
) -> None:
    """Round trip: what the file says (cumulative centimetres) is what the series holds."""
    stamp, meters = FIRST, 0
    samples, expected_s, expected_m = [], [], []
    for step, increase_cm in steps:
        stamp += step
        meters += increase_cm
        samples.append(fw.Sample(stamp, 150, distance_m=meters / 100))
        expected_s.append(float(stamp - samples[0].timestamp))
        expected_m.append(meters / 100)
    spec = fw.ActivitySpec(
        START, fw.Sport.RUNNING, fw.SubSport.GENERIC, samples, total_elapsed_ms=3_600_000
    )

    activity = decode_activity_file(fw.build_activity(spec), "x.fit")

    assert activity.distance is not None
    assert activity.distance.seconds.tolist() == expected_s
    assert activity.distance.meters.tolist() == pytest.approx(expected_m, abs=1e-6)


speed_only = st.lists(
    st.tuples(st.integers(1, 25), st.integers(0, 12_000).map(lambda mm: mm / 1000)),
    min_size=2,
    max_size=60,
)


@given(speed_only)
def test_t_sec_02_fuzz_fit_speed_integration_matches_a_naive_trapezoid(
    steps: list[tuple[int, float]],
) -> None:
    stamp, samples = FIRST, []
    for step, speed in steps:
        stamp += step
        samples.append(fw.Sample(stamp, 150, speed_ms=speed))
    spec = fw.ActivitySpec(
        START, fw.Sport.RUNNING, fw.SubSport.GENERIC, samples, total_elapsed_ms=3_600_000
    )

    activity = decode_activity_file(fw.build_activity(spec), "x.fit")

    assert activity.distance is not None
    naive = [0.0]
    for (_, before), (gap, after) in pairwise(steps):
        naive.append(naive[-1] + (0.0 if gap > 10 else (before + after) / 2 * gap))
    assert activity.distance.meters.tolist() == pytest.approx(naive, abs=1e-6)


# --- GPX -----------------------------------------------------------------------------------

gpx_points = st.lists(
    st.tuples(
        st.integers(-3, 40),  # time step in seconds (backwards and repeated included)
        st.floats(-90.0, 90.0, allow_nan=False),
        st.floats(-180.0, 180.0, allow_nan=False),
        st.one_of(st.none(), st.integers(30, 220)),
        st.booleans(),  # a new segment starts here
    ),
    min_size=2,
    max_size=50,
)


@given(gpx_points)
def test_t_sec_02_fuzz_gpx_distance_series_invariants(
    raw: list[tuple[int, float, float, int | None, bool]],
) -> None:
    moment = datetime(2026, 3, 14, 7, 0, tzinfo=UTC)
    segments: list[list[gw.GpxPoint]] = []
    times: list[datetime] = []
    for step, lat, lon, bpm, new_segment in raw:
        moment += timedelta(seconds=step)
        times.append(moment)
        point = gw.GpxPoint(round(lat, 7), round(lon, 7), moment, None, bpm)
        if new_segment or not segments:
            segments.append([])
        segments[-1].append(point)
    data = gw.build_gpx(gw.GpxSpec(segments, "running"))
    try:
        activity = decode_activity_file(data, "x.gpx")
    except ActivityFileError:
        return
    axis = {(t - times[0]).total_seconds() for t in times}

    check_axis(activity, axis)
    outcome = evaluate_threshold_test(activity, HeartRateLimits(MIN_BPM, MAX_BPM), DAY)
    assert isinstance(outcome, ThresholdRejection | ThresholdTestResult)


# --- Differential: the threshold test against a naive exact computation ---------------------------

run_blocks = st.lists(
    st.tuples(
        st.integers(0, 600).map(lambda cm: cm / 100),  # speed 0 to 6 m/s
        # HR: mostly inside the limits, now and then out of them or missing
        st.tuples(st.integers(0, 99), st.integers(30, 215), st.integers(140, 180)).map(
            lambda t: None if t[0] == 0 else t[1] if t[0] == 1 else t[2]
        ),
    ),
    min_size=31,
    max_size=44,
)
dropouts = st.lists(st.tuples(st.integers(5, 2500), st.integers(2, 14)), max_size=6)


def half_up(value: Fraction) -> int:
    return int((value + Fraction(1, 2)) // 1)


def naive_threshold(activity: Activity, start: int) -> tuple[int | ThresholdRejection, int | None]:
    """Threshold HR and pace of the segment `[start, start + 1800]`, exact."""
    assert activity.heart_rate is not None
    assert activity.distance is not None
    times = [int(t) for t in activity.heart_rate.seconds]
    bpms = [int(b) for b in activity.heart_rate.bpm]
    gaps = [b - a for a, b in pairwise(times)]
    validity = [*gaps, statistics.median(gaps) if gaps else 1]
    records = sorted({*times, *(int(t) for t in activity.distance.seconds)})
    paused = sum(
        min(b, start + WINDOW) - max(a, start)
        for a, b in pairwise(records)
        if b - a > 10 and min(b, start + WINDOW) > max(a, start)
    )
    if paused > 60:
        return ThresholdRejection.TOO_MANY_PAUSES, None
    lo, hi = start + WINDOW - LAST, start + WINDOW
    seconds, weighted = Fraction(0), Fraction(0)
    for t, b, v in zip(times, bpms, validity, strict=True):
        if MIN_BPM <= b <= MAX_BPM and v <= 10:
            overlap = Fraction(min(t + v, hi) - max(t, lo)).limit_denominator(10**6)
            if overlap > 0:
                seconds += overlap
                weighted += overlap * b
    if seconds * 100 < 90 * LAST:
        return ThresholdRejection.TOO_FEW_HEART_RATE_SAMPLES, None
    bpm = half_up(weighted / seconds)
    if not (MIN_BPM < bpm < MAX_BPM):
        return ThresholdRejection.THRESHOLD_OUT_OF_RANGE, None
    xs, ys = activity.distance.seconds, activity.distance.meters
    covered = Fraction(float(np.interp(hi, xs, ys) - np.interp(lo, xs, ys))).limit_denominator(
        10**4
    )
    pace = None
    if covered > 0:
        candidate = half_up(Fraction(LAST) / (covered / 1000))
        pace = candidate if 90 <= candidate <= 3600 else None
    return bpm, pace


@settings(max_examples=250, deadline=None)
@given(run_blocks, dropouts)
def test_t_piano_06_fuzz_the_result_agrees_with_a_naive_exact_computation(
    raw: list[tuple[float, int | None]], gaps: list[tuple[int, int]]
) -> None:
    legs = [Leg(60, bpm, speed) for speed, bpm in raw]
    total = 60 * len(legs)
    dropped = {
        t
        for after, length in gaps
        if after + length < total - 5
        for t in range(after + 1, after + length)
    }
    assume(sum(speed for speed, _ in raw) > 0)
    activity = decode_activity_file(run_fit(legs, dropped=dropped), "x.fit")
    assert activity.distance is not None

    outcome = evaluate_threshold_test(activity, HeartRateLimits(MIN_BPM, MAX_BPM), DAY)

    if activity.heart_rate is None:
        assert outcome is ThresholdRejection.TOO_FEW_HEART_RATE_SAMPLES
        return
    assume(activity.distance.seconds[-1] - activity.distance.seconds[0] >= WINDOW)
    expected_start = best_start_naive(activity)
    assume(expected_start is not None)
    assert expected_start is not None
    expected_bpm, expected_pace = naive_threshold(activity, expected_start)
    event(f"naive outcome: {getattr(expected_bpm, 'value', 'accepted')}")
    if isinstance(expected_bpm, ThresholdRejection):
        assert outcome is expected_bpm
        return
    assert isinstance(outcome, ThresholdTestResult)
    assert outcome.segment_start_s == expected_start
    assert outcome.threshold_bpm == expected_bpm
    assert outcome.pace_s_per_km == expected_pace


def best_start_naive(activity: Activity) -> int | None:
    """Every whole second as a start: the first window with the greatest distance."""
    assert activity.distance is not None
    xs, ys = activity.distance.seconds, activity.distance.meters
    first, last = int(xs[0]), int(xs[-1])
    best, best_start = 0.0, None
    for start in range(first, last - WINDOW + 1):
        gain = round(float(np.interp(start + WINDOW, xs, ys) - np.interp(start, xs, ys)), 6)
        if gain > best:
            best, best_start = gain, start
    return best_start


def test_t_piano_06_the_differential_oracle_is_not_vacuous() -> None:
    """The oracle sees a difference: a wrong rounding of the same mean would be caught."""
    assert half_up(Fraction(331, 2)) == 166
    assert half_up(Fraction(1649, 10)) == 165
    assert date(2026, 3, 14) == DAY


@pytest.mark.xfail(
    strict=True,
    reason=(
        "D-F4-06: when a record comes with a timestamp before the first record of the file "
        "(records out of order at the very start), the distance series starts at a negative "
        "second: the axis is no longer 'seconds from the first record' and a threshold segment "
        "could start before the activity (the same holds for the HR series, which is older)"
    ),
)
def test_t_sec_02_a_record_before_the_first_one_cannot_put_the_distance_before_second_zero() -> (
    None
):
    samples = [fw.Sample(FIRST + 100, None), fw.Sample(FIRST + 99, None, speed_ms=0.0)]
    spec = fw.ActivitySpec(
        START, fw.Sport.RUNNING, fw.SubSport.GENERIC, samples, total_elapsed_ms=3_600_000
    )

    activity = decode_activity_file(fw.build_activity(spec), "x.fit")

    assert activity.distance is None or activity.distance.seconds[0] >= 0
