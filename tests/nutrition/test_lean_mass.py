# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""T-NUT-02 and T-NUT-06: measured lean mass and the long weight-loss advisory."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from hypothesis import given

from ponos_coach.load.domain import Sex
from ponos_coach.nutrition.domain import (
    ActivityLevel,
    Advisory,
    DayInput,
    DayLoadLevel,
    LeanMass,
    LeanMassEntry,
    MaintenanceGoal,
    MealTimes,
    Person,
    WeightLossGoal,
    defaults,
    energy_availability,
    energy_target,
    fat_free_mass_kg,
    is_plausible_lean_mass,
    lean_mass_on,
    plan_day,
)
from tests.nutrition.strategies import days

REST = DayLoadLevel.REST
BASAL = 1780.0
TIMES = MealTimes.default()


def man(lean_mass_kg: float | None = None) -> Person:
    """80 kg, 180 cm, active: basal 1780, expenditure 2492, Boer fat-free mass 61.42 kg."""
    return Person(Sex.MALE, 30, 180, 80.0, ActivityLevel.ACTIVE, lean_mass_kg)


# --- The type ------------------------------------------------------------------------------


@pytest.mark.parametrize("tenths", [150, 151, 612, 1999, 2000])
def test_a_lean_mass_inside_the_limits_is_accepted(tenths: int) -> None:
    assert LeanMass(tenths).tenths_kg == tenths


@pytest.mark.parametrize("tenths", [-1, 0, 149, 2001])
def test_a_lean_mass_outside_15_to_200_kg_is_refused(tenths: int) -> None:
    with pytest.raises(ValueError, match="lean mass"):
        LeanMass(tenths)


def test_from_kg_takes_one_decimal_digit_and_nothing_else() -> None:
    assert LeanMass.from_kg(Decimal("61.4")) == LeanMass(614)
    assert LeanMass.from_kg(Decimal("15")) == LeanMass(150)
    assert LeanMass.from_kg(Decimal("200.0")).kg == 200.0
    for bad in ("61.45", "14.9", "200.1", "NaN", "Infinity", "1E+400"):
        with pytest.raises(ValueError, match="lean mass"):
            LeanMass.from_kg(Decimal(bad))


def test_an_entry_cannot_start_before_1900() -> None:
    with pytest.raises(ValueError, match="1900"):
        LeanMassEntry(date(1899, 12, 31), LeanMass(600))
    assert LeanMassEntry(date(1900, 1, 1), LeanMass(600)).valid_from == date(1900, 1, 1)


# --- Plausibility against the weight (on point and off point) -------------------------------


@pytest.mark.parametrize(
    ("tenths", "plausible"),
    [
        (319, False),  # 31.9 kg is under 40% of 80 kg
        (320, True),  # 32.0 kg: exactly 40%
        (760, True),  # 76.0 kg: exactly 95%
        (761, False),
    ],
)
def test_a_lean_mass_is_plausible_between_40_and_95_percent_of_the_weight(
    tenths: int, plausible: bool
) -> None:
    assert is_plausible_lean_mass(LeanMass(tenths), 80.0) is plausible


def test_a_weight_that_is_not_a_number_makes_nothing_plausible() -> None:
    assert not is_plausible_lean_mass(LeanMass(600), float("nan"))
    assert not is_plausible_lean_mass(LeanMass(600), 0.0)


# --- The series ----------------------------------------------------------------------------


def test_the_measurement_holds_from_its_day_until_the_next_one() -> None:
    entries = [
        LeanMassEntry(date(2026, 2, 1), LeanMass(610)),
        LeanMassEntry(date(2026, 3, 1), LeanMass(620)),
    ]

    assert lean_mass_on(entries, date(2026, 1, 31)) is None  # not before the first measurement
    assert lean_mass_on(entries, date(2026, 2, 1)) == LeanMass(610)
    assert lean_mass_on(entries, date(2026, 2, 28)) == LeanMass(610)
    assert lean_mass_on(entries, date(2026, 3, 1)) == LeanMass(620)
    assert lean_mass_on(entries, date(2030, 1, 1)) == LeanMass(620)
    assert lean_mass_on([], date(2026, 3, 1)) is None


# --- It replaces Boer in the limit on energy availability -----------------------------------


def test_the_person_takes_a_lean_mass_that_is_positive_and_not_above_the_weight() -> None:
    assert man(80.0).lean_mass_kg == 80.0
    for bad in (0.0, -1.0, 80.1, float("nan"), float("inf")):
        with pytest.raises(ValueError, match="lean mass"):
            man(bad)


def test_without_a_measurement_the_fat_free_mass_is_the_boer_estimate() -> None:
    assert fat_free_mass_kg(man()) == pytest.approx(61.42)


