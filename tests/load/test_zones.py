# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Zone di FC sulla soglia (Friel): confini, secondi per zona e regole del carico."""

from __future__ import annotations

import numpy as np
import pytest
from hypothesis import given
from hypothesis import strategies as st

from ponos_coach.activities.domain import HeartRateSeries, Sport
from ponos_coach.load.domain import (
    ZONE_COUNT,
    ZONE_STARTS_PERCENT,
    LoadSource,
    ZoneSeconds,
    compute_activity_load,
    seconds_per_zone,
    zone_indices,
    zone_starts_bpm,
)
from tests.load.helpers import PHYSIOLOGY, WEIGHT_ONE

# FCmin 50, FCmax 190, soglia 170: confini al 85%, 90%, 95% e 100% = 144,5 - 153 - 161,5 - 170.


def series_at_1_hz(*bpm: float) -> HeartRateSeries:
    return HeartRateSeries(np.arange(len(bpm), dtype=np.float64), np.array(bpm, dtype=np.float64))


def zones_of(*bpm: float, threshold: int = 170) -> list[int]:
    return [int(i) for i in zone_indices(np.array(bpm, dtype=np.float64), threshold)]


def test_the_project_default_percentages_are_friels_five_zones() -> None:
    assert ZONE_STARTS_PERCENT == (85, 90, 95, 100)
    assert ZONE_COUNT == 5


def test_zone_starts_in_bpm_for_a_threshold_of_170() -> None:
    assert zone_starts_bpm(170) == (144.5, 153.0, 161.5, 170.0)


def test_each_boundary_belongs_to_the_higher_zone_on_point_and_off_point() -> None:
    # Z1 < 85% | Z2 85-89% | Z3 90-94% | Z4 95-99% | Z5 >= 100% della soglia (170)
    assert zones_of(144, 145) == [0, 1]  # 144,5 è il confine: 144 sotto, 145 sopra
    assert zones_of(152, 153) == [1, 2]  # 153 è esattamente il 90%: Z3
    assert zones_of(161, 162) == [2, 3]  # 161,5 è il confine
    assert zones_of(169, 170) == [3, 4]  # 170 è esattamente il 100%: Z5


def test_extremes_fall_in_the_first_and_last_zone() -> None:
    assert zones_of(0, 50, 190, 250) == [0, 0, 4, 4]


def test_boundary_with_a_fractional_threshold_percentage_is_exact() -> None:
    # soglia 166 (bici di una soglia corsa 170): 85% = 141,1; 90% = 149,4; 95% = 157,7
    assert zones_of(141, 142, threshold=166) == [0, 1]
    assert zones_of(149, 150, threshold=166) == [1, 2]
    assert zones_of(157, 158, threshold=166) == [2, 3]
    assert zones_of(165, 166, threshold=166) == [3, 4]


def test_seconds_per_zone_sums_the_seconds_of_each_zone() -> None:
    bpm = np.array([100.0, 144.0, 145.0, 152.0, 153.0, 161.0, 162.0, 169.0, 170.0, 190.0])
    seconds = np.array([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0])

    assert seconds_per_zone(bpm, seconds, 170).values == (3.0, 7.0, 11.0, 15.0, 19.0)


def test_no_samples_give_zero_seconds_in_every_zone() -> None:
    empty = np.zeros(0)

    assert seconds_per_zone(empty, empty, 170) == ZoneSeconds.ZERO


def test_activity_load_zone_seconds_by_hand() -> None:
    """Dieci campioni a 1 Hz, uno per zona più i due estremi: ogni campione vale 1 s."""
    series = series_at_1_hz(100, 144, 145, 152, 153, 161, 162, 169, 170, 190)

    result = compute_activity_load(Sport.RUNNING, 10.0, series, PHYSIOLOGY, WEIGHT_ONE)

    assert result.source is LoadSource.HEART_RATE
    assert result.zone_seconds.values == (2.0, 2.0, 2.0, 2.0, 2.0)
    assert result.zone_seconds.total == result.used_samples


def test_zones_use_the_same_samples_as_the_load_discarded_ones_are_in_no_zone() -> None:
    # 200 bpm è sopra la massima (190) e 40 sotto la minima (50): scartati e fuori da ogni zona
    series = series_at_1_hz(150, 200, 150, 40, 150, 150, 150, 150, 150, 150)

    result = compute_activity_load(Sport.RUNNING, 10.0, series, PHYSIOLOGY, WEIGHT_ONE)

    assert result.discarded_samples == 2
    assert result.zone_seconds.values == (0.0, 8.0, 0.0, 0.0, 0.0)  # 150 bpm = 88% di 170: Z2
    assert result.zone_seconds.total == result.used_samples


def test_150_bpm_with_threshold_170_is_zone_two() -> None:
    result = compute_activity_load(
        Sport.RUNNING, 3.0, series_at_1_hz(150, 150, 150), PHYSIOLOGY, WEIGHT_ONE
    )

    assert result.zone_seconds.values == (0.0, 3.0, 0.0, 0.0, 0.0)


