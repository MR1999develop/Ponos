# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Carico di una attività: esempi a mano, confronto con l'implementazione ingenua, invarianti.

T-CALC-01, T-CALC-02, T-CALC-03, T-CALC-06 (parte del dominio).
"""

from __future__ import annotations

import math
import statistics

import numpy as np
import pytest
from hypothesis import given
from hypothesis import strategies as st

from ponos_coach.load.domain import (
    ActivityLoad,
    HeartRateSeries,
    LoadSource,
    Physiology,
    Sport,
    SportWeight,
    compute_activity_load,
    default_weight,
    typical_intensity,
)
from tests.load.helpers import PHYSIOLOGY, WEIGHT_ONE

# Tolleranza dichiarata dei confronti (T-CALC-01/02): errore relativo di arrotondamento
# della somma in virgola mobile, molto sotto la precisione degli esempi a mano.
REL_TOL = 1e-9
HAND_TOL = 0.01  # gli esempi di modello-carico.md sono scritti con 2 decimali


def constant(bpm: float, seconds: int) -> HeartRateSeries:
    """Un campione al secondo a FC costante."""
    return HeartRateSeries(np.arange(seconds, dtype=float), np.full(seconds, float(bpm)))


# --- T-CALC-01: valori calcolati a mano --------------------------------------


def test_t_calc_01_one_hour_at_threshold_is_hrtss_100() -> None:
    result = compute_activity_load(Sport.RUNNING, 3600, constant(170, 3600), PHYSIOLOGY, WEIGHT_ONE)

    assert result.hr_tss == pytest.approx(100.0, rel=REL_TOL)
    assert result.trimp == pytest.approx(170.65, abs=HAND_TOL * 10)  # Esempio A
    assert result.source is LoadSource.HEART_RATE


def test_t_calc_01_thirty_minutes_at_120_matches_example_b() -> None:
    result = compute_activity_load(Sport.RUNNING, 1800, constant(120, 1800), PHYSIOLOGY, WEIGHT_ONE)

    assert result.trimp == pytest.approx(25.07, abs=HAND_TOL)
    assert result.hr_tss == pytest.approx(14.69, abs=HAND_TOL)
    assert result.load == pytest.approx(14.69, abs=HAND_TOL)


def test_t_calc_01_strength_weight_applies_to_example_b() -> None:
    result = compute_activity_load(
        Sport.STRENGTH, 1800, constant(120, 1800), PHYSIOLOGY, default_weight(Sport.STRENGTH)
    )

    assert result.load == pytest.approx(17.6, abs=0.05)


def test_t_calc_01_heart_rate_equal_to_minimum_gives_zero() -> None:
    result = compute_activity_load(Sport.RUNNING, 600, constant(50, 600), PHYSIOLOGY, WEIGHT_ONE)

    assert result.trimp == 0.0
    assert result.load == 0.0
    assert result.source is LoadSource.HEART_RATE


# --- T-CALC-02: vettoriale contro ingenuo ------------------------------------


def naive_load(
    sport: Sport,
    duration_s: float,
    series: HeartRateSeries,
    physiology: Physiology,
    weight: SportWeight,
) -> ActivityLoad:
    """Implementazione ingenua, un ciclo per campione, scritta dalla specifica."""
    seconds = [float(x) for x in series.seconds]
    bpm = [float(x) for x in series.bpm]
    count = len(seconds)
    gaps = [seconds[i + 1] - seconds[i] for i in range(count - 1)]
    validity = [*gaps, statistics.median(gaps) if gaps else 1.0]
    low, high = physiology.limits.min_bpm, physiology.limits.max_bpm
    k = physiology.sex.banister_k
    threshold = physiology.threshold.bpm - (4 if sport is Sport.CYCLING else 0)
    hrr_t = (threshold - low) / (high - low)
    one_hour = 60 * hrr_t * 0.64 * math.exp(k * hrr_t)

    trimp = counted_s = excluded = 0.0
    used = discarded = 0
    for i in range(count):
        if not low <= bpm[i] <= high:
            discarded += 1
        elif validity[i] > 10:
            excluded += validity[i]
        else:
            hrr = (bpm[i] - low) / (high - low)
            trimp += validity[i] / 60 * hrr * 0.64 * math.exp(k * hrr)
            counted_s += validity[i]
            used += 1
    if counted_s < 0.7 * duration_s:
        hr_tss = duration_s / 3600 * 100 * typical_intensity(sport) ** 2
        return ActivityLoad(
            hr_tss / 100 * one_hour, hr_tss, hr_tss * weight.value, 0, discarded, excluded,
            LoadSource.ESTIMATE,
        )  # fmt: skip
    hr_tss = 100 * trimp / one_hour
    return ActivityLoad(
        trimp, hr_tss, hr_tss * weight.value, used, discarded, excluded, LoadSource.HEART_RATE
    )


@st.composite
def random_activities(draw: st.DrawFn) -> tuple[Sport, float, HeartRateSeries, SportWeight]:
    count = draw(st.integers(1, 80))
    gaps = draw(st.lists(st.floats(0.5, 15.0), min_size=count - 1, max_size=count - 1))
    seconds = np.concatenate(([0.0], np.cumsum(gaps)))
    bpm = draw(st.lists(st.floats(30.0, 220.0), min_size=count, max_size=count))
    duration = float(seconds[-1]) + draw(st.floats(0.5, 40.0))
    weight = SportWeight(draw(st.floats(0.0, 2.0)))
    return (
        draw(st.sampled_from(list(Sport))),
        duration,
        HeartRateSeries(seconds, np.array(bpm)),
        weight,
    )


@given(random_activities())
def test_t_calc_02_vectorised_equals_naive(
    case: tuple[Sport, float, HeartRateSeries, SportWeight],
) -> None:
    sport, duration, series, weight = case

    fast = compute_activity_load(sport, duration, series, PHYSIOLOGY, weight)
    slow = naive_load(sport, duration, series, PHYSIOLOGY, weight)

    assert fast.source is slow.source
    assert (fast.used_samples, fast.discarded_samples) == (
        slow.used_samples,
        slow.discarded_samples,
    )
    assert fast.excluded_seconds == pytest.approx(slow.excluded_seconds, rel=REL_TOL, abs=1e-9)
    assert fast.trimp == pytest.approx(slow.trimp, rel=REL_TOL, abs=1e-9)
    assert fast.hr_tss == pytest.approx(slow.hr_tss, rel=REL_TOL, abs=1e-9)
    assert fast.load == pytest.approx(slow.load, rel=REL_TOL, abs=1e-9)


# --- T-CALC-03: invarianti ---------------------------------------------------


@given(random_activities())
def test_t_calc_03_load_is_never_negative(
    case: tuple[Sport, float, HeartRateSeries, SportWeight],
) -> None:
    sport, duration, series, weight = case

    result = compute_activity_load(sport, duration, series, PHYSIOLOGY, weight)

    assert result.trimp >= 0.0
    assert result.hr_tss >= 0.0
    assert result.load >= 0.0


@given(st.integers(60, 600), st.integers(1, 600), st.integers(51, 189))
def test_t_calc_03_load_grows_with_duration(seconds: int, extra: int, bpm: int) -> None:
    short = compute_activity_load(
        Sport.RUNNING, seconds, constant(bpm, seconds), PHYSIOLOGY, WEIGHT_ONE
    )
    long = compute_activity_load(
        Sport.RUNNING, seconds + extra, constant(bpm, seconds + extra), PHYSIOLOGY, WEIGHT_ONE
    )

    assert long.load > short.load


@given(st.integers(51, 188), st.integers(1, 20))
def test_t_calc_03_load_grows_with_heart_rate(bpm: int, extra: int) -> None:
    higher = min(bpm + extra, 190)
    low_load = compute_activity_load(Sport.RUNNING, 600, constant(bpm, 600), PHYSIOLOGY, WEIGHT_ONE)
    high_load = compute_activity_load(
        Sport.RUNNING, 600, constant(higher, 600), PHYSIOLOGY, WEIGHT_ONE
    )

    assert high_load.load > low_load.load


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

    clean = compute_activity_load(Sport.RUNNING, count, base, PHYSIOLOGY, WEIGHT_ONE)
    dirty = compute_activity_load(Sport.RUNNING, count, with_bad, PHYSIOLOGY, WEIGHT_ONE)

    assert dirty.trimp == pytest.approx(clean.trimp, rel=REL_TOL)
    assert dirty.load == pytest.approx(clean.load, rel=REL_TOL)
    assert dirty.discarded_samples == clean.discarded_samples + 1
    assert dirty.used_samples == clean.used_samples


@given(random_activities())
def test_t_calc_03_weight_zero_gives_zero_load(
    case: tuple[Sport, float, HeartRateSeries, SportWeight],
) -> None:
    sport, duration, series, _ = case

    result = compute_activity_load(sport, duration, series, PHYSIOLOGY, SportWeight(0.0))

    assert result.load == 0.0


@pytest.mark.parametrize("duration", [0.0, -1.0, float("nan"), float("inf")])
def test_invalid_duration_is_rejected(duration: float) -> None:
    with pytest.raises(ValueError, match="durata"):
        compute_activity_load(Sport.RUNNING, duration, None, PHYSIOLOGY, WEIGHT_ONE)


# --- Regole: intervalli, scarti, stima ---------------------------------------


def test_each_sample_is_valid_until_the_next_and_last_takes_the_median() -> None:
    series = HeartRateSeries(np.array([0.0, 1.0, 3.0, 4.0]), np.full(4, 170.0))

    result = compute_activity_load(Sport.RUNNING, 4.0, series, PHYSIOLOGY, WEIGHT_ONE)

    # Intervalli 1, 2, 1 e ultimo = mediana 1: 5 s a soglia.
    assert result.trimp == pytest.approx(5 / 60 * 2.8442, abs=1e-3)
    assert result.used_samples == 4


def test_gap_over_ten_seconds_is_not_counted_and_is_reported() -> None:
    seconds = np.concatenate((np.arange(60.0), np.arange(70.0, 130.0)))  # 59 -> 70: 11 s
    series = HeartRateSeries(seconds, np.full(seconds.size, 170.0))

    result = compute_activity_load(Sport.RUNNING, 130.0, series, PHYSIOLOGY, WEIGHT_ONE)

    assert result.excluded_seconds == 11.0
    assert result.used_samples == 119
    assert result.source is LoadSource.HEART_RATE


def test_gap_of_exactly_ten_seconds_is_counted() -> None:
    series = HeartRateSeries(np.array([0.0, 10.0, 11.0]), np.full(3, 170.0))

    result = compute_activity_load(Sport.RUNNING, 12.0, series, PHYSIOLOGY, WEIGHT_ONE)

    assert result.excluded_seconds == 0.0


def test_single_sample_is_valid_for_one_second() -> None:
    series = HeartRateSeries(np.array([0.0]), np.array([170.0]))

    result = compute_activity_load(Sport.RUNNING, 1.0, series, PHYSIOLOGY, WEIGHT_ONE)

    assert result.hr_tss == pytest.approx(100 / 3600, rel=REL_TOL)


def test_no_heart_rate_is_an_estimate_marked_as_such() -> None:
    result = compute_activity_load(
        Sport.WALKING, 2400, None, PHYSIOLOGY, default_weight(Sport.WALKING)
    )

    expected_tss = 2400 / 3600 * 100 * typical_intensity(Sport.WALKING) ** 2
    assert result.source is LoadSource.ESTIMATE
    assert result.hr_tss == pytest.approx(expected_tss, rel=REL_TOL)
    assert result.load == pytest.approx(expected_tss * 0.6, rel=REL_TOL)
    assert (result.used_samples, result.discarded_samples) == (0, 0)


def test_thirty_percent_discarded_is_still_measured() -> None:
    bpm = np.concatenate((np.full(70, 150.0), np.full(30, 0.0)))
    series = HeartRateSeries(np.arange(100.0), bpm)

    result = compute_activity_load(Sport.RUNNING, 100.0, series, PHYSIOLOGY, WEIGHT_ONE)

    assert result.source is LoadSource.HEART_RATE
    assert result.discarded_samples == 30


def test_more_than_thirty_percent_discarded_is_an_estimate() -> None:
    bpm = np.concatenate((np.full(69, 150.0), np.full(31, 0.0)))
    series = HeartRateSeries(np.arange(100.0), bpm)

    result = compute_activity_load(Sport.RUNNING, 100.0, series, PHYSIOLOGY, WEIGHT_ONE)

    assert result.source is LoadSource.ESTIMATE
    assert result.discarded_samples == 31


def test_t_calc_06_bike_threshold_is_four_bpm_lower() -> None:
    at_bike_threshold = constant(166, 3600)

    bike = compute_activity_load(Sport.CYCLING, 3600, at_bike_threshold, PHYSIOLOGY, WEIGHT_ONE)
    run = compute_activity_load(Sport.RUNNING, 3600, at_bike_threshold, PHYSIOLOGY, WEIGHT_ONE)

    assert bike.hr_tss == pytest.approx(100.0, rel=REL_TOL)
    assert run.hr_tss < 100.0
