# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""T-NUT-01 (basal metabolic rate) and T-NUT-03 (energy of an activity), hand-calculated values."""

from __future__ import annotations

import math

import numpy as np
import pytest

from ponos_coach.activities.domain import Sport
from ponos_coach.load.domain import Sex
from ponos_coach.nutrition.domain import (
    ActivityLevel,
    EnergySource,
    Person,
    activity_energy,
    average_valid_bpm,
    basal_metabolic_rate,
    keytel_kcal_per_minute,
    met_kcal,
)

MAN = Person(Sex.MALE, 30, 180, 80.0)
WOMAN = Person(Sex.FEMALE, 30, 165, 60.0)
HOUR_S = 3600.0
MAN_BASAL_PER_HOUR = 1780.0 / 24.0


@pytest.mark.parametrize(
    ("person", "expected"),
    [
        (MAN, 1780.0),  # 10*80 + 6.25*180 - 5*30 + 5
        (WOMAN, 1320.25),  # 10*60 + 6.25*165 - 5*30 - 161
        (Person(Sex.FEMALE, 17, 150, 45.0), 1141.5),  # a minor
        (Person(Sex.MALE, 65, 170, 70.0), 1442.5),  # an older man
        (Person(Sex.MALE, 5, 100, 20.0), 805.0),  # lowest age, height and weight
        (Person(Sex.FEMALE, 100, 250, 300.0), 3901.5),  # highest height and weight
    ],
)
def test_basal_metabolic_rate_matches_the_hand_calculation(person: Person, expected: float) -> None:
    assert basal_metabolic_rate(person) == pytest.approx(expected)


def test_a_man_burns_166_kcal_more_than_a_woman_of_the_same_build() -> None:
    same_build = Person(Sex.FEMALE, 30, 180, 80.0)

    assert basal_metabolic_rate(MAN) - basal_metabolic_rate(same_build) == pytest.approx(166.0)


def test_activity_levels_have_the_factors_without_sport() -> None:
    # the classic 1.375 / 1.55 / 1.725 already include exercise, which the app adds on its own
    assert [level.factor for level in ActivityLevel] == [1.2, 1.3, 1.4, 1.5]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"age_years": -1},
        {"age_years": 131},
        {"height_cm": 99},
        {"height_cm": 251},
        {"weight_kg": 19.9},
        {"weight_kg": 300.1},
        {"weight_kg": math.nan},
    ],
)
def test_a_person_outside_the_plausible_ranges_is_refused(kwargs: dict[str, float]) -> None:
    values = {"sex": Sex.MALE, "age_years": 30, "height_cm": 180, "weight_kg": 80.0} | kwargs

    with pytest.raises(ValueError, match="range"):
        Person(**values)  # type: ignore[arg-type]


def test_body_mass_index_is_weight_over_height_squared() -> None:
    assert Person(Sex.MALE, 30, 200, 74.0).body_mass_index == pytest.approx(18.5)


# --- Activities: T-NUT-03 -----------------------------------------------------------------


def test_the_device_total_is_used_and_the_resting_part_removed() -> None:
    energy = activity_energy(MAN, Sport.RUNNING, HOUR_S, 600.0, average_bpm=150.0)

    assert energy.source is EnergySource.DECLARED
    assert energy.total_kcal == 600.0
    assert energy.net_kcal == pytest.approx(600.0 - MAN_BASAL_PER_HOUR)


def test_without_a_device_total_keytel_estimates_from_the_average_heart_rate() -> None:
    energy = activity_energy(MAN, Sport.RUNNING, HOUR_S, None, average_bpm=140.0)

    # (-55.0969 + 0.6309*140 + 0.1988*80 + 0.2017*30) kJ/min / 4.184 * 60 min
    assert energy.source is EnergySource.HEART_RATE
    assert energy.total_kcal == pytest.approx(791.36, abs=0.01)
    assert energy.net_kcal == pytest.approx(791.36 - MAN_BASAL_PER_HOUR, abs=0.01)


def test_keytel_for_a_woman_uses_the_female_coefficients() -> None:
    # (-20.4022 + 0.4472*140 - 0.1263*60 + 0.074*30) kJ/min / 4.184
    assert keytel_kcal_per_minute(WOMAN, 140.0) == pytest.approx(8.8068, abs=1e-4)


def test_keytel_never_goes_below_zero() -> None:
    assert keytel_kcal_per_minute(WOMAN, 40.0) == 0.0


