# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""T-NUT-01 (expenditure, deficit, macros by hand) and T-NUT-02 (deficit safety limits)."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from ponos_coach.load.domain import Sex
from ponos_coach.nutrition.domain import (
    ActivityLevel,
    Advisory,
    DayInput,
    DayLoadLevel,
    MaintenanceGoal,
    MealTimes,
    NutritionGoal,
    PerformanceGoal,
    Person,
    TrainingSession,
    WeightLossGoal,
    basal_metabolic_rate,
    classify_day_load,
    day_macros,
    energy_target,
    plan_day,
    requested_deficit_kcal,
)
from tests.nutrition.strategies import days, goals, persons

REST = DayLoadLevel.REST
MAN = Person(Sex.MALE, 30, 180, 80.0, ActivityLevel.ACTIVE)  # basal 1780, expenditure 2492
BASAL = 1780.0
TIMES = MealTimes.default()


def day_of(person: Person, goal: NutritionGoal, *sessions: TrainingSession) -> DayInput:
    return DayInput(person, goal, TIMES, tuple(sessions))


# --- T-NUT-01: expenditure, deficit and macros ----------------------------------------------


def test_expenditure_is_basal_times_the_level_factor_on_a_rest_day() -> None:
    result = plan_day(day_of(MAN, MaintenanceGoal()))

    assert (result.basal_kcal, result.expenditure_kcal, result.target_kcal) == (1780, 2492, 2492)
    assert result.level is DayLoadLevel.REST


@pytest.mark.parametrize(
    ("level", "expected"),
    [
        (ActivityLevel.SEDENTARY, 2136),  # 1780 * 1.2
        (ActivityLevel.LIGHT, 2314),  # 1780 * 1.3
        (ActivityLevel.ACTIVE, 2492),  # 1780 * 1.4
        (ActivityLevel.VERY_ACTIVE, 2670),  # 1780 * 1.5
        (None, 2136),  # not set: sedentary, with an advisory
    ],
)
def test_every_activity_level_changes_the_expenditure(
    level: ActivityLevel | None, expected: int
) -> None:
    person = Person(Sex.MALE, 30, 180, 80.0, level)

    assert plan_day(day_of(person, MaintenanceGoal())).expenditure_kcal == expected


def test_net_activity_energy_is_added_to_the_expenditure() -> None:
    session = TrainingSession(18 * 60, 60.0, 500.0, 50.0)

    result = plan_day(day_of(MAN, MaintenanceGoal(), session))

    assert result.expenditure_kcal == 2992  # 2492 + 500


def test_a_missing_activity_level_is_assumed_sedentary_and_said_so() -> None:
    person = Person(Sex.MALE, 30, 180, 80.0)

    result = plan_day(day_of(person, MaintenanceGoal()))

    assert Advisory.ACTIVITY_LEVEL_ASSUMED in result.advisories


def test_weight_loss_of_500_g_a_week_is_a_550_kcal_deficit() -> None:
    assert requested_deficit_kcal(WeightLossGoal(500)) == pytest.approx(550.0)
    session = TrainingSession(18 * 60, 60.0, 500.0, 50.0)  # expenditure 2992: 20% is 598

    result = plan_day(day_of(MAN, WeightLossGoal(500), session))

    assert (result.deficit_kcal, result.target_kcal) == (550, 2442)
    assert Advisory.DEFICIT_LIMITED not in result.advisories


def test_on_a_rest_day_the_same_request_meets_the_20_percent_cap() -> None:
    result = plan_day(day_of(MAN, WeightLossGoal(500)))  # expenditure 2492: 20% is 498.4

    assert (result.deficit_kcal, result.target_kcal) == (498, 1994)
    assert Advisory.DEFICIT_LIMITED in result.advisories