def test_gaps_over_ten_seconds_are_in_no_zone() -> None:
    seconds = np.array([0.0, 1.0, 2.0, 30.0, 31.0, 32.0, 33.0, 34.0, 35.0, 36.0])
    series = HeartRateSeries(seconds, np.full(10, 175.0))

    result = compute_activity_load(Sport.RUNNING, 10.0, series, PHYSIOLOGY, WEIGHT_ONE)

    # il campione a 2 s vale fino a 30 s (28 s): intervallo escluso dal carico e dalle zone
    assert result.excluded_seconds == 28.0
    assert result.zone_seconds.values == (0.0, 0.0, 0.0, 0.0, 9.0)


def test_the_cycling_zones_use_the_bike_threshold_four_bpm_lower() -> None:
    """T-CALC-06: in bici la soglia è la soglia della corsa meno 4 bpm anche per le zone."""
    series = series_at_1_hz(*[166.0] * 10)  # 166 bpm = soglia in bici

    bike = compute_activity_load(Sport.CYCLING, 10.0, series, PHYSIOLOGY, WEIGHT_ONE)
    run = compute_activity_load(Sport.RUNNING, 10.0, series, PHYSIOLOGY, WEIGHT_ONE)

    assert bike.zone_seconds.values == (0.0, 0.0, 0.0, 0.0, 10.0)  # Z5 sulla soglia della bici
    assert run.zone_seconds.values == (0.0, 0.0, 0.0, 10.0, 0.0)  # 166/170 = 97,6%: Z4


def test_an_activity_without_heart_rate_has_no_zone_seconds() -> None:
    result = compute_activity_load(Sport.RUNNING, 3600.0, None, PHYSIOLOGY, WEIGHT_ONE)

    assert result.source is LoadSource.ESTIMATE
    assert result.zone_seconds == ZoneSeconds.ZERO


def test_an_estimate_from_mostly_missing_samples_keeps_the_measured_zone_seconds() -> None:
    series = series_at_1_hz(150, 150, 150)  # 3 s misurati su 100 s in movimento

    result = compute_activity_load(Sport.RUNNING, 100.0, series, PHYSIOLOGY, WEIGHT_ONE)

    assert result.source is LoadSource.ESTIMATE
    assert result.zone_seconds.values == (0.0, 3.0, 0.0, 0.0, 0.0)


def test_zone_seconds_must_be_five_finite_non_negative_numbers() -> None:
    with pytest.raises(ValueError, match="servono"):
        ZoneSeconds((1.0, 2.0))  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="non negativi"):
        ZoneSeconds((1.0, -0.5, 0.0, 0.0, 0.0))
    with pytest.raises(ValueError, match="finiti"):
        ZoneSeconds((1.0, float("nan"), 0.0, 0.0, 0.0))


def _naive_zone(bpm: int, threshold: int) -> int:
    """Una zona alla volta con soli interi: la prima percentuale superata dal campione."""
    zone = 0
    for percent in ZONE_STARTS_PERCENT:
        if bpm * 100 >= percent * threshold:
            zone += 1
    return zone


@given(
    bpm=st.lists(st.integers(min_value=30, max_value=250), min_size=1, max_size=200),
    threshold=st.integers(min_value=40, max_value=250),
)
def test_vectorised_zones_equal_the_naive_integer_comparison(
    bpm: list[int], threshold: int
) -> None:
    expected = [_naive_zone(value, threshold) for value in bpm]

    found = zone_indices(np.array(bpm, dtype=np.float64), threshold)

    assert found.tolist() == expected


@given(
    bpm=st.lists(st.integers(min_value=30, max_value=250), min_size=1, max_size=100),
    threshold=st.integers(min_value=40, max_value=250),
)
def test_seconds_per_zone_equal_the_naive_sum_and_add_up_to_the_total(
    bpm: list[int], threshold: int
) -> None:
    seconds = np.arange(1, len(bpm) + 1, dtype=np.float64)
    expected = [0.0] * ZONE_COUNT
    for value, sec in zip(bpm, seconds.tolist(), strict=True):
        expected[_naive_zone(value, threshold)] += sec

    found = seconds_per_zone(np.array(bpm, dtype=np.float64), seconds, threshold)

    assert found.values == pytest.approx(tuple(expected))
    assert found.total == pytest.approx(float(seconds.sum()))


@given(
    bpm=st.lists(st.integers(min_value=50, max_value=190), min_size=2, max_size=300),
)
def test_zone_seconds_of_a_load_always_add_up_to_the_counted_seconds(bpm: list[int]) -> None:
    series = series_at_1_hz(*[float(v) for v in bpm])

    result = compute_activity_load(Sport.RUNNING, float(len(bpm)), series, PHYSIOLOGY, WEIGHT_ONE)

    assert result.zone_seconds.total == pytest.approx(result.used_samples)
