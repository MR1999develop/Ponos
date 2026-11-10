# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""HR zones on threshold (user's choice of 2026-11-10): boundaries, seconds, load rules."""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest
from hypothesis import given
from hypothesis import strategies as st

from ponos_coach.activities.domain import HeartRateSeries, Sport
from ponos_coach.load.domain import (
    ZONE_COUNT,
    ZONE_STARTS_PERCENT,
    LoadSource,
    ThresholdHeartRate,
    ZoneSeconds,
    compute_activity_load,
    load_fingerprint,
    seconds_per_zone,
    zone_bpm_ranges,
    zone_indices,
    zone_starts_bpm,
)
from ponos_coach.load.domain import parameters as parameters_module
from ponos_coach.load.domain import zones as zones_module
from tests.load.helpers import PHYSIOLOGY

# FCmin 50, FCmax 190, threshold 170: boundaries at 75, 85, 95, 103% = 127.5, 144.5, 161.5, 175.1.


def series_at_1_hz(*bpm: float) -> HeartRateSeries:
    return HeartRateSeries(np.arange(len(bpm), dtype=np.float64), np.array(bpm, dtype=np.float64))


def zones_of(*bpm: float, threshold: int = 170) -> list[int]:
    return [int(i) for i in zone_indices(np.array(bpm, dtype=np.float64), threshold)]


def test_the_default_percentages_are_the_five_zones_chosen_by_the_user() -> None:
    assert ZONE_STARTS_PERCENT == (75, 85, 95, 103)
    assert ZONE_COUNT == 5


def test_zone_starts_in_bpm_for_a_threshold_of_170() -> None:
    assert zone_starts_bpm(170) == pytest.approx((127.5, 144.5, 161.5, 175.1))


def test_each_boundary_belongs_to_the_higher_zone_on_point_and_off_point() -> None:
    # Z1 < 75% | Z2 75-84% | Z3 85-94% | Z4 95-102% | Z5 >= 103% of threshold (170)
    assert zones_of(127, 128) == [0, 1]  # 127.5 is the boundary: 127 below, 128 above
    assert zones_of(144, 145) == [1, 2]  # 144.5 is the boundary
    assert zones_of(161, 162) == [2, 3]  # 161.5 is the boundary
    assert zones_of(175, 176) == [3, 4]  # 175.1 is the boundary
    assert zones_of(1, 175, threshold=170) == [0, 3]  # 175 is 102.9%: still Z4
    # a whole threshold: the boundaries 75, 85, 95, 103 are exact
    assert zones_of(74, 75, 84, 85, 94, 95, 102, 103, threshold=100) == [0, 1, 1, 2, 2, 3, 3, 4]


def test_extremes_fall_in_the_first_and_last_zone() -> None:
    assert zones_of(0, 50, 190, 250) == [0, 0, 4, 4]


def test_boundary_with_a_fractional_threshold_percentage_is_exact() -> None:
    # threshold 166 (a fractional share of it):
    # 75% = 124.5; 85% = 141.1; 95% = 157.7; 103% = 170.98
    assert zones_of(124, 125, threshold=166) == [0, 1]
    assert zones_of(141, 142, threshold=166) == [1, 2]
    assert zones_of(157, 158, threshold=166) == [2, 3]
    assert zones_of(170, 171, threshold=166) == [3, 4]


def test_seconds_per_zone_sums_the_seconds_of_each_zone() -> None:
    bpm = np.array([100.0, 127.0, 128.0, 144.0, 145.0, 161.0, 162.0, 175.0, 176.0, 190.0])
    seconds = np.array([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0])

    assert seconds_per_zone(bpm, seconds, 170).values == (3.0, 7.0, 11.0, 15.0, 19.0)


def test_no_samples_give_zero_seconds_in_every_zone() -> None:
    empty = np.zeros(0)

    assert seconds_per_zone(empty, empty, 170) == ZoneSeconds.ZERO