def test_macros_of_a_rest_day_by_goal() -> None:
    # protein 1.6 g/kg (2.0 in deficit); carbohydrates 3-5 g/kg at rest: low end for weight loss,
    # middle for maintenance, high end for performance; fat is the rest.
    maintenance = plan_day(day_of(MAN, MaintenanceGoal())).macros
    performance = plan_day(day_of(MAN, PerformanceGoal())).macros
    loss = plan_day(day_of(MAN, WeightLossGoal(500))).macros

    # maintenance: 2492 kcal; carbohydrates 4 g/kg = 320; fat (2492 - 4 * 448) / 9 = 77.8 -> 78
    assert (maintenance.carbs_g, maintenance.protein_g, maintenance.fat_g) == (320, 128, 78)
    # performance: 5 g/kg = 400 g would need more energy than the fat floor leaves: the room is
    # (2492 - 4 * 128 - 9 * 55.38) / 4 = 370.4 -> 370; fat (2492 - 4 * 498) / 9 = 55.6 -> 56
    assert (performance.carbs_g, performance.protein_g, performance.fat_g) == (370, 128, 56)
    # loss: target 1994 (deficit capped at 498); protein 2 g/kg = 160; the room for carbohydrates
    # is (1994 - 640 - 9 * 44.3) / 4 = 238.8 -> 238; fat (1994 - 4 * 398) / 9 = 44.7 -> 45
    assert (loss.carbs_g, loss.protein_g, loss.fat_g) == (238, 160, 45)


@pytest.mark.parametrize(
    ("load", "minutes", "expected"),
    [
        (0.0, 0.0, DayLoadLevel.REST),
        (1.0, 20.0, DayLoadLevel.LIGHT),
        (59.9, 59.9, DayLoadLevel.LIGHT),  # just under both thresholds
        (60.0, 20.0, DayLoadLevel.MODERATE),  # on the load threshold
        (10.0, 60.0, DayLoadLevel.MODERATE),  # on the time threshold
        (119.9, 119.9, DayLoadLevel.MODERATE),
        (120.0, 30.0, DayLoadLevel.HIGH),
        (10.0, 120.0, DayLoadLevel.HIGH),
        (199.9, 239.9, DayLoadLevel.HIGH),
        (200.0, 30.0, DayLoadLevel.VERY_HIGH),
        (10.0, 240.0, DayLoadLevel.VERY_HIGH),
    ],
)
def test_the_day_level_is_the_higher_of_load_and_time(
    load: float, minutes: float, expected: DayLoadLevel
) -> None:
    assert classify_day_load(load, minutes) is expected


@pytest.mark.parametrize(
    ("load", "minutes"), [(-1.0, 10.0), (10.0, -1.0), (float("nan"), 10.0), (10.0, float("inf"))]
)
def test_a_negative_or_non_finite_load_is_refused(load: float, minutes: float) -> None:
    with pytest.raises(ValueError, match="finite"):
        classify_day_load(load, minutes)


@pytest.mark.parametrize(
    ("level", "band"),
    [
        (DayLoadLevel.REST, (3.0, 5.0)),
        (DayLoadLevel.LIGHT, (3.0, 5.0)),
        (DayLoadLevel.MODERATE, (5.0, 7.0)),
        (DayLoadLevel.HIGH, (6.0, 10.0)),
        (DayLoadLevel.VERY_HIGH, (8.0, 12.0)),
    ],
)
def test_carbohydrate_bands_by_level_in_g_per_kg(
    level: DayLoadLevel, band: tuple[float, float]
) -> None:
    assert level.carbs_g_per_kg == band


@pytest.mark.parametrize(
    ("minutes", "load", "expected_carbs"),
    [
        (90.0, 60.0, 480),  # moderate: 5-7 g/kg, middle 6 g/kg x 80 kg
        (150.0, 100.0, 640),  # high: 6-10 g/kg, middle 8 g/kg
        (250.0, 100.0, 800),  # very high: 8-12 g/kg, middle 10 g/kg
    ],
)
def test_carbohydrates_follow_the_day_level(
    minutes: float, load: float, expected_carbs: int
) -> None:
    session = TrainingSession(18 * 60, minutes, 2500.0, load)  # enough net energy for the carbs
    person = Person(Sex.MALE, 30, 180, 80.0, ActivityLevel.VERY_ACTIVE)

    result = plan_day(day_of(person, MaintenanceGoal(), session))

    assert result.macros.carbs_g == expected_carbs


# --- T-NUT-02: safety limits -----------------------------------------------------------------


def target_for(
    person: Person, goal: NutritionGoal, level: DayLoadLevel = REST, net: float = 0.0
) -> tuple[int, int, int, tuple[Advisory, ...]]:
    target = energy_target(person, goal, level, BASAL, net)
    return target.expenditure_kcal, target.deficit_kcal, target.target_kcal, target.advisories


