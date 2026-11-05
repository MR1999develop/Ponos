# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Tipi di dominio del carico: validi per costruzione."""

from __future__ import annotations

import numpy as np
import pytest

from ponos_coach.load.domain import (
    HeartRateLimits,
    HeartRateSeries,
    Physiology,
    Sex,
    Sport,
    SportWeight,
    ThresholdHeartRate,
    default_weight,
    threshold_for_sport,
    typical_intensity,
)


@pytest.mark.parametrize(("low", "high"), [(50, 190), (20, 21), (249, 250)])
def test_limits_accept_valid_ranges(low: int, high: int) -> None:
    assert HeartRateLimits(low, high).reserve_bpm == high - low


@pytest.mark.parametrize(("low", "high"), [(19, 190), (50, 50), (90, 80), (50, 251)])
def test_limits_reject_invalid_ranges(low: int, high: int) -> None:
    with pytest.raises(ValueError, match="FC"):
        HeartRateLimits(low, high)


@pytest.mark.parametrize("bpm", [39, 251, 0, -1])
def test_threshold_rejects_out_of_range(bpm: int) -> None:
    with pytest.raises(ValueError, match="soglia"):
        ThresholdHeartRate(bpm)


def test_physiology_requires_threshold_inside_limits() -> None:
    limits = HeartRateLimits(50, 190)
    with pytest.raises(ValueError, match="soglia"):
        Physiology(limits, ThresholdHeartRate(190 + 1), Sex.MALE)
    # La soglia in bici (soglia - 4) deve restare sopra la FC minima.
    with pytest.raises(ValueError, match="soglia"):
        Physiology(limits, ThresholdHeartRate(54), Sex.FEMALE)
    assert Physiology(limits, ThresholdHeartRate(55), Sex.FEMALE).sex is Sex.FEMALE


def test_banister_coefficients() -> None:
    assert Sex.MALE.banister_k == 1.92
    assert Sex.FEMALE.banister_k == 1.67


def test_threshold_for_sport_only_cycling_is_lower() -> None:
    threshold = ThresholdHeartRate(170)
    assert threshold_for_sport(threshold, Sport.CYCLING) == 166
    assert [threshold_for_sport(threshold, s) for s in Sport if s is not Sport.CYCLING] == [170] * (
        len(Sport) - 1
    )


def test_default_weights_match_plan() -> None:
    expected = {
        Sport.RUNNING: 1.0,
        Sport.TRAIL: 1.1,
        Sport.CYCLING: 0.8,
        Sport.HIKING: 0.8,
        Sport.WALKING: 0.6,
        Sport.STRENGTH: 1.2,
        Sport.OTHER: 0.8,
    }
    assert {s: default_weight(s).value for s in Sport} == expected


def test_typical_intensity_is_defined_and_below_threshold_for_every_sport() -> None:
    assert all(0.0 < typical_intensity(s) <= 1.0 for s in Sport)


@pytest.mark.parametrize("value", [-0.1, 5.1, float("nan"), float("inf")])
def test_weight_rejects_invalid_values(value: float) -> None:
    with pytest.raises(ValueError, match="peso"):
        SportWeight(value)


def test_weight_zero_is_valid() -> None:
    assert SportWeight(0.0).value == 0.0


def test_series_accepts_valid_data_and_is_read_only() -> None:
    series = HeartRateSeries(np.array([0.0, 1.0, 2.5]), np.array([100.0, 110.0, 120.0]))
    assert len(series) == 3
    with pytest.raises(ValueError, match="read-only"):
        series.bpm[0] = 1.0


def test_series_copies_its_input() -> None:
    seconds = np.array([0.0, 1.0])
    series = HeartRateSeries(seconds, np.array([100.0, 100.0]))
    seconds[1] = 99.0
    assert series.seconds[1] == 1.0


@pytest.mark.parametrize(
    ("seconds", "bpm"),
    [
        ([], []),
        ([0.0, 1.0], [100.0]),
        ([0.0, 0.0], [100.0, 100.0]),
        ([1.0, 0.5], [100.0, 100.0]),
        ([0.0, float("nan")], [100.0, 100.0]),
        ([0.0, 1.0], [100.0, float("inf")]),
    ],
)
def test_series_rejects_invalid_columns(seconds: list[float], bpm: list[float]) -> None:
    with pytest.raises(ValueError, match=r"serie|secondi|finiti"):
        HeartRateSeries(np.array(seconds), np.array(bpm))
