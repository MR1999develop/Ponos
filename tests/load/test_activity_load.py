# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Load of one activity: hand examples, comparison with the naive implementation, invariants.

T-CALC-01, T-CALC-02, T-CALC-03, T-CALC-06 and T-CALC-07 (domain part).
"""

from __future__ import annotations

import math
import statistics

import numpy as np
import pytest
from hypothesis import given
from hypothesis import strategies as st

from ponos_coach.activities.domain import HeartRateSeries, Sport
from ponos_coach.load.domain import (
    ActivityLoad,
    LoadSource,
    Physiology,
    compute_activity_load,
    equivalence_factor,
    load_per_zone,
    reference_hour_weight,
    typical_intensity,
)
from tests.load.helpers import PHYSIOLOGY

# Declared comparison tolerance (T-CALC-01/02): relative rounding error
# of the floating-point sum, far below the precision of the hand examples.
REL_TOL = 1e-9
HAND_TOL = 0.01  # the examples in modello-carico.md are written with 2 decimals


def constant(bpm: float, seconds: int) -> HeartRateSeries:
    """One sample per second at constant HR."""
    return HeartRateSeries(np.arange(seconds, dtype=float), np.full(seconds, float(bpm)))


# --- T-CALC-01: hand-computed values --------------------------------------


def test_t_calc_01_one_hour_at_threshold_is_trimp_eq_100() -> None:
    result = compute_activity_load(Sport.RUNNING, 3600, constant(170, 3600), PHYSIOLOGY)

    assert result.trimp_eq == pytest.approx(100.0, rel=REL_TOL)
    assert reference_hour_weight(PHYSIOLOGY) == pytest.approx(170.65, abs=HAND_TOL * 10)  # Ex. A
    assert result.source is LoadSource.HEART_RATE


def test_t_calc_01_thirty_minutes_at_120_matches_example_b() -> None:
    result = compute_activity_load(Sport.RUNNING, 1800, constant(120, 1800), PHYSIOLOGY)

    assert result.trimp_eq == pytest.approx(14.69, abs=HAND_TOL)


def test_t_calc_01_the_sport_does_not_weigh_the_load() -> None:
    run = compute_activity_load(Sport.RUNNING, 1800, constant(120, 1800), PHYSIOLOGY)
    strength = compute_activity_load(Sport.STRENGTH, 1800, constant(120, 1800), PHYSIOLOGY)

    assert strength.trimp_eq == run.trimp_eq == pytest.approx(14.69, abs=HAND_TOL)


def test_t_calc_01_heart_rate_equal_to_minimum_gives_zero() -> None:
    result = compute_activity_load(Sport.RUNNING, 600, constant(50, 600), PHYSIOLOGY)

    assert result.trimp_eq == 0.0
    assert result.source is LoadSource.HEART_RATE


# --- T-CALC-02: vectorized vs naive ------------------------------------


def naive_load(
    sport: Sport,
    moving_time_s: float,
    series: HeartRateSeries,
    physiology: Physiology,
) -> ActivityLoad:
    """Naive implementation, one loop per sample, written from the spec: Banister on the
    running-equivalent intensity, 100 = one hour at the threshold."""
    seconds = [float(x) for x in series.seconds]
    bpm = [float(x) for x in series.bpm]
    count = len(seconds)
    gaps = [seconds[i + 1] - seconds[i] for i in range(count - 1)]
    validity = [*gaps, statistics.median(gaps) if gaps else 1.0]
    low, high = physiology.limits.min_bpm, physiology.limits.max_bpm
    k = physiology.sex.banister_k
    threshold = physiology.threshold.bpm
    sport_threshold = max(threshold - 4, low + 1) if sport is Sport.CYCLING else threshold
    hrr_run = (threshold - low) / (high - low)
    ratio = hrr_run / ((sport_threshold - low) / (high - low))
    one_hour = 60 * hrr_run * 0.64 * math.exp(k * hrr_run)

    load = counted_s = excluded = 0.0
    used = discarded = 0
    for i in range(count):
        if not low <= bpm[i] <= high:
            discarded += 1
        elif validity[i] > 10:
            excluded += validity[i]
        else:
            eq = min(1.0, (bpm[i] - low) / (high - low) * ratio)
            load += 100 * validity[i] / 60 * eq * 0.64 * math.exp(k * eq) / one_hour
            counted_s += validity[i]
            used += 1
    if round(counted_s * 1000) * 100 < 70 * round(moving_time_s * 1000):  # whole milliseconds
        estimate = moving_time_s / 3600 * 100 * typical_intensity(sport) ** 2
        return ActivityLoad(estimate, 0, discarded, excluded, LoadSource.ESTIMATE)
    return ActivityLoad(load, used, discarded, excluded, LoadSource.HEART_RATE)


@st.composite
def random_activities(draw: st.DrawFn) -> tuple[Sport, float, HeartRateSeries]:
    count = draw(st.integers(1, 80))
    gaps = draw(st.lists(st.floats(0.5, 15.0), min_size=count - 1, max_size=count - 1))
    seconds = np.concatenate(([0.0], np.cumsum(gaps)))
    bpm = draw(st.lists(st.floats(30.0, 220.0), min_size=count, max_size=count))
    duration = float(seconds[-1]) + draw(st.floats(0.5, 40.0))
    return (
        draw(st.sampled_from(list(Sport))),
        duration,
        HeartRateSeries(seconds, np.array(bpm)),
    )


@given(random_activities())
def test_t_calc_02_vectorised_equals_naive(
    case: tuple[Sport, float, HeartRateSeries],
) -> None:
    sport, duration, series = case

    fast = compute_activity_load(sport, duration, series, PHYSIOLOGY)
    slow = naive_load(sport, duration, series, PHYSIOLOGY)

    assert fast.source is slow.source
    assert (fast.used_samples, fast.discarded_samples) == (
        slow.used_samples,
        slow.discarded_samples,
    )
    assert fast.excluded_seconds == pytest.approx(slow.excluded_seconds, rel=REL_TOL, abs=1e-9)
    assert fast.trimp_eq == pytest.approx(slow.trimp_eq, rel=REL_TOL, abs=1e-9)


# --- T-CALC-03: invariants ---------------------------------------------------


@given(random_activities())
def test_t_calc_03_load_is_never_negative(
    case: tuple[Sport, float, HeartRateSeries],
) -> None:
    sport, duration, series = case

    result = compute_activity_load(sport, duration, series, PHYSIOLOGY)

    assert result.trimp_eq >= 0.0


@given(st.integers(60, 600), st.integers(1, 600), st.integers(51, 189))
def test_t_calc_03_load_grows_with_duration(seconds: int, extra: int, bpm: int) -> None:
    short = compute_activity_load(Sport.RUNNING, seconds, constant(bpm, seconds), PHYSIOLOGY)
    long = compute_activity_load(
        Sport.RUNNING, seconds + extra, constant(bpm, seconds + extra), PHYSIOLOGY
    )

    assert long.trimp_eq > short.trimp_eq


@given(st.integers(51, 188), st.integers(1, 20))
def test_t_calc_03_load_grows_with_heart_rate(bpm: int, extra: int) -> None:
    higher = min(bpm + extra, 190)
    low_load = compute_activity_load(Sport.RUNNING, 600, constant(bpm, 600), PHYSIOLOGY)
    high_load = compute_activity_load(Sport.RUNNING, 600, constant(higher, 600), PHYSIOLOGY)

    assert high_load.trimp_eq > low_load.trimp_eq


@given(
    st.lists(st.floats(51.0, 190.0), min_size=5, max_size=60),
    st.sampled_from([0.0, 10.0, 49.9, 190.1, 250.0]),
)
def test_t_calc_03_out_of_range_sample_does_not_change_the_result(
    bpm: list[float], bad: float
) -> None:
    count = len(bpm)
    base = HeartRateSeries(np.arange(count, dtype=float), np.array(bpm))
    with_bad = HeartRateSeries(np.arange(count + 1, dtype=float), np.array([*bpm, bad]))

    clean = compute_activity_load(Sport.RUNNING, count, base, PHYSIOLOGY)
    dirty = compute_activity_load(Sport.RUNNING, count, with_bad, PHYSIOLOGY)

    assert dirty.trimp_eq == pytest.approx(clean.trimp_eq, rel=REL_TOL)
    assert dirty.discarded_samples == clean.discarded_samples + 1
    assert dirty.used_samples == clean.used_samples


@pytest.mark.parametrize("duration", [0.0, -1.0, float("nan"), float("inf")])
def test_invalid_duration_is_rejected(duration: float) -> None:
    with pytest.raises(ValueError, match="duration"):
        compute_activity_load(Sport.RUNNING, duration, None, PHYSIOLOGY)


# --- Rules: intervals, discards, estimate ---------------------------------------


def test_each_sample_is_valid_until_the_next_and_last_takes_the_median() -> None:
    series = HeartRateSeries(np.array([0.0, 1.0, 3.0, 4.0]), np.full(4, 170.0))

    result = compute_activity_load(Sport.RUNNING, 4.0, series, PHYSIOLOGY)

    # Intervals 1, 2, 1 and last = median 1: 5 s at threshold.
    assert result.trimp_eq == pytest.approx(100 * 5 / 3600, abs=1e-3)
    assert result.used_samples == 4


def test_gap_over_ten_seconds_is_not_counted_and_is_reported() -> None:
    seconds = np.concatenate((np.arange(60.0), np.arange(70.0, 130.0)))  # 59 -> 70: 11 s
    series = HeartRateSeries(seconds, np.full(seconds.size, 170.0))

    result = compute_activity_load(Sport.RUNNING, 130.0, series, PHYSIOLOGY)

    assert result.excluded_seconds == 11.0
    assert result.used_samples == 119
    assert result.source is LoadSource.HEART_RATE


def test_gap_of_exactly_ten_seconds_is_counted() -> None:
    series = HeartRateSeries(np.array([0.0, 10.0, 11.0]), np.full(3, 170.0))

    result = compute_activity_load(Sport.RUNNING, 12.0, series, PHYSIOLOGY)

    assert result.excluded_seconds == 0.0


def test_single_sample_is_valid_for_one_second() -> None:
    series = HeartRateSeries(np.array([0.0]), np.array([170.0]))

    result = compute_activity_load(Sport.RUNNING, 1.0, series, PHYSIOLOGY)

    assert result.trimp_eq == pytest.approx(100 / 3600, rel=REL_TOL)


def test_no_heart_rate_is_an_estimate_marked_as_such() -> None:
    result = compute_activity_load(Sport.WALKING, 2400, None, PHYSIOLOGY)

    expected_tss = 2400 / 3600 * 100 * typical_intensity(Sport.WALKING) ** 2
    assert result.source is LoadSource.ESTIMATE
    assert result.trimp_eq == pytest.approx(expected_tss, rel=REL_TOL)
    assert (result.used_samples, result.discarded_samples) == (0, 0)


def test_thirty_percent_discarded_is_still_measured() -> None:
    bpm = np.concatenate((np.full(70, 150.0), np.full(30, 0.0)))
    series = HeartRateSeries(np.arange(100.0), bpm)

    result = compute_activity_load(Sport.RUNNING, 100.0, series, PHYSIOLOGY)

    assert result.source is LoadSource.HEART_RATE
    assert result.discarded_samples == 30


def test_more_than_thirty_percent_discarded_is_an_estimate() -> None:
    bpm = np.concatenate((np.full(69, 150.0), np.full(31, 0.0)))
    series = HeartRateSeries(np.arange(100.0), bpm)

    result = compute_activity_load(Sport.RUNNING, 100.0, series, PHYSIOLOGY)

    assert result.source is LoadSource.ESTIMATE
    assert result.discarded_samples == 31


def test_t_calc_07_seventy_percent_boundary_is_compared_in_integer_milliseconds() -> None:
    # 20 samples at a 0.35 s step add up to 7 s, but in floating point 6.999999999999999: on
    # the 70% limit of 10 s the load remains measured.
    series = HeartRateSeries(np.arange(20, dtype=np.float64) * 0.35, np.full(20, 150.0))
    assert float(np.sum(np.append(np.diff(series.seconds), 0.35))) < 7.0  # the trap

    result = compute_activity_load(Sport.RUNNING, 10.0, series, PHYSIOLOGY)

    assert result.source is LoadSource.HEART_RATE


def test_t_calc_07_just_under_seventy_percent_in_milliseconds_is_an_estimate() -> None:
    series = HeartRateSeries(np.arange(20, dtype=np.float64) * 0.35, np.full(20, 150.0))

    result = compute_activity_load(Sport.RUNNING, 10.001, series, PHYSIOLOGY)

    assert result.source is LoadSource.ESTIMATE


def test_t_calc_07_the_rule_uses_the_moving_time_given_not_a_longer_elapsed_time() -> None:
    series = constant(150, 1800)

    moving = compute_activity_load(Sport.RUNNING, 1800.0, series, PHYSIOLOGY)
    with_pauses_counted = compute_activity_load(Sport.RUNNING, 7200.0, series, PHYSIOLOGY)

    assert moving.source is LoadSource.HEART_RATE
    assert with_pauses_counted.source is LoadSource.ESTIMATE


def test_t_calc_06_bike_threshold_is_four_bpm_lower() -> None:
    at_bike_threshold = constant(166, 3600)

    bike = compute_activity_load(Sport.CYCLING, 3600, at_bike_threshold, PHYSIOLOGY)
    run = compute_activity_load(Sport.RUNNING, 3600, at_bike_threshold, PHYSIOLOGY)

    assert bike.trimp_eq == pytest.approx(100.0, rel=REL_TOL)
    assert run.trimp_eq < 100.0


# --- Load per zone ---------------------------------------------------------------


@given(random_activities())
def test_the_load_per_zone_adds_up_to_the_measured_load(
    case: tuple[Sport, float, HeartRateSeries],
) -> None:
    sport, duration, series = case
    load = compute_activity_load(sport, duration, series, PHYSIOLOGY)

    parts = load_per_zone(sport, series, PHYSIOLOGY, load)

    if load.source is LoadSource.ESTIMATE or load.trimp_eq == 0:
        assert parts is None
    else:
        assert parts is not None
        assert all(part >= 0 for part in parts)
        assert math.fsum(parts) == pytest.approx(load.trimp_eq, rel=REL_TOL, abs=1e-9)


def test_an_estimated_load_has_no_load_per_zone() -> None:
    load = compute_activity_load(Sport.RUNNING, 1800.0, None, PHYSIOLOGY)

    assert load_per_zone(Sport.RUNNING, None, PHYSIOLOGY, load) is None


# --- TRIMPeq: the running-equivalent intensity -----------------------------------------


def test_the_equivalence_ratio_is_one_without_a_threshold_of_the_sports_own() -> None:
    for sport in (
        Sport.RUNNING,
        Sport.TRAIL,
        Sport.HIKING,
        Sport.WALKING,
        Sport.STRENGTH,
        Sport.OTHER,
    ):
        assert equivalence_factor(PHYSIOLOGY, sport) == 1.0
    assert equivalence_factor(PHYSIOLOGY, Sport.CYCLING) == pytest.approx(120 / 116)


@pytest.mark.parametrize("sport", [Sport.TRAIL, Sport.HIKING, Sport.WALKING, Sport.STRENGTH])
def test_a_sport_without_a_threshold_of_its_own_loads_like_running(sport: Sport) -> None:
    series = constant(150, 1800)

    other = compute_activity_load(sport, 1800, series, PHYSIOLOGY)
    run = compute_activity_load(Sport.RUNNING, 1800, series, PHYSIOLOGY)

    assert other.trimp_eq == run.trimp_eq


def test_one_hour_at_the_bike_threshold_is_100_as_one_hour_at_the_running_one() -> None:
    bike = compute_activity_load(Sport.CYCLING, 3600, constant(166, 3600), PHYSIOLOGY)

    assert bike.trimp_eq == pytest.approx(100.0, rel=REL_TOL)


def test_the_equivalent_intensity_is_clamped_to_one_at_the_boundary() -> None:
    # bike: HRr * 120/116 reaches 1 at HRr = 116/120, i.e. 185.333... bpm (on point); a bpm
    # below stays under the clamp (off point), one above is clamped to the maximum weight.
    on_point = 50 + 140 * 116 / 120
    at = {
        bpm: compute_activity_load(Sport.CYCLING, 600, constant(bpm, 600), PHYSIOLOGY)
        for bpm in (185.0, on_point, 186.0, 190.0)
    }

    assert at[185.0].trimp_eq < at[on_point].trimp_eq
    assert at[on_point].trimp_eq == pytest.approx(at[190.0].trimp_eq, rel=1e-12)
    assert at[186.0].trimp_eq == pytest.approx(at[190.0].trimp_eq, rel=1e-12)