def test_activity_load_zone_seconds_by_hand() -> None:
    """Ten samples at 1 Hz, one per zone plus the two extremes: each sample is worth 1 s."""
    series = series_at_1_hz(100, 127, 128, 144, 145, 161, 162, 175, 176, 190)

    result = compute_activity_load(Sport.RUNNING, 10.0, series, PHYSIOLOGY)

    assert result.source is LoadSource.HEART_RATE
    assert result.zone_seconds.values == (2.0, 2.0, 2.0, 2.0, 2.0)
    assert result.zone_seconds.total == result.used_samples


def test_zones_use_the_same_samples_as_the_load_discarded_ones_are_in_no_zone() -> None:
    # 200 bpm is above the maximum (190) and 40 below the minimum (50): discarded, in no zone
    series = series_at_1_hz(150, 200, 150, 40, 150, 150, 150, 150, 150, 150)

    result = compute_activity_load(Sport.RUNNING, 10.0, series, PHYSIOLOGY)

    assert result.discarded_samples == 2
    assert result.zone_seconds.values == (0.0, 0.0, 8.0, 0.0, 0.0)  # 150 bpm = 88% of 170: Z3
    assert result.zone_seconds.total == result.used_samples


def test_150_bpm_with_threshold_170_is_zone_three() -> None:
    result = compute_activity_load(Sport.RUNNING, 3.0, series_at_1_hz(150, 150, 150), PHYSIOLOGY)

    assert result.zone_seconds.values == (0.0, 0.0, 3.0, 0.0, 0.0)


def test_gaps_over_ten_seconds_are_in_no_zone() -> None:
    seconds = np.array([0.0, 1.0, 2.0, 30.0, 31.0, 32.0, 33.0, 34.0, 35.0, 36.0])
    series = HeartRateSeries(seconds, np.full(10, 180.0))

    result = compute_activity_load(Sport.RUNNING, 10.0, series, PHYSIOLOGY)

    # the sample at 2 s is worth up to 30 s (28 s): interval excluded from load and zones
    assert result.excluded_seconds == 28.0
    assert result.zone_seconds.values == (0.0, 0.0, 0.0, 0.0, 9.0)


def test_the_cycling_zones_use_the_other_sports_lt2_like_every_non_running_sport() -> None:
    """T-CALC-06: the bike has no threshold of its own: the other-sports LT2, else the running."""
    series = series_at_1_hz(*[172.0] * 10)  # 103.6% of 166, 101.2% of 170
    other_166 = replace(PHYSIOLOGY, other_threshold=ThresholdHeartRate(166))

    bike = compute_activity_load(Sport.CYCLING, 10.0, series, other_166)
    run = compute_activity_load(Sport.RUNNING, 10.0, series, other_166)
    bike_default = compute_activity_load(Sport.CYCLING, 10.0, series, PHYSIOLOGY)

    assert bike.zone_seconds.values == (0.0, 0.0, 0.0, 0.0, 10.0)  # Z5: from 103% of its LT2 166
    assert run.zone_seconds.values == (0.0, 0.0, 0.0, 10.0, 0.0)  # 172/170 = 101.2%: Z4
    assert bike_default.zone_seconds == run.zone_seconds  # no value entered: the running LT2


def test_an_activity_without_heart_rate_has_no_zone_seconds() -> None:
    result = compute_activity_load(Sport.RUNNING, 3600.0, None, PHYSIOLOGY)

    assert result.source is LoadSource.ESTIMATE
    assert result.zone_seconds == ZoneSeconds.ZERO


def test_an_estimate_from_mostly_missing_samples_keeps_the_measured_zone_seconds() -> None:
    series = series_at_1_hz(150, 150, 150)  # 3 s measured out of 100 s moving

    result = compute_activity_load(Sport.RUNNING, 100.0, series, PHYSIOLOGY)

    assert result.source is LoadSource.ESTIMATE
    assert result.zone_seconds.values == (0.0, 0.0, 3.0, 0.0, 0.0)