def test_a_measurement_replaces_the_boer_estimate() -> None:
    assert fat_free_mass_kg(man(70.0)) == 70.0


def test_the_energy_availability_is_the_net_intake_over_the_fat_free_mass() -> None:
    assert energy_availability(man(), 2500.0, 500.0) == pytest.approx(2000 / 61.42)
    assert energy_availability(man(50.0), 2500.0, 500.0) == pytest.approx(40.0)


def test_a_larger_lean_mass_makes_the_availability_limit_bind_sooner() -> None:
    # Rest day, expenditure 2492, 1000 g a week asked: the 20% cap is 498. With Boer (61.42 kg)
    # the limit allows 2492 - 30 * 61.42 = 649.4; with a measured 70 kg it allows
    # 2492 - 30 * 70 = 392: the deficit is cut to 392 and the advisory says so.
    boer = energy_target(man(), WeightLossGoal(1000), REST, BASAL, 0.0)
    measured = energy_target(man(70.0), WeightLossGoal(1000), REST, BASAL, 0.0)

    assert boer.deficit_kcal == 498
    assert measured.deficit_kcal == 392
    assert Advisory.DEFICIT_LIMITED in measured.advisories
    assert energy_availability(man(70.0), measured.target_kcal, 0.0) >= 30.0


def test_a_smaller_lean_mass_leaves_room_for_the_requested_deficit() -> None:
    measured = energy_target(man(50.0), WeightLossGoal(1000), REST, BASAL, 0.0)

    assert measured.deficit_kcal == 498  # the 20% cap, not the availability, decides


@given(days)
def test_a_deficit_never_leaves_the_availability_under_30_with_or_without_a_measurement(
    day: DayInput,
) -> None:
    result = plan_day(day)

    if result.deficit_kcal > 0:
        net = sum(s.net_kcal for s in day.sessions)
        assert energy_availability(day.person, result.target_kcal, net) >= 30.0 - 1e-9


# --- The advisory on a long weight-loss period ------------------------------------------------


def long_loss(days_in: int | None, person: Person, goal: object = None) -> tuple[Advisory, ...]:
    chosen = WeightLossGoal(1000) if goal is None else goal
    day = DayInput(person, chosen, TIMES, weight_loss_days=days_in)  # type: ignore[arg-type]
    return plan_day(day).advisories


def test_the_default_limit_is_12_weeks() -> None:
    assert defaults.LONG_WEIGHT_LOSS_WEEKS == 12


@pytest.mark.parametrize(("days_in", "advised"), [(0, False), (83, False), (84, False), (85, True)])
def test_a_weight_loss_period_longer_than_12_weeks_gets_the_advisory(
    days_in: int, advised: bool
) -> None:
    # rest day: target 1994, availability 1994 / 61.42 = 32.5 kcal/kg
    assert (Advisory.LONG_WEIGHT_LOSS in long_loss(days_in, man())) is advised


def test_outside_a_weight_loss_period_there_is_no_advisory() -> None:
    assert Advisory.LONG_WEIGHT_LOSS not in long_loss(None, man())
    assert Advisory.LONG_WEIGHT_LOSS not in long_loss(400, man(), MaintenanceGoal())


@pytest.mark.parametrize(("lean_mass_kg", "advised"), [(44.3, False), (44.4, True)])
def test_the_advisory_needs_an_availability_under_45(lean_mass_kg: float, advised: bool) -> None:
    # target 1994 kcal: 1994 / 44.3 = 45.01 (balanced), 1994 / 44.4 = 44.91 (reduced)
    assert (Advisory.LONG_WEIGHT_LOSS in long_loss(100, man(lean_mass_kg))) is advised


def test_without_an_applied_deficit_there_is_nothing_to_pause() -> None:
    minor = Person(Sex.MALE, 17, 180, 80.0, ActivityLevel.ACTIVE)  # no numbers, no deficit

    assert Advisory.LONG_WEIGHT_LOSS not in long_loss(200, minor)


def test_the_advisory_comes_last_in_the_display_order() -> None:
    advisories = list(Advisory)

    assert advisories[-1] is Advisory.LONG_WEIGHT_LOSS


def test_a_negative_duration_is_a_programming_error() -> None:
    with pytest.raises(ValueError, match="negative"):
        DayInput(man(), WeightLossGoal(500), TIMES, weight_loss_days=-1)


@given(days)
def test_the_advisory_needs_a_deficit_and_more_than_12_weeks(day: DayInput) -> None:
    result = plan_day(day)

    if Advisory.LONG_WEIGHT_LOSS in result.advisories:
        assert result.deficit_kcal > 0
        assert day.weight_loss_days is not None
        assert day.weight_loss_days > 12 * 7
