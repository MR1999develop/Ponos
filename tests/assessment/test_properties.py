# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Property tests of the threshold test: invariants and comparison with a naive implementation.

At one record a second every breakpoint is a whole second, so trying every whole second as a start
(the naive way, one loop per start) finds the same best window as the vectorized search.
"""

from __future__ import annotations

import math
from datetime import date

import numpy as np
from hypothesis import given
from hypothesis import strategies as st

from ponos_coach.activities.domain import Activity
from ponos_coach.assessment.domain import (
    ThresholdRejection,
    ThresholdTestResult,
    evaluate_threshold_test,
)
from ponos_coach.assessment.domain.segment import HeartRateTimeline, best_start_by_distance
from tests.assessment.builders import LIMITS, Leg, synthetic_run

DAY = date(2026, 3, 2)
WINDOW = 1800
LAST = 1200

# A run of 1800 to 2700 seconds made of blocks of 60 s at a random speed and HR (some out of the
# profile limits, some missing).
blocks = st.lists(
    st.tuples(
        st.floats(0.0, 6.0, allow_nan=False).map(lambda v: round(v, 2)),
        st.one_of(st.integers(40, 195), st.none()),
    ),
    min_size=30,
    max_size=45,
)


def legs_of(raw: list[tuple[float, int | None]]) -> list[Leg]:
    return [Leg(60, bpm, speed) for speed, bpm in raw]


def naive_best_by_distance(meters: np.ndarray) -> int | None:
    """Every whole second as a start, one subtraction each; the first of the best."""
    best, best_start = 0.0, None
    for start in range(len(meters) - WINDOW):
        gained = round(float(meters[start + WINDOW] - meters[start]), 6)
        if gained > best:
            best, best_start = gained, start
    return best_start


@given(blocks)
def test_the_chosen_window_is_the_one_with_the_greatest_distance_the_first_on_a_tie(
    raw: list[tuple[float, int | None]],
) -> None:
    run = synthetic_run(legs_of(raw))
    assert run.distance is not None

    chosen = best_start_by_distance(run.distance)

    expected = naive_best_by_distance(run.distance.meters)
    if expected is not None:  # the best window must also be a running pace (90-3600 s/km)
        gained = float(run.distance.meters[expected + WINDOW] - run.distance.meters[expected])
        if not 90 <= WINDOW / (gained / 1000) <= 3600:
            expected = None
    assert chosen == (None if expected is None else float(expected))


def per_half_second_valid_bpm(run: Activity) -> np.ndarray:
    """The counted HR of each half second, NaN where no counted sample covers it.

    A sample is valid until the next one; it counts if its bpm is inside the profile limits and
    the interval is 10 s or less (the last sample is valid for the median interval, a multiple of
    half a second here).
    """
    series = run.heart_rate
    assert series is not None
    seconds = series.seconds.astype(int)
    gaps = 2 * np.diff(seconds)
    validity = np.append(gaps, round(float(np.median(gaps))) if gaps.size else 2)
    covered = np.full(2 * int(seconds[-1]) + int(validity[-1]), np.nan)
    for start, length, bpm in zip(2 * seconds, validity, series.bpm, strict=True):
        if length <= 2 * 10 and LIMITS.min_bpm <= bpm <= LIMITS.max_bpm:
            covered[start : start + length] = bpm
    return covered


@given(blocks)
def test_the_hr_window_is_the_one_with_the_highest_mean_valid_hr(
    raw: list[tuple[float, int | None]],
) -> None:
    run = synthetic_run(legs_of(raw), with_distance=False)
    if run.heart_rate is None:
        return
    timeline = HeartRateTimeline.of(run.heart_rate, LIMITS)
    covered = per_half_second_valid_bpm(run)
    valid = ~np.isnan(covered)
    seconds_before = np.concatenate(([0], np.cumsum(valid)))
    bpm_before = np.concatenate(([0.0], np.cumsum(np.where(valid, covered, 0.0))))
    length = 2 * WINDOW
    starts = np.arange(len(covered) - length + 1)
    counted = seconds_before[starts + length] - seconds_before[starts]
    sums = bpm_before[starts + length] - bpm_before[starts]

    chosen = timeline.best_start_by_mean_bpm()

    if not (counted > 0).any():
        assert chosen is None
        return
    means = np.where(counted > 0, np.round(sums / np.maximum(counted, 1), 9), -np.inf)
    assert chosen == int(np.argmax(means)) / 2


@given(blocks)
def test_the_threshold_hr_lies_between_the_extremes_of_the_valid_samples_of_the_last_20_minutes(
    raw: list[tuple[float, int | None]],
) -> None:
    run = synthetic_run(legs_of(raw))
    outcome = evaluate_threshold_test(run, LIMITS, DAY)
    if not isinstance(outcome, ThresholdTestResult):
        return
    assert run.heart_rate is not None
    start = int(outcome.segment_start_s) + WINDOW - LAST
    seconds, bpm = run.heart_rate.seconds, run.heart_rate.bpm
    in_window = (seconds >= start) & (seconds < start + LAST)
    valid = bpm[in_window & (bpm >= LIMITS.min_bpm) & (bpm <= LIMITS.max_bpm)]

    assert valid.size == outcome.valid_hr_samples
    assert valid.min() <= outcome.threshold_bpm <= valid.max()
    assert LIMITS.min_bpm < outcome.threshold_bpm < LIMITS.max_bpm


@given(blocks)
def test_the_threshold_hr_is_the_rounded_mean_of_the_valid_samples_of_the_last_20_minutes(
    raw: list[tuple[float, int | None]],
) -> None:
    run = synthetic_run(legs_of(raw))
    outcome = evaluate_threshold_test(run, LIMITS, DAY)
    if not isinstance(outcome, ThresholdTestResult):
        return
    assert run.heart_rate is not None
    start = int(outcome.segment_start_s) + WINDOW - LAST
    seconds, bpm = run.heart_rate.seconds, run.heart_rate.bpm
    valid = bpm[(seconds >= start) & (seconds < start + LAST) & (bpm >= 50) & (bpm <= 190)]

    assert outcome.threshold_bpm == math.floor(round(float(valid.mean()), 9) + 0.5)


@given(blocks)
def test_a_rejection_always_comes_with_a_reason_and_never_an_error(
    raw: list[tuple[float, int | None]],
) -> None:
    outcome = evaluate_threshold_test(synthetic_run(legs_of(raw)), LIMITS, DAY)

    assert isinstance(outcome, (ThresholdTestResult, ThresholdRejection))