def test_zone_seconds_must_be_five_finite_non_negative_numbers() -> None:
    with pytest.raises(ValueError, match="needed"):
        ZoneSeconds((1.0, 2.0))  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="non-negative"):
        ZoneSeconds((1.0, -0.5, 0.0, 0.0, 0.0))
    with pytest.raises(ValueError, match="finite"):
        ZoneSeconds((1.0, float("nan"), 0.0, 0.0, 0.0))


def _naive_zone(bpm: int, threshold: int) -> int:
    """One zone at a time with integers only: the first percentage exceeded by the sample."""
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

    result = compute_activity_load(Sport.RUNNING, float(len(bpm)), series, PHYSIOLOGY)

    assert result.zone_seconds.total == pytest.approx(result.used_samples)


def test_new_zone_percentages_change_the_seconds_and_the_fingerprint_but_not_the_load(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The zones only classify the seconds: the TRIMPeq stays as it was, while the
    fingerprint of the saved loads changes, so the cache recomputes the seconds per zone."""
    series = series_at_1_hz(100, 127, 128, 144, 145, 161, 162, 175, 176, 190)
    current = compute_activity_load(Sport.RUNNING, 10.0, series, PHYSIOLOGY)
    current_print = load_fingerprint(PHYSIOLOGY)

    old_percent = (85, 90, 95, 100)
    monkeypatch.setattr(zones_module, "ZONE_STARTS_PERCENT", old_percent)
    monkeypatch.setattr(parameters_module, "ZONE_STARTS_PERCENT", old_percent)
    previous = compute_activity_load(Sport.RUNNING, 10.0, series, PHYSIOLOGY)
    previous_print = load_fingerprint(PHYSIOLOGY)

    assert current.trimp_eq == previous.trimp_eq
    assert current.zone_seconds != previous.zone_seconds
    assert current_print != previous_print


# --- bpm of each zone ---------------------------------------------------------------------


def test_the_bpm_of_each_zone_start_at_the_whole_bpm_that_reaches_the_boundary() -> None:
    # Boundaries 127.5, 144.5, 161.5, 175.1: Z2 starts at 128, not at 127 (127 is still Z1).
    assert zone_bpm_ranges(170, 50, 190) == (
        (50, 127),
        (128, 144),
        (145, 161),
        (162, 175),
        (176, 190),
    )


def test_a_boundary_that_is_a_whole_bpm_belongs_to_the_higher_zone() -> None:
    # Threshold 160: Z2 starts at exactly 120, Z3 at 136, Z4 at 152, Z5 at 164.8 -> 165.
    ranges = zone_bpm_ranges(160, 45, 185)

    assert ranges[1] is not None
    assert ranges[1][0] == 120
    assert zones_of(119, 120, threshold=160) == [0, 1]
    assert ranges[2] == (136, 151)
    assert ranges[3] == (152, 164)


def test_z1_starts_at_the_minimum_and_z5_ends_at_the_maximum() -> None:
    ranges = zone_bpm_ranges(160, 42, 181)

    assert ranges[0] is not None
    assert ranges[0][0] == 42
    assert ranges[4] is not None
    assert ranges[4][1] == 181


def test_the_ranges_agree_with_the_zone_of_every_whole_bpm() -> None:
    for threshold in (120, 151, 170, 199):
        ranges = zone_bpm_ranges(threshold, 40, 220)
        for bpm in range(40, 221):
            zone = zones_of(float(bpm), threshold=threshold)[0]
            span = ranges[zone]
            assert span is not None
            assert span[0] <= bpm <= span[1]


def test_a_zone_with_no_whole_bpm_inside_the_limits_is_none() -> None:
    # A threshold above the maximum HR leaves Z5 (and part of Z4) out of reach.
    ranges = zone_bpm_ranges(240, 50, 190)

    assert ranges[4] is None
    assert ranges[0] == (50, 179)
