# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Independent acceptance of the nutrition model of phase 4b (T-NUT-01...05).

The expected values are worked out by hand from the published sources (Mifflin-St Jeor 1990,
Keytel 2005, Compendium METs, ISSN/ACSM/IOC bands) and written as literals, with the arithmetic in
a comment. The people are invented. The invariants of `check_day` hold for every day, whoever the
person is: no negative amount, the meals add up to the day, the safety limits are never passed,
the advisories are there when they must be.

Defects found while testing were kept as strict xfail until fixed; none is left.
"""

from __future__ import annotations

from datetime import time

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from ponos_coach.activities.domain import Sport
from ponos_coach.load.domain import Sex
from ponos_coach.nutrition.domain import (
    ActivityLevel,
    Advisory,
    DayInput,
    DayLoadLevel,
    DayNutrition,
    EnergySource,
    MaintenanceGoal,
    MealSlot,
    MealTimes,
    NutritionGoal,
    PerformanceGoal,
    Person,
    TrainingSession,
    WeightLossGoal,
    activity_energy,
    basal_metabolic_rate,
    defaults,
    energy_target,
    keytel_kcal_per_minute,
    plan_day,
)

TIMES = MealTimes.default()
# Kcal by which the macros of a day may differ from its energy target: every gram is a whole
# number (carbohydrates and protein round down: up to 4 kcal each; fat rounds to the nearest: 4.5).
KCAL_ROUNDING_TOLERANCE = 13
MAX_HOURLY_CARBS_G = 90  # IOC: the upper end of the range during very long efforts
MAX_HOURLY_FLUID_ML = 800  # ACSM: 0.4 to 0.8 L per hour
MAX_FAT_FREE_PROTEIN_G_PER_KG = 2.0  # ISSN: 1.4 to 2.0 g/kg (2.2 for short deficits)
CARBS_BAND_HIGH_G_PER_KG = 12.0  # the top of the very high band


def man(age: int, cm: int, kg: float, level: ActivityLevel | None = None) -> Person:
    return Person(Sex.MALE, age, cm, kg, level)


def woman(age: int, cm: int, kg: float, level: ActivityLevel | None = None) -> Person:
    return Person(Sex.FEMALE, age, cm, kg, level)


def day_of(
    person: Person,
    goal: NutritionGoal = MaintenanceGoal(),  # noqa: B008 - an immutable value
    sessions: tuple[TrainingSession, ...] = (),
    next_gap: float | None = None,
    times: MealTimes = TIMES,
) -> DayInput:
    return DayInput(person, goal, times, sessions, next_gap)


def run(
    start: int, minutes: float, net_kcal: float | None = None, load: float | None = None
) -> TrainingSession:
    """A session; without explicit energy and load, plausible ones for its duration."""
    return TrainingSession(
        start,
        minutes,
        7.0 * minutes if net_kcal is None else net_kcal,
        minutes * 1.2 if load is None else load,
    )


# --- Invariants of every day ---------------------------------------------------------------


def check_day(day: DayInput, result: DayNutrition) -> None:
    """Everything that must be true of the guidance for any day (T-NUT-02, T-NUT-04)."""
    person, macros = day.person, result.macros
    # no negative quantity anywhere
    for item in result.meals:
        assert min(item.macros.carbs_g, item.macros.protein_g, item.macros.fat_g) >= 0
    assert min(macros.carbs_g, macros.protein_g, macros.fat_g) >= 0
    assert min(result.basal_kcal, result.expenditure_kcal, result.deficit_kcal) >= 0
    # the items add up to the day, gram by gram
    assert sum(i.macros.carbs_g for i in result.meals) == macros.carbs_g
    assert sum(i.macros.protein_g for i in result.meals) == macros.protein_g
    assert sum(i.macros.fat_g for i in result.meals) == macros.fat_g
    # the day's grams carry the energy of the target, up to rounding; fat is at least 20%
    assert abs(macros.kcal - result.target_kcal) <= KCAL_ROUNDING_TOLERANCE
    assert 9 * macros.fat_g >= 0.2 * result.target_kcal - 1e-6
    # safety limits of the deficit
    assert result.target_kcal == result.expenditure_kcal - result.deficit_kcal
    assert result.target_kcal >= result.basal_kcal
    assert result.deficit_kcal <= 0.2 * result.expenditure_kcal + 1e-9
    assert result.deficit_kcal <= 750
    if not isinstance(day.goal, WeightLossGoal):
        assert result.deficit_kcal == 0
    minor = person.age_years < 18
    thin = person.body_mass_index < 18.5
    if minor or thin:
        assert result.deficit_kcal == 0
    if isinstance(day.goal, WeightLossGoal) and (minor or thin):
        assert Advisory.DEFICIT_NOT_APPLIED in result.advisories
    # the advisories that must be there, and only those
    assert (Advisory.UNDER_18 in result.advisories) is minor
    assert (Advisory.LOW_BMI in result.advisories) is thin
    assert (Advisory.ACTIVITY_LEVEL_ASSUMED in result.advisories) is (person.activity_level is None)
    assert len(set(result.advisories)) == len(result.advisories)
    # nutrient ceilings
    assert macros.carbs_g <= CARBS_BAND_HIGH_G_PER_KG * person.weight_kg
    assert macros.protein_g <= MAX_FAT_FREE_PROTEIN_G_PER_KG * person.weight_kg + 1
    # the items: in order, inside the day, each regular meal exactly once
    minutes = [i.minute for i in result.meals]
    assert minutes == sorted(minutes)
    assert all(0 <= m <= 1439 for m in minutes)
    slots = [slot for i in result.meals for slot in i.slots]
    for regular in (MealSlot.BREAKFAST, MealSlot.LUNCH, MealSlot.DINNER):
        assert slots.count(regular) == 1
    # the guidance during a training stays inside the published ranges
    for item in result.meals:
        if item.hourly is not None:
            assert 0 < item.hourly.carbs_g <= MAX_HOURLY_CARBS_G
            assert 0 < item.hourly.fluid_ml <= MAX_HOURLY_FLUID_ML
            assert item.hourly.sodium_mg > 0
    during = [s for s in day.sessions if s.duration_min > 75]
    assert sum(MealSlot.DURING_TRAINING in i.slots for i in result.meals) == len(during)
    # one recovery per session: a single dose or, with another session within 8 hours, up to 4
    # hourly intakes, each marked with its hour
    recoveries = sum(MealSlot.POST_TRAINING in i.slots for i in result.meals)
    long_enough = sum(s.duration_min >= 30 for s in day.sessions)
    assert (recoveries >= 1) is (long_enough >= 1)
    assert recoveries <= long_enough * defaults.REFUEL_HOURS
    assert all(1 <= i.refuel_hour <= defaults.REFUEL_HOURS for i in result.meals if i.refuel_hour)
    # determinism
    assert plan_day(day) == result


# --- T-NUT-01: basal metabolic rate, by hand -----------------------------------------------


@pytest.mark.parametrize(
    ("person", "expected"),
    [
        # 10 kg + 6.25 cm - 5 age + 5 (man) or - 161 (woman)
        (man(17, 180, 70.0), 1745.0),  # 700 + 1125 - 85 + 5
        (woman(17, 160, 45.0), 1204.0),  # 450 + 1000 - 85 - 161 (BMI 17.6)
        (man(90, 175, 70.0), 1348.75),  # 700 + 1093.75 - 450 + 5
        (woman(90, 151, 50.0), 832.75),  # 500 + 943.75 - 450 - 161
        (man(40, 175, 122.5), 2123.75),  # 1225 + 1093.75 - 200 + 5 (BMI 40.0)
        (woman(30, 171, 49.7), 1254.75),  # 497 + 1068.75 - 150 - 161 (BMI 17.0)
        (woman(130, 100, 20.0), 14.0),  # 200 + 625 - 650 - 161: the limits of the person
    ],
)
def test_t_nut_01_basal_rate_of_extreme_profiles_matches_the_hand_calculation(
    person: Person, expected: float
) -> None:
    assert basal_metabolic_rate(person) == pytest.approx(expected, abs=1e-9)


def test_t_nut_01_the_body_mass_index_of_the_extreme_profiles() -> None:
    assert man(40, 175, 122.5).body_mass_index == pytest.approx(40.0)
    assert woman(30, 171, 49.7).body_mass_index == pytest.approx(16.996, abs=1e-3)
    assert woman(17, 160, 45.0).body_mass_index == pytest.approx(17.578, abs=1e-3)


# Woman of 30, 165 cm, 60 kg: basal 600 + 1031.25 - 150 - 161 = 1320.25.
@pytest.mark.parametrize(
    ("level", "expenditure"),
    [
        (ActivityLevel.SEDENTARY, 1584),  # 1320.25 x 1.2 = 1584.3
        (ActivityLevel.LIGHT, 1716),  # x 1.3 = 1716.33
        (ActivityLevel.ACTIVE, 1848),  # x 1.4 = 1848.35
        (ActivityLevel.VERY_ACTIVE, 1980),  # x 1.5 = 1980.38
    ],
)
def test_t_nut_01_expenditure_of_a_rest_day_by_activity_level(
    level: ActivityLevel, expenditure: int
) -> None:
    person = woman(30, 165, 60.0, level)

    result = plan_day(day_of(person))

    assert (result.basal_kcal, result.expenditure_kcal) == (1320, expenditure)
    assert (result.deficit_kcal, result.target_kcal) == (0, expenditure)
    assert result.level is DayLoadLevel.REST


def test_t_nut_01_the_energy_of_a_day_with_an_activity_does_not_count_rest_twice() -> None:
    # man 35 y, 180 cm, 70 kg, active: basal 700 + 1125 - 175 + 5 = 1655; x 1.4 = 2317.
    # A 60 minute run of 700 kcal (device) nets 700 - 1655 / 24 = 631.04 -> 2948.04 -> 2948
    person = man(35, 180, 70.0, ActivityLevel.ACTIVE)
    net = 700.0 - 1655.0 / 24.0

    result = plan_day(day_of(person, sessions=(run(18 * 60, 60.0, net, 90.0),)))

    assert result.expenditure_kcal == 2948
    assert result.level is DayLoadLevel.MODERATE  # 60 min


# --- T-NUT-01: goals and macros, by hand ---------------------------------------------------


def test_t_nut_01_macros_of_a_rest_day_for_each_goal() -> None:
    # man 35 y, 180 cm, 70 kg, active: expenditure 2317; rest day: carbohydrates 3-5 g/kg
    person = man(35, 180, 70.0, ActivityLevel.ACTIVE)

    maintenance = plan_day(day_of(person))
    performance = plan_day(day_of(person, PerformanceGoal()))
    loss = plan_day(day_of(person, WeightLossGoal(500)))

    # maintenance: carbs 4 g/kg = 280, protein 1.6 g/kg = 112, fat (2317 - 1568) / 9 = 83.2
    assert (maintenance.macros.carbs_g, maintenance.macros.protein_g) == (280, 112)
    assert maintenance.macros.fat_g == 83
    # performance: carbs 5 g/kg = 350, protein 112, fat (2317 - 1848) / 9 = 52.1
    assert (performance.macros.carbs_g, performance.macros.protein_g) == (350, 112)
    assert performance.macros.fat_g == 52
    # loss 500 g/week: asks 550 kcal, but 20% of 2317 = 463.4 -> 463; carbs 3 g/kg = 210, protein
    # 2.0 g/kg = 140, fat (1854 - 1400) / 9 = 50.4
    assert (loss.deficit_kcal, loss.target_kcal) == (463, 1854)
    assert (loss.macros.carbs_g, loss.macros.protein_g, loss.macros.fat_g) == (210, 140, 50)
    assert loss.advisories == (Advisory.DEFICIT_LIMITED,)
    assert maintenance.advisories == ()


def test_t_nut_01_a_low_energy_target_makes_carbohydrates_give_way_and_says_so() -> None:
    # woman 30 y, 165 cm, 60 kg, light, loss 500 g: expenditure 1716, 20% = 343.2 -> 343, target
    # 1373 (basal 1320 is not reached). Protein 120 g, fat floor 0.2 x 1373 / 9 = 30.5 g -> 31;
    # carbs (1373 - 480 - 274.6) / 4 = 154.6 -> 154 of the 180 g wanted; fat 277 / 9 = 30.8
    person = woman(30, 165, 60.0, ActivityLevel.LIGHT)

    result = plan_day(day_of(person, WeightLossGoal(500)))

    assert (result.deficit_kcal, result.target_kcal) == (343, 1373)
    assert (result.macros.carbs_g, result.macros.protein_g, result.macros.fat_g) == (154, 120, 31)
    assert result.advisories == (
        Advisory.DEFICIT_LIMITED,
        Advisory.CARBS_LIMITED_BY_ENERGY,
    )


# --- T-NUT-02: the safety limits at their edges --------------------------------------------


def test_t_nut_02_the_deficit_never_takes_the_target_below_the_basal_rate() -> None:
    # sedentary woman of 100 kg, 160 cm, 30 y: basal 1000 + 1000 - 150 - 161 = 1689, expenditure
    # 2026.8 -> 2027, only 338 above the basal; 20% would be 405. Her energy availability limit
    # is looser: Boer 25.2 + 75.68 - 48.3 = 52.58 kg, 30 x 52.58 = 1577.4, so 449 would be allowed.
    person = woman(30, 160, 100.0, ActivityLevel.SEDENTARY)

    result = plan_day(day_of(person, WeightLossGoal(1000)))

    assert (result.deficit_kcal, result.target_kcal) == (338, 1689)
    assert result.target_kcal == result.basal_kcal
    assert Advisory.DEFICIT_LIMITED in result.advisories


def test_t_nut_02_the_energy_availability_can_be_stricter_than_the_basal_rate() -> None:
    # sedentary woman of 60 kg, 165 cm: expenditure 1584 is 264 above the basal 1320, but Boer
    # gives 15.12 + 78.045 - 48.3 = 44.865 kg and 30 x 44.865 = 1345.95 kcal: 1584 - 1345.95 =
    # 238.05 -> 238 (target 1346, availability 30.002)
    person = woman(30, 165, 60.0, ActivityLevel.SEDENTARY)

    result = plan_day(day_of(person, WeightLossGoal(1000)))

    assert (result.deficit_kcal, result.target_kcal) == (238, 1346)
    assert Advisory.DEFICIT_LIMITED in result.advisories


def test_t_nut_02_the_deficit_is_capped_at_750_kcal_on_a_very_large_expenditure() -> None:
    # man 30 y, 190 cm, 110 kg, very active: basal 1100 + 1187.5 - 150 + 5 = 2142.5 x 1.5 =
    # 3213.75; with 1000 kcal of net activity 4214. 20% is 842.8 but the cap is 750 (1% of 110 kg
    # allows 1100 g a week; the availability limit allows 3213.75 - 30 x 80.0 = 800)
    person = man(30, 190, 110.0, ActivityLevel.VERY_ACTIVE)

    target = energy_target(
        person, WeightLossGoal(1000), DayLoadLevel.MODERATE, basal_metabolic_rate(person), 1000.0
    )

    assert (target.expenditure_kcal, target.deficit_kcal) == (4214, 750)
    assert target.target_kcal == 3464
    assert Advisory.DEFICIT_LIMITED in target.advisories


def test_t_nut_02_the_cap_is_exactly_one_fifth_of_the_expenditure_at_every_kcal() -> None:
    """No floating point rounding costs (or gives) a kcal at the 20% edge."""
    # very active, 70 kg, 180 cm, 35 y: basal 700 + 1125 - 175 + 5 = 1655, x 1.5 = 2482.5; the
    # availability limit (2482.5 - 30 x 57.35 = 762) and the 1% pace (700 g = 770 kcal) stay above
    # the 20% cap and the 750 cap over the whole range, so the fifth is the limit
    person = man(35, 180, 70.0, ActivityLevel.VERY_ACTIVE)

    wrong = []
    for expenditure in range(2500, 3750):
        target = energy_target(
            person,
            WeightLossGoal(1000),
            DayLoadLevel.REST,
            basal_metabolic_rate(person),
            expenditure - 2482.5,
        )
        assert target.expenditure_kcal == expenditure
        if target.deficit_kcal != expenditure // 5:
            wrong.append(expenditure)
    assert wrong == []


@pytest.mark.parametrize(
    ("level", "kept"),
    [
        (DayLoadLevel.REST, 550),
        (DayLoadLevel.LIGHT, 550),
        (DayLoadLevel.MODERATE, 550),
        (DayLoadLevel.HIGH, 275),  # 550 x 0.5
        (DayLoadLevel.VERY_HIGH, 137),  # 550 x 0.25 = 137.5, never rounded up
    ],
)
def test_t_nut_02_a_heavy_day_keeps_a_smaller_share_of_the_deficit(
    level: DayLoadLevel, kept: int
) -> None:
    # very active, 70 kg, 180 cm, 35 y: basal 1655 x 1.5 = 2482.5; with 3000 net expenditure
    # 5482.5 -> 5482; 500 g a week is 550 kcal, under 20% (1096), 750 and the availability
    # limit (2482.5 - 30 x 57.35 = 762)
    person = man(35, 180, 70.0, ActivityLevel.VERY_ACTIVE)
    target = energy_target(person, WeightLossGoal(500), level, basal_metabolic_rate(person), 3000.0)

    assert target.deficit_kcal == kept
    assert (Advisory.DEFICIT_REDUCED_FOR_LOAD in target.advisories) is (kept < 550)


@pytest.mark.parametrize("age", [0, 5, 16, 17])
def test_t_nut_02_no_deficit_under_18_even_with_the_strongest_request(age: int) -> None:
    person = man(age, 180, 70.0, ActivityLevel.LIGHT)

    result = plan_day(day_of(person, WeightLossGoal(1000), (run(7 * 60, 120.0),)))

    assert result.deficit_kcal == 0
    assert result.target_kcal == result.expenditure_kcal
    assert {Advisory.UNDER_18, Advisory.DEFICIT_NOT_APPLIED} <= set(result.advisories)


def test_t_nut_02_at_exactly_18_the_deficit_is_allowed() -> None:
    result = plan_day(day_of(man(18, 180, 70.0, ActivityLevel.LIGHT), WeightLossGoal(500)))

    assert result.deficit_kcal > 0
    assert Advisory.UNDER_18 not in result.advisories


def test_t_nut_02_a_body_mass_index_of_17_gets_no_deficit_and_the_advisory() -> None:
    result = plan_day(day_of(woman(30, 171, 49.7, ActivityLevel.ACTIVE), WeightLossGoal(500)))

    assert result.deficit_kcal == 0
    assert set(result.advisories) >= {Advisory.LOW_BMI, Advisory.DEFICIT_NOT_APPLIED}


def test_t_nut_02_a_body_mass_index_of_40_still_gets_a_limited_deficit() -> None:
    # man 40 y, 175 cm, 122.5 kg, light: basal 2124, expenditure 2123.75 x 1.3 = 2760.9 -> 2761;
    # Boer 49.8575 + 46.725 - 19.2 = 77.3825 kg, 30 x 77.3825 = 2321.5 kcal at availability 30:
    # 2761 - 2321.5 = 439.5 -> 439 (the 20% of 552 is looser) -> target 2322; protein 2 g/kg =
    # 245 g and the fat floor leave 219 g of the 367 g of carbohydrates a loss day would want:
    # (2322 - 980 - 464.4) / 4 = 219.4
    person = man(40, 175, 122.5, ActivityLevel.LIGHT)

    result = plan_day(day_of(person, WeightLossGoal(1000)))

    assert (result.expenditure_kcal, result.deficit_kcal, result.target_kcal) == (2761, 439, 2322)
    assert result.advisories == (Advisory.DEFICIT_LIMITED, Advisory.CARBS_LIMITED_BY_ENERGY)
    assert result.macros.carbs_g == 219


def test_t_nut_02_a_minor_and_a_thin_adult_are_warned_even_on_maintenance() -> None:
    assert Advisory.UNDER_18 in plan_day(day_of(man(15, 175, 60.0))).advisories
    assert Advisory.LOW_BMI in plan_day(day_of(woman(30, 171, 49.7))).advisories


def test_t_nut_02_a_missing_activity_level_is_assumed_sedentary_and_said_so() -> None:
    result = plan_day(day_of(woman(30, 165, 60.0)))

    assert result.expenditure_kcal == 1584
    # carbs 4 g/kg = 240 g do not fit: protein 96 g and the fat floor leave 220 g
    assert result.advisories == (
        Advisory.ACTIVITY_LEVEL_ASSUMED,
        Advisory.CARBS_LIMITED_BY_ENERGY,
    )


# --- T-NUT-03: energy of an activity, by hand ----------------------------------------------


@pytest.mark.parametrize(
    ("person", "bpm", "minutes", "expected"),
    [
        # kJ/min = -55.0969 + 0.6309 HR + 0.1988 kg + 0.2017 age (men), / 4.184
        (man(40, 175, 80.0), 150, 60, 910.76),  # 63.5101 kJ/min = 15.179 kcal/min
        (man(90, 175, 70.0), 100, 60, 574.52),  # 40.0621 kJ/min = 9.575 kcal/min
        # kJ/min = -20.4022 + 0.4472 HR - 0.1263 kg + 0.074 age (women)
        (woman(40, 165, 60.0), 140, 30, 269.51),  # 37.5878 kJ/min = 8.984 kcal/min
        (woman(17, 160, 50.0), 170, 45, 543.83),  # 50.5648 kJ/min = 12.085 kcal/min
    ],
)
def test_t_nut_03_keytel_matches_the_published_equation(
    person: Person, bpm: int, minutes: int, expected: float
) -> None:
    per_minute = keytel_kcal_per_minute(person, float(bpm))

    assert per_minute * minutes == pytest.approx(expected, abs=0.05)
    estimate = activity_energy(person, Sport.RUNNING, minutes * 60.0, None, float(bpm))
    assert estimate.source is EnergySource.HEART_RATE
    assert estimate.total_kcal == pytest.approx(expected, abs=0.05)


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
def test_t_nut_03_without_heart_rate_the_compendium_mets_give_kcal_per_kg_and_hour(
    sport: Sport, mets: float
) -> None:
    person = man(35, 180, 70.0)

    estimate = activity_energy(person, sport, 2 * 3600.0, None, None)

    assert estimate.source is EnergySource.MET
    assert estimate.total_kcal == pytest.approx(mets * 70.0 * 2.0)  # MET x kg x hours


def test_t_nut_03_the_resting_part_is_removed_once_and_never_below_zero() -> None:
    person = man(35, 180, 70.0)  # basal 1655 kcal a day
    # 8 h at trail METs: 9.0 x 70 x 8 = 5040; resting 1655 / 3 = 551.67; net 4488.33
    ultra = activity_energy(person, Sport.TRAIL, 8 * 3600.0, None, None)
    assert ultra.total_kcal == pytest.approx(5040.0)
    assert ultra.net_kcal == pytest.approx(5040.0 - 1655.0 / 3.0)
    # a device total lower than the resting energy of the same time: net is zero, not negative
    quiet = activity_energy(person, Sport.OTHER, 8 * 3600.0, 100.0, None)
    assert quiet.net_kcal == 0.0
    assert quiet.total_kcal == 100.0


@pytest.mark.parametrize("bpm", [None, 39.9, 250.1, 0.0, float("nan")])
def test_t_nut_03_an_unusable_average_heart_rate_falls_back_to_the_mets(
    bpm: float | None,
) -> None:
    estimate = activity_energy(man(35, 180, 70.0), Sport.RUNNING, 3600.0, None, bpm)

    assert estimate.source is EnergySource.MET


@pytest.mark.parametrize("declared", [0.0, -1.0, float("nan"), float("inf")])
def test_t_nut_03_declared_calories_that_are_not_positive_numbers_are_refused(
    declared: float,
) -> None:
    with pytest.raises(ValueError, match="calories"):
        activity_energy(man(35, 180, 70.0), Sport.RUNNING, 3600.0, declared, None)


# --- T-NUT-04: scenarios of the day --------------------------------------------------------

ADULT_MAN = man(35, 180, 70.0, ActivityLevel.ACTIVE)
ADULT_WOMAN = woman(30, 165, 60.0, ActivityLevel.LIGHT)
TEENAGER = woman(17, 160, 45.0, ActivityLevel.ACTIVE)
ELDERLY = man(90, 175, 70.0, ActivityLevel.SEDENTARY)
HEAVY = man(40, 175, 122.5, ActivityLevel.LIGHT)
THIN = woman(30, 171, 49.7, ActivityLevel.ACTIVE)

PEOPLE = [ADULT_MAN, ADULT_WOMAN, TEENAGER, ELDERLY, HEAVY, THIN]
GOALS: list[NutritionGoal] = [
    MaintenanceGoal(),
    PerformanceGoal(),
    WeightLossGoal(100),
    WeightLossGoal(1000),
]
SCENARIOS = {
    "no session": (),
    "one at lunch": (run(12 * 60, 60.0),),
    "dawn before breakfast": (run(4 * 60 + 30, 120.0),),
    "late evening": (run(23 * 60, 90.0),),
    "across midnight": (run(23 * 60 + 30, 180.0),),
    "two sessions": (run(6 * 60, 75.0), run(18 * 60, 100.0)),
    "three sessions": (run(6 * 60, 45.0), run(12 * 60 + 30, 40.0), run(19 * 60, 110.0)),
    "long ride": (run(7 * 60, 240.0, 2600.0, 300.0),),
    "ultra of 8 hours": (run(6 * 60, 480.0, 4500.0, 420.0),),
    "short only": (run(10 * 60, 20.0),),
}


@pytest.mark.parametrize("person", PEOPLE, ids=lambda p: f"{p.sex.value}-{p.age_years}")
@pytest.mark.parametrize("goal", GOALS, ids=lambda g: type(g).__name__)
@pytest.mark.parametrize("scenario", list(SCENARIOS))
def test_t_nut_04_every_scenario_keeps_every_invariant(
    person: Person, goal: NutritionGoal, scenario: str
) -> None:
    day = day_of(person, goal, SCENARIOS[scenario], next_gap=18 * 60.0)

    check_day(day, plan_day(day))


def test_t_nut_04_a_session_at_dawn_moves_breakfast_after_it_and_eats_before() -> None:
    # 05:00-07:00: breakfast (07:30) falls within the hour after the end -> 08:00, merged with
    # the recovery; one hour before (04:00) there is a pre-training intake
    result = plan_day(day_of(ADULT_MAN, sessions=(run(5 * 60, 120.0),)))

    by_slot = {slot: item for item in result.meals for slot in item.slots}
    assert by_slot[MealSlot.PRE_TRAINING].minute == 4 * 60
    assert by_slot[MealSlot.BREAKFAST].moved
    assert by_slot[MealSlot.BREAKFAST].minute >= 8 * 60
    assert MealSlot.POST_TRAINING in by_slot[MealSlot.BREAKFAST].slots


def test_t_nut_04_a_session_at_lunch_moves_lunch_and_leaves_the_other_meals() -> None:
    result = plan_day(day_of(ADULT_MAN, sessions=(run(12 * 60 + 30, 60.0),)))

    by_slot = {slot: item for item in result.meals for slot in item.slots}
    assert by_slot[MealSlot.LUNCH].moved
    assert by_slot[MealSlot.LUNCH].minute >= 14 * 60
    assert not by_slot[MealSlot.BREAKFAST].moved
    assert by_slot[MealSlot.DINNER].minute == 20 * 60


def test_t_nut_04_a_session_at_23_has_its_recovery_inside_the_day_and_dinner_before_it() -> None:
    result = plan_day(day_of(ADULT_MAN, sessions=(run(23 * 60, 90.0),)))

    by_slot = {slot: item for item in result.meals for slot in item.slots}
    assert by_slot[MealSlot.DINNER].minute <= 22 * 60
    assert by_slot[MealSlot.POST_TRAINING].minute == 23 * 60 + 59  # clamped to the day
    assert by_slot[MealSlot.PRE_TRAINING].minute == 21 * 60  # an hour' session needs it: 90 > 60


def test_t_nut_04_two_sessions_a_few_hours_apart_each_get_their_recovery_carbohydrates() -> None:
    # 06:00-07:15 and 13:00-14:40: the next one is 5 h 45 min later (under 8 h), so the first
    # recovery has carbs
    first, second = run(6 * 60, 75.0), run(13 * 60, 100.0)
    result = plan_day(day_of(ADULT_MAN, sessions=(first, second)))

    posts = [i for i in result.meals if MealSlot.POST_TRAINING in i.slots]
    recoveries = [i for i in posts if i.refuel_hour in (None, 1)]
    assert len(recoveries) == 2
    assert all(p.macros.protein_g > 0 for p in recoveries)
    # the first one is hourly (another session within 8 h): 1.1 g/kg x 70 kg = 77 g an hour
    hourly = [i for i in posts if i.refuel_hour]
    assert [i.refuel_hour for i in hourly] == [1, 2, 3, 4]
    assert hourly[0].macros.carbs_g == 77


def test_t_nut_04_three_sessions_with_the_same_start_do_not_break_the_day() -> None:
    sessions = (run(18 * 60, 60.0), run(18 * 60, 60.0), run(18 * 60, 60.0))
    day = day_of(ADULT_MAN, sessions=sessions)

    check_day(day, plan_day(day))


def test_t_nut_04_an_eight_hour_ultra_gets_the_during_box_with_hourly_guidance_and_caps() -> None:
    # 06:00-14:00, carbohydrates on the day sized for performance: pre 1 g/kg at 05:00 (two hours
    # before would be before 05:00 -> one hour), during 60 g/h from 2.5 h on (the ceiling of a
    # single carbohydrate), post after the end
    day = day_of(ADULT_MAN, PerformanceGoal(), (run(6 * 60, 480.0, 4488.33, 420.0),))
    result = plan_day(day)
    check_day(day, result)

    (during,) = (i for i in result.meals if i.hourly is not None)
    assert during.hourly is not None
    assert (during.hourly.carbs_g, during.hourly.fluid_ml, during.hourly.sodium_mg) == (
        60,
        600,
        300,
    )
    assert during.minute == 6 * 60
    assert during.macros.carbs_g == 480  # 60 g/h x 8 h
    by_slot = {slot: item for item in result.meals for slot in item.slots}
    assert by_slot[MealSlot.PRE_TRAINING].minute == 5 * 60
    assert by_slot[MealSlot.PRE_TRAINING].macros.carbs_g == 70
    assert by_slot[MealSlot.POST_TRAINING].minute >= 14 * 60 + 30
    assert result.level is DayLoadLevel.VERY_HIGH


@pytest.mark.parametrize(("minutes", "rate"), [(75.0, None), (75.5, 45), (150.0, 45), (151.0, 60)])
def test_t_nut_04_the_hourly_rate_changes_at_75_minutes_and_at_two_and_a_half_hours(
    minutes: float, rate: int | None
) -> None:
    result = plan_day(day_of(ADULT_MAN, PerformanceGoal(), (run(7 * 60, minutes),)))

    hourly = [i.hourly for i in result.meals if i.hourly is not None]
    assert [h.carbs_g for h in hourly] == ([] if rate is None else [rate])


def test_t_nut_04_a_dinner_with_the_whole_evening_blocked_never_leaves_the_day() -> None:
    # 16:00-23:50: the dinner cannot go after (past 23:00): it goes before the blocked hour
    day = day_of(ADULT_MAN, sessions=(run(16 * 60, 470.0),))
    result = plan_day(day)

    check_day(day, result)
    by_slot = {slot: item for item in result.meals for slot in item.slots}
    assert by_slot[MealSlot.DINNER].minute <= 23 * 60  # moved before the blocked hour, or at 23:00


# --- T-NUT-05: determinism and dates -------------------------------------------------------


def test_t_nut_05_the_order_the_sessions_are_given_in_makes_no_difference() -> None:
    sessions = (run(6 * 60, 75.0), run(12 * 60 + 30, 40.0), run(19 * 60, 110.0))

    forward = plan_day(day_of(ADULT_MAN, sessions=sessions))
    backward = plan_day(day_of(ADULT_MAN, sessions=sessions[::-1]))

    assert forward.meals == backward.meals
    assert forward.macros == backward.macros


def test_t_nut_05_changing_level_weight_and_goal_changes_the_guidance_as_expected() -> None:
    """The same day with a lighter person, a higher level and a loss goal: each effect shows."""
    before = plan_day(day_of(man(30, 180, 80.0, ActivityLevel.ACTIVE), MaintenanceGoal()))
    after = plan_day(day_of(man(30, 180, 75.0, ActivityLevel.VERY_ACTIVE), WeightLossGoal(500)))

    assert (before.basal_kcal, before.expenditure_kcal) == (1780, 2492)  # 1780 x 1.4 = 2492
    # basal 750 + 1125 - 150 + 5 = 1730; x 1.5 = 2595; the 550 asked meet the 20% cap: 519
    assert (after.basal_kcal, after.expenditure_kcal) == (1730, 2595)
    assert (after.deficit_kcal, after.target_kcal) == (519, 2076)
    assert Advisory.DEFICIT_LIMITED in after.advisories


# --- Properties (long campaign) ------------------------------------------------------------

persons = st.builds(
    Person,
    sex=st.sampled_from(Sex),
    age_years=st.integers(0, 130),
    height_cm=st.integers(100, 250),
    weight_kg=st.integers(200, 3000).map(lambda tenths: tenths / 10.0),
    activity_level=st.none() | st.sampled_from(ActivityLevel),
)
goals = st.one_of(
    st.just(MaintenanceGoal()),
    st.just(PerformanceGoal()),
    st.builds(WeightLossGoal, st.integers(100, 1000)),
)
sessions = st.builds(
    TrainingSession,
    start_minute=st.integers(0, 1439),
    duration_min=st.one_of(st.integers(1, 1500), st.floats(1.0, 1500.0)),
    net_kcal=st.integers(0, 20_000).map(float),
    load=st.integers(0, 1500).map(float),
)


@st.composite
def meal_times(draw: st.DrawFn) -> MealTimes:
    breakfast = draw(st.integers(0, 1439 - 240))
    lunch = draw(st.integers(breakfast + 120, 1439 - 120))
    dinner = draw(st.integers(lunch + 120, 1439))
    return MealTimes(
        time(breakfast // 60, breakfast % 60),
        time(lunch // 60, lunch % 60),
        time(dinner // 60, dinner % 60),
    )


@pytest.mark.collaudo
@settings(deadline=None)
@given(
    persons,
    goals,
    st.lists(sessions, max_size=4),
    st.none() | st.integers(0, 4000).map(float),
    meal_times(),
)
def test_t_nut_02_04_every_day_keeps_every_invariant(
    person: Person,
    goal: NutritionGoal,
    drawn: list[TrainingSession],
    next_gap: float | None,
    times: MealTimes,
) -> None:
    day = DayInput(person, goal, times, tuple(drawn), next_gap)

    check_day(day, plan_day(day))


# --- Defects found -------------------------------------------------------------------------


def test_d_f4b_01_an_extreme_but_allowed_person_does_not_crash_the_day() -> None:
    """Woman of 98 y, 100 cm, 20.1 kg (inside the limits of `Person`), loss 100 g a week."""
    person = woman(98, 100, 20.1, ActivityLevel.LIGHT)
    day = day_of(person, WeightLossGoal(100))

    check_day(day, plan_day(day))  # ValueError: macronutrient grams cannot be negative


@pytest.mark.parametrize(
    "goal", [MaintenanceGoal(), WeightLossGoal(500)], ids=lambda g: type(g).__name__
)
def test_d_f4b_03_the_during_box_agrees_with_its_hourly_guidance_in_an_eight_hour_ultra(
    goal: NutritionGoal,
) -> None:
    """The box says 60 g of carbohydrates an hour; for 8 hours the item must carry about 480 g
    (and the dinner after the effort keep some carbohydrates), or the hourly line is wrong."""
    result = plan_day(day_of(ADULT_MAN, goal, (run(6 * 60, 480.0, 4488.33, 420.0),)))

    (during,) = (i for i in result.meals if i.hourly is not None)
    assert during.hourly is not None
    assert during.macros.carbs_g >= 0.9 * during.hourly.carbs_g * 8
    dinner = next(i for i in result.meals if MealSlot.DINNER in i.slots)
    assert dinner.macros.carbs_g >= ADULT_MAN.weight_kg  # at least 1 g/kg after 8 hours