def test_the_deficit_is_capped_at_20_percent_of_the_expenditure() -> None:
    # 20% of 2492 is 498.4 kcal, below the 1100 kcal that 1 kg/week asks
    expenditure, deficit, _, advisories = target_for(MAN, WeightLossGoal(1000))

    assert (expenditure, deficit) == (2492, 498)
    assert Advisory.DEFICIT_LIMITED in advisories


def test_the_deficit_is_capped_at_750_kcal() -> None:
    expenditure, deficit, target, advisories = target_for(MAN, WeightLossGoal(1000), net=3508.0)

    assert (expenditure, deficit, target) == (6000, 750, 5250)  # 20% would be 1200
    assert advisories == (Advisory.DEFICIT_LIMITED,)


def test_the_target_never_goes_below_the_basal_rate() -> None:
    # Sedentary: expenditure 2136, only 356 above the basal rate; 20% would allow 427.
    person = Person(Sex.MALE, 30, 180, 80.0, ActivityLevel.SEDENTARY)

    _, deficit, target, advisories = target_for(person, WeightLossGoal(1000))

    assert (deficit, target) == (356, 1780)
    assert Advisory.DEFICIT_LIMITED in advisories


def test_a_woman_is_never_taken_below_1200_kcal_when_her_basal_rate_is_lower() -> None:
    # basal 10 * 50 + 6.25 * 160 - 5 * 30 - 161 = 1189 kcal; sedentary 1426.8 -> 1427; the cap
    # would allow 285, but only 1427 - 1200 = 227 keeps her at the absolute minimum
    woman = Person(Sex.FEMALE, 30, 160, 50.0, ActivityLevel.SEDENTARY)

    target = energy_target(woman, WeightLossGoal(1000), REST, basal_metabolic_rate(woman), 0.0)

    assert (target.basal_kcal, target.expenditure_kcal) == (1189, 1427)
    assert (target.deficit_kcal, target.target_kcal) == (227, 1200)
    assert Advisory.DEFICIT_LIMITED in target.advisories


def test_a_man_is_never_taken_below_1500_kcal_when_his_basal_rate_is_lower() -> None:
    # basal 600 + 1031.25 - 150 + 5 = 1486.25; sedentary 1783.5 -> 1784; 1784 - 1500 = 284
    man = Person(Sex.MALE, 30, 165, 60.0, ActivityLevel.SEDENTARY)

    target = energy_target(man, WeightLossGoal(1000), REST, basal_metabolic_rate(man), 0.0)

    assert (target.basal_kcal, target.expenditure_kcal) == (1486, 1784)
    assert (target.deficit_kcal, target.target_kcal) == (284, 1500)
    assert Advisory.DEFICIT_LIMITED in target.advisories


def test_no_deficit_at_all_when_the_expenditure_is_already_under_the_absolute_minimum() -> None:
    # basal 500 + 1000 - 300 + 5 = 1205; sedentary 1446 kcal, under the 1500 of a man
    small_man = Person(Sex.MALE, 60, 160, 50.0, ActivityLevel.SEDENTARY)

    target = energy_target(
        small_man, WeightLossGoal(500), REST, basal_metabolic_rate(small_man), 0.0
    )

    assert (target.expenditure_kcal, target.deficit_kcal, target.target_kcal) == (1446, 0, 1446)
    assert target.advisories == (Advisory.DEFICIT_LIMITED,)


def test_a_heavy_day_halves_the_deficit_and_a_very_heavy_day_quarters_it() -> None:
    _, high, _, high_advisories = target_for(MAN, WeightLossGoal(500), DayLoadLevel.HIGH)
    _, very_high, _, _ = target_for(MAN, WeightLossGoal(500), DayLoadLevel.VERY_HIGH)
    _, moderate, _, moderate_advisories = target_for(
        MAN, WeightLossGoal(500), DayLoadLevel.MODERATE
    )

    # the 20% cap (498.4) comes first: 498, half of it 249, a quarter of it 124
    assert (moderate, high, very_high) == (498, 249, 124)
    assert Advisory.DEFICIT_REDUCED_FOR_LOAD in high_advisories
    assert Advisory.DEFICIT_REDUCED_FOR_LOAD not in moderate_advisories


