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
MAN = Person(Sex.MALE, 30, 180, 80.0, ActivityLevel.ACTIVE)  # basal 1780, expenditure 2759
BASAL = 1780.0
TIMES = MealTimes.default()


def day_of(person: Person, goal: NutritionGoal, *sessions: TrainingSession) -> DayInput:
    return DayInput(person, goal, TIMES, tuple(sessions))


# --- T-NUT-01: expenditure, deficit and macros ----------------------------------------------


def test_expenditure_is_basal_times_the_level_factor_on_a_rest_day() -> None:
    result = plan_day(day_of(MAN, MaintenanceGoal()))

    assert (result.basal_kcal, result.expenditure_kcal, result.target_kcal) == (1780, 2759, 2759)
    assert result.level is DayLoadLevel.REST


@pytest.mark.parametrize(
    ("level", "expected"),
    [
        (ActivityLevel.SEDENTARY, 2136),  # 1780 * 1.2
        (ActivityLevel.LIGHT, 2448),  # 1780 * 1.375 = 2447.5, rounded to even
        (ActivityLevel.ACTIVE, 2759),  # 1780 * 1.55
        (ActivityLevel.VERY_ACTIVE, 3070),  # 1780 * 1.725 = 3070.5, rounded to even
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

    assert result.expenditure_kcal == 3259  # 2759 + 500


def test_a_missing_activity_level_is_assumed_sedentary_and_said_so() -> None:
    person = Person(Sex.MALE, 30, 180, 80.0)

    result = plan_day(day_of(person, MaintenanceGoal()))

    assert Advisory.ACTIVITY_LEVEL_ASSUMED in result.advisories


def test_weight_loss_of_500_g_a_week_is_a_550_kcal_deficit() -> None:
    assert requested_deficit_kcal(WeightLossGoal(500)) == pytest.approx(550.0)

    result = plan_day(day_of(MAN, WeightLossGoal(500)))

    assert (result.deficit_kcal, result.target_kcal) == (550, 2209)
    assert result.advisories == ()


def test_macros_of_a_rest_day_by_goal() -> None:
    # protein 1.6 g/kg (2.0 in deficit); carbohydrates 3-5 g/kg at rest: low end for weight loss,
    # middle for maintenance, high end for performance; fat is the rest.
    maintenance = plan_day(day_of(MAN, MaintenanceGoal())).macros
    performance = plan_day(day_of(MAN, PerformanceGoal())).macros
    loss = plan_day(day_of(MAN, WeightLossGoal(500))).macros

    assert (maintenance.carbs_g, maintenance.protein_g, maintenance.fat_g) == (320, 128, 107)
    assert (performance.carbs_g, performance.protein_g, performance.fat_g) == (400, 128, 72)
    assert (loss.carbs_g, loss.protein_g, loss.fat_g) == (240, 160, 68)


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
    # 20% of 2759 is 551.8 kcal, below the 1100 kcal that 1 kg/week asks
    expenditure, deficit, _, advisories = target_for(MAN, WeightLossGoal(1000))

    assert (expenditure, deficit) == (2759, 551)
    assert Advisory.DEFICIT_LIMITED in advisories


def test_the_deficit_is_capped_at_750_kcal() -> None:
    expenditure, deficit, target, advisories = target_for(MAN, WeightLossGoal(1000), net=3241.0)

    assert (expenditure, deficit, target) == (6000, 750, 5250)  # 20% would be 1200
    assert advisories == (Advisory.DEFICIT_LIMITED,)


def test_the_target_never_goes_below_the_basal_rate() -> None:
    # Sedentary: expenditure 2136, only 356 above the basal rate; 20% would allow 427.
    person = Person(Sex.MALE, 30, 180, 80.0, ActivityLevel.SEDENTARY)

    _, deficit, target, advisories = target_for(person, WeightLossGoal(1000))

    assert (deficit, target) == (356, 1780)
    assert Advisory.DEFICIT_LIMITED in advisories


def test_a_heavy_day_halves_the_deficit_and_a_very_heavy_day_quarters_it() -> None:
    _, high, _, high_advisories = target_for(MAN, WeightLossGoal(500), DayLoadLevel.HIGH)
    _, very_high, _, _ = target_for(MAN, WeightLossGoal(500), DayLoadLevel.VERY_HIGH)
    _, moderate, _, moderate_advisories = target_for(
        MAN, WeightLossGoal(500), DayLoadLevel.MODERATE
    )

    assert (moderate, high, very_high) == (550, 275, 137)
    assert Advisory.DEFICIT_REDUCED_FOR_LOAD in high_advisories
    assert Advisory.DEFICIT_REDUCED_FOR_LOAD not in moderate_advisories


@pytest.mark.parametrize("age", [5, 10, 17])
def test_no_deficit_under_18_and_the_user_is_told(age: int) -> None:
    person = Person(Sex.MALE, age, 175, 65.0, ActivityLevel.ACTIVE)

    _, deficit, target, advisories = target_for(person, WeightLossGoal(500))

    assert deficit == 0
    assert target == round(BASAL * 1.55)
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
