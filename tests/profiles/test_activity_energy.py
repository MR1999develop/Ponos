# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""`GetActivityEnergy`: the active calories of the detail are those of the day's nutrition."""

from __future__ import annotations

import pytest

from ponos_coach.load.activity_detail import ActivityDetail, GetActivityDetail
from ponos_coach.nutrition.domain import ActivityEnergy, EnergySource
from ponos_coach.profiles.day_nutrition import GetActivityEnergy, GetRecordEnergy, NutritionSources
from tests.profiles.test_day_nutrition import ROME, Rig, at, decode_fit, fit_bytes

BASAL_KCAL_PER_DAY = 1780.0  # male, 180 cm, 30 y, 80 kg (Mifflin-St Jeor)


def energy_of_first(rig: Rig) -> ActivityEnergy | None:
    p = rig.profile
    record = p.activities.list_started_between(at(1, day=1), at(1, day=4))[0]
    detail = GetActivityDetail(p.activities, rig.archive, p.physiology, decode_fit, timezone=ROME)(
        record.identity
    )
    assert isinstance(detail, ActivityDetail)
    sources = NutritionSources(
        p.activities, rig.archive, p.load_cache, p.physiology,
        p.weights, p.fixed_data, p.activity_levels, p.meal_times, p.goal_periods,
        p.lean_masses,
    )  # fmt: skip
    return GetActivityEnergy(sources)(detail)


def test_the_active_calories_are_the_declared_total_minus_the_resting_part_of_the_duration() -> (
    None
):
    rig = Rig()
    rig.add_file(fit_bytes(at(18), 1, calories=700))

    energy = energy_of_first(rig)

    assert energy is not None
    assert energy.source is EnergySource.DECLARED
    assert energy.net_kcal == pytest.approx(700.0 - BASAL_KCAL_PER_DAY / 24.0)


@pytest.mark.parametrize("calories", [700, None])
def test_the_detail_has_the_same_energy_as_the_nutrition_of_the_day(calories: int | None) -> None:
    rig = Rig()
    rig.add_file(fit_bytes(at(18), 1, bpm=140, calories=calories))

    energy = energy_of_first(rig)

    (activity,) = rig.report().activities
    assert energy == activity.energy


def test_without_a_declared_total_the_energy_is_an_estimate_from_the_heart_rate() -> None:
    rig = Rig()
    rig.add_file(fit_bytes(at(18), 1, bpm=140))

    energy = energy_of_first(rig)

    assert energy is not None
    assert energy.source is EnergySource.HEART_RATE


@pytest.mark.parametrize("rig", [Rig(body=False), Rig(weight=False)])
def test_without_the_body_data_of_the_day_there_is_no_active_energy(rig: Rig) -> None:
    rig.add_file(fit_bytes(at(18), 1, calories=700))

    assert energy_of_first(rig) is None


def record_energy(rig: Rig) -> ActivityEnergy | None:
    p = rig.profile
    record = p.activities.list_started_between(at(1, day=1), at(1, day=4))[0]
    sources = NutritionSources(
        p.activities, rig.archive, p.load_cache, p.physiology,
        p.weights, p.fixed_data, p.activity_levels, p.meal_times, p.goal_periods,
        p.lean_masses,
    )  # fmt: skip
    return GetRecordEnergy(sources, decode_fit, ROME)(record.identity)


@pytest.mark.parametrize("calories", [700, None])
def test_the_energy_of_a_record_is_the_one_of_the_day_and_of_the_detail(
    calories: int | None,
) -> None:
    rig = Rig()
    rig.add_file(fit_bytes(at(18), 1, bpm=140, calories=calories))

    of_record = record_energy(rig)

    (activity,) = rig.report().activities
    assert of_record == activity.energy
    assert of_record == energy_of_first(rig)


def test_a_credible_declared_total_in_the_index_needs_no_file() -> None:
    rig = Rig()
    rig.add_file(fit_bytes(at(18), 1, calories=700))
    reads = rig.archive.reads

    energy = record_energy(rig)

    assert energy is not None
    assert energy.source is EnergySource.DECLARED
    assert rig.archive.reads == reads


@pytest.mark.parametrize("rig", [Rig(body=False), Rig(weight=False)])
def test_without_the_body_data_a_record_has_no_energy(rig: Rig) -> None:
    rig.add_file(fit_bytes(at(18), 1, calories=700))

    assert record_energy(rig) is None
