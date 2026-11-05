# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Carico delle attività fixture decodificate: T-CALC-01 e i casi di T-CALC-07."""

from __future__ import annotations

import pytest

from ponos_coach.activities.fit_decoder import decode_activity
from ponos_coach.load.domain import (
    ActivityLoad,
    HeartRateLimits,
    LoadSource,
    Physiology,
    Sex,
    Sport,
    ThresholdHeartRate,
    compute_activity_load,
    default_weight,
)
from tests.load.helpers import FIXTURES, PHYSIOLOGY


def load_of(name: str, physiology: Physiology = PHYSIOLOGY) -> ActivityLoad:
    activity = decode_activity((FIXTURES / "fit" / name).read_bytes())
    return compute_activity_load(
        activity.sport,
        activity.duration_s,
        activity.heart_rate,
        physiology,
        default_weight(activity.sport),
    )


def test_t_calc_01_sixty_minutes_at_threshold_160_is_hrtss_100() -> None:
    physiology = Physiology(HeartRateLimits(50, 190), ThresholdHeartRate(160), Sex.MALE)

    result = load_of("run_constant_160.fit", physiology)

    assert result.hr_tss == pytest.approx(100.0, rel=1e-9)  # tolleranza dichiarata
    assert result.source is LoadSource.HEART_RATE
    assert (result.used_samples, result.discarded_samples) == (3600, 0)


def test_t_calc_07_one_second_activity_has_a_tiny_positive_load() -> None:
    result = load_of("run_one_second.fit")

    assert result.source is LoadSource.HEART_RATE
    assert result.used_samples == 1
    assert 0 < result.hr_tss < 0.1


def test_t_calc_07_twenty_four_hours_hike_is_measured_not_estimated() -> None:
    result = load_of("hike_24h.fit")

    assert result.source is LoadSource.HEART_RATE
    assert result.used_samples == 17280
    assert result.excluded_seconds == 0.0
    assert result.hr_tss > 0


def test_t_calc_07_hr_gaps_over_ten_seconds_are_not_counted() -> None:
    result = load_of("run_hr_gaps.fit")

    # 12 s di FC non valida (intervallo di 13 s) e 15 s senza record (16 s).
    assert result.excluded_seconds == 29.0
    assert result.source is LoadSource.HEART_RATE


def test_t_calc_07_out_of_range_samples_are_discarded_and_counted() -> None:
    result = load_of("run_hr_out_of_range.fit")

    assert result.discarded_samples == 8
    assert result.used_samples == 1800 - 8
    assert result.source is LoadSource.HEART_RATE


def test_t_calc_06_activity_without_heart_rate_is_an_estimate() -> None:
    result = load_of("walk_no_hr.fit")

    assert result.source is LoadSource.ESTIMATE
    assert result.load > 0


def test_flat_heart_rate_and_other_sports_give_positive_loads() -> None:
    for name in ("bike_easy.fit", "strength_low_hr.fit", "run_intervals.fit"):
        assert load_of(name).load > 0


def test_sport_of_the_fixtures_follows_the_fit_session() -> None:
    sports = {
        name: decode_activity((FIXTURES / "fit" / name).read_bytes()).sport
        for name in ("run_intervals.fit", "bike_easy.fit", "strength_low_hr.fit", "walk_no_hr.fit")
    }

    assert sports == {
        "run_intervals.fit": Sport.RUNNING,
        "bike_easy.fit": Sport.CYCLING,
        "strength_low_hr.fit": Sport.STRENGTH,
        "walk_no_hr.fit": Sport.WALKING,
    }