@pytest.mark.parametrize("age", [5, 10, 17])
def test_no_deficit_under_18_and_the_user_is_told(age: int) -> None:
    person = Person(Sex.MALE, age, 175, 65.0, ActivityLevel.ACTIVE)

    _, deficit, target, advisories = target_for(person, WeightLossGoal(500))

    assert deficit == 0
    assert target == round(BASAL * 1.4)
    assert advisories == (Advisory.UNDER_18, Advisory.DEFICIT_NOT_APPLIED)


def test_a_deficit_is_allowed_from_the_age_of_18() -> None:
    person = Person(Sex.MALE, 18, 175, 65.0, ActivityLevel.ACTIVE)

    _, deficit, _, advisories = target_for(person, WeightLossGoal(500))

    assert deficit > 0
    assert Advisory.UNDER_18 not in advisories


def test_no_deficit_with_a_body_mass_index_under_18_5_and_the_user_is_told() -> None:
    person = Person(Sex.FEMALE, 30, 180, 55.0, ActivityLevel.ACTIVE)  # BMI 16.98

    _, deficit, _, advisories = target_for(person, WeightLossGoal(500))

    assert deficit == 0
    assert advisories == (Advisory.LOW_BMI, Advisory.DEFICIT_NOT_APPLIED)


def test_a_body_mass_index_of_exactly_18_5_allows_the_deficit() -> None:
    person = Person(Sex.MALE, 30, 200, 74.0, ActivityLevel.ACTIVE)  # BMI 18.5

    _, deficit, _, advisories = target_for(person, WeightLossGoal(500))

    assert deficit > 0
    assert Advisory.LOW_BMI not in advisories


def test_a_minor_with_another_goal_still_gets_the_advisory() -> None:
    person = Person(Sex.MALE, 15, 170, 60.0, ActivityLevel.ACTIVE)

    _, deficit, _, advisories = target_for(person, PerformanceGoal())

    assert deficit == 0
    assert advisories == (Advisory.UNDER_18,)


def test_when_the_energy_is_short_carbohydrates_give_way_and_it_is_said() -> None:
    # A 20 kg child on a very heavy day: the 8-12 g/kg band wants 200 g of carbohydrates, more
    # than the energy leaves after protein and the fat floor.
    child = Person(Sex.MALE, 6, 110, 20.0, ActivityLevel.SEDENTARY)
    target = energy_target(child, PerformanceGoal(), DayLoadLevel.VERY_HIGH, 1000.0, 0.0)

    macros, advisories = day_macros(child, PerformanceGoal(), DayLoadLevel.VERY_HIGH, target)

    assert advisories == (Advisory.CARBS_LIMITED_BY_ENERGY,)
    assert macros.fat_g * 9 >= 0.2 * target.target_kcal


@given(persons, goals, st.sampled_from(DayLoadLevel), st.floats(0.0, 5000.0))
def test_the_deficit_always_respects_every_safety_limit(
    person: Person, goal: NutritionGoal, level: DayLoadLevel, net: float
) -> None:
    target = energy_target(person, goal, level, basal_metabolic_rate(person), net)

    assert 0 <= target.deficit_kcal <= 750
    assert target.deficit_kcal <= 0.2 * target.expenditure_kcal
    assert target.target_kcal >= target.basal_kcal
    if target.deficit_kcal > 0:
        minimum = 1500 if person.sex is Sex.MALE else 1200
        assert target.target_kcal >= minimum
    assert target.target_kcal == target.expenditure_kcal - target.deficit_kcal
    if not isinstance(goal, WeightLossGoal):
        assert target.deficit_kcal == 0
    if person.age_years < 18 or person.body_mass_index < 18.5:
        assert target.deficit_kcal == 0
        if isinstance(goal, WeightLossGoal):
            assert Advisory.DEFICIT_NOT_APPLIED in target.advisories


@given(days)
def test_the_macros_of_any_day_cover_the_target_with_a_fat_floor(day: DayInput) -> None:
    result = plan_day(day)

    assert min(result.macros.carbs_g, result.macros.protein_g, result.macros.fat_g) >= 0
    assert result.macros.fat_g * 9 >= 0.2 * result.target_kcal
    assert abs(result.macros.kcal - result.target_kcal) <= 20  # whole-gram rounding
    assert result.target_kcal >= result.basal_kcal


@given(days)
def test_advice_for_a_day_is_deterministic(day: DayInput) -> None:
    assert plan_day(day) == plan_day(day)