@pytest.mark.parametrize("average_bpm", [None, 39.9, 250.1, math.nan])
def test_without_a_usable_heart_rate_the_sport_mets_are_used(average_bpm: float | None) -> None:
    energy = activity_energy(MAN, Sport.RUNNING, HOUR_S, None, average_bpm)

    assert energy.source is EnergySource.MET
    assert energy.total_kcal == pytest.approx(9.8 * 80.0)  # 9.8 METs x 80 kg x 1 h
    assert energy.net_kcal == pytest.approx(784.0 - MAN_BASAL_PER_HOUR)


@pytest.mark.parametrize("average_bpm", [40.0, 250.0])
def test_the_limits_of_the_usable_heart_rate_are_accepted(average_bpm: float) -> None:
    energy = activity_energy(MAN, Sport.RUNNING, HOUR_S, None, average_bpm)

    assert energy.source is EnergySource.HEART_RATE


@pytest.mark.parametrize(
    ("sport", "mets"),
    [
        (Sport.RUNNING, 9.8),
        (Sport.TRAIL, 9.0),
        (Sport.CYCLING, 8.0),
        (Sport.HIKING, 6.0),
        (Sport.WALKING, 3.5),
        (Sport.STRENGTH, 3.5),
        (Sport.OTHER, 4.0),
    ],
)
def test_every_sport_has_its_mets(sport: Sport, mets: float) -> None:
    assert met_kcal(sport, 70.0, 7200.0) == pytest.approx(mets * 70.0 * 2.0)


def test_the_energy_of_a_short_low_effort_is_all_resting_and_net_is_zero_not_negative() -> None:
    energy = activity_energy(MAN, Sport.WALKING, HOUR_S, 10.0, None)

    assert energy.net_kcal == 0.0


def test_net_energy_scales_with_the_duration_of_the_resting_part() -> None:
    one_hour = activity_energy(MAN, Sport.RUNNING, HOUR_S, 900.0, None)
    two_hours = activity_energy(MAN, Sport.RUNNING, 2 * HOUR_S, 900.0, None)

    assert one_hour.net_kcal - two_hours.net_kcal == pytest.approx(MAN_BASAL_PER_HOUR)


@pytest.mark.parametrize("duration", [0.0, -1.0, math.inf, math.nan])
def test_an_invalid_duration_is_refused(duration: float) -> None:
    with pytest.raises(ValueError, match="duration"):
        activity_energy(MAN, Sport.RUNNING, duration, None, None)


@pytest.mark.parametrize("declared", [0.0, -5.0, math.inf, math.nan])
def test_invalid_declared_calories_are_refused(declared: float) -> None:
    with pytest.raises(ValueError, match="calories"):
        activity_energy(MAN, Sport.RUNNING, HOUR_S, declared, None)


def test_the_average_heart_rate_only_counts_samples_inside_the_limits() -> None:
    bpm = np.array([30.0, 100.0, 140.0, 260.0])

    assert average_valid_bpm(bpm, 40, 250) == pytest.approx(120.0)


def test_there_is_no_average_heart_rate_when_every_sample_is_out_of_range() -> None:
    assert average_valid_bpm(np.array([10.0, 300.0]), 40, 250) is None


# --- Declared calories must be credible for the time (1,500 kcal an hour at most) ------------


def test_declared_calories_at_the_hourly_ceiling_are_still_taken() -> None:
    # 30 minutes at the ceiling: 1,500 / 2 = 750 kcal
    result = activity_energy(MAN, Sport.RUNNING, 1800.0, 750.0, None)

    assert result.source is EnergySource.DECLARED
    assert result.total_kcal == 750.0


def test_declared_calories_above_the_hourly_ceiling_are_discarded_and_estimated() -> None:
    # 751 kcal in 30 minutes is just over the ceiling: the METs estimate is used instead
    result = activity_energy(MAN, Sport.RUNNING, 1800.0, 751.0, None)

    assert result.source is EnergySource.MET
    assert result.total_kcal == pytest.approx(9.8 * 80.0 * 0.5)  # 392 kcal


def test_discarded_calories_leave_room_for_the_heart_rate_estimate() -> None:
    result = activity_energy(MAN, Sport.RUNNING, 1200.0, 50_000.0, 150.0)

    assert result.source is EnergySource.HEART_RATE
    assert result.total_kcal < 1500.0 / 3.0  # under the ceiling for 20 minutes


@pytest.mark.parametrize("declared", [0.0, -5.0, float("nan"), float("inf")])
def test_declared_calories_that_are_not_positive_numbers_are_still_an_error(
    declared: float,
) -> None:
    with pytest.raises(ValueError, match="declared"):
        activity_energy(MAN, Sport.RUNNING, 1800.0, declared, None)
