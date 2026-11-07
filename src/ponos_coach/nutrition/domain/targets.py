# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""The day's energy target (with the deficit and its safety limits) and macro totals.

All amounts are whole kcal or grams and self-consistent: the deficit is computed on the rounded
expenditure and never leaves the target below the rounded basal metabolic rate.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from ponos_coach.load.domain import Sex
from ponos_coach.nutrition.domain import defaults
from ponos_coach.nutrition.domain.advice import Advisory, DayLoadLevel, ordered
from ponos_coach.nutrition.domain.energy import Person
from ponos_coach.nutrition.domain.macros import Macros
from ponos_coach.nutrition.domain.settings import (
    MaintenanceGoal,
    NutritionGoal,
    PerformanceGoal,
    WeightLossGoal,
)


@dataclass(frozen=True, slots=True)
class EnergyTarget:
    """Energy budget of a day, whole kcal: `target = expenditure - deficit`, and with a deficit the
    target stays at or above `max(basal, absolute minimum for the sex)`."""

    basal_kcal: int
    expenditure_kcal: int
    deficit_kcal: int
    target_kcal: int
    advisories: tuple[Advisory, ...]


def permitted_weekly_loss_g(requested_g: float, weight_kg: float) -> float:
    """Weekly loss the model allows: the smallest of the request, the app's maximum pace and 1% of
    the body weight (ACSM: keeps lean mass)."""
    return min(
        requested_g,
        float(defaults.MAX_WEEKLY_LOSS_G),
        defaults.MAX_WEEKLY_LOSS_FRACTION * weight_kg * 1000.0,
    )


def requested_deficit_kcal(goal: WeightLossGoal, weight_kg: float) -> float:
    """Daily deficit that produces the permitted weekly loss (7700 kcal per kg), given the
    day's weight: the goal's pace cut to 1% of the body weight when it is larger."""
    loss_g = permitted_weekly_loss_g(goal.weekly_loss_g, weight_kg)
    return loss_g / 1000.0 * defaults.KCAL_PER_KG_BODY_MASS / defaults.DAYS_PER_WEEK


def estimated_fat_free_mass_kg(person: Person) -> float:
    """Fat-free mass in kg (Boer 1984), within [0.5 x weight, weight]."""
    per_kg, per_cm, constant = (
        defaults.BOER_MALE if person.sex is Sex.MALE else defaults.BOER_FEMALE
    )
    estimate = per_kg * person.weight_kg + per_cm * person.height_cm + constant
    return min(person.weight_kg, max(defaults.MIN_FFM_FRACTION * person.weight_kg, estimate))


def max_deficit_for_energy_availability(
    person: Person, expenditure_kcal: float, net_activity_kcal: float
) -> float:
    """Largest deficit that keeps the energy availability at 30 kcal/kg of fat-free mass or more
    (ACSM); negative when even eating the whole expenditure stays under it."""
    minimum_intake = (
        defaults.MIN_ENERGY_AVAILABILITY_KCAL_PER_KG_FFM * estimated_fat_free_mass_kg(person)
        + net_activity_kcal
    )
    return expenditure_kcal - minimum_intake


def minimum_intake_kcal(person: Person, basal: int) -> int:
    """The least a deficit may leave: the larger of the basal metabolic rate and the absolute
    minimum for the sex (1,200 kcal women, 1,500 kcal men)."""
    absolute = (
        defaults.MIN_INTAKE_KCAL_MALE if person.sex is Sex.MALE else defaults.MIN_INTAKE_KCAL_FEMALE
    )
    return max(basal, absolute)


def _person_advisories(person: Person) -> list[Advisory]:
    found = []
    if person.age_years < defaults.ADULT_AGE_YEARS:
        found.append(Advisory.UNDER_18)
    if person.body_mass_index < defaults.MIN_BMI_FOR_DEFICIT:
        found.append(Advisory.LOW_BMI)
    return found


def _applied_deficit(
    goal: WeightLossGoal,
    person: Person,
    level: DayLoadLevel,
    expenditure: int,
    basal: int,
    net_activity_kcal: float,
) -> tuple[int, list[Advisory]]:
    """Whole-kcal deficit after the safety limits, and the advisories about it."""
    if _person_advisories(person):
        return 0, [Advisory.DEFICIT_NOT_APPLIED]
    requested = requested_deficit_kcal(goal, person.weight_kg)
    cap = min(defaults.MAX_DEFICIT_FRACTION * expenditure, float(defaults.MAX_DEFICIT_KCAL))
    capped = min(requested, cap)
    reduced = capped * level.deficit_factor
    allowed = max(
        0.0,
        min(
            float(expenditure - minimum_intake_kcal(person, basal)),
            max_deficit_for_energy_availability(person, expenditure, net_activity_kcal),
        ),
    )
    applied = math.floor(min(reduced, allowed))
    found = []
    cut_pace = permitted_weekly_loss_g(goal.weekly_loss_g, person.weight_kg) < goal.weekly_loss_g
    if allowed < reduced or capped < requested or cut_pace:
        found.append(Advisory.DEFICIT_LIMITED)
    if reduced < capped:
        found.append(Advisory.DEFICIT_REDUCED_FOR_LOAD)
    return applied, found


def energy_target(
    person: Person,
    goal: NutritionGoal,
    level: DayLoadLevel,
    basal_kcal: float,
    net_activity_kcal: float,
) -> EnergyTarget:
    """Energy expenditure of the day and the target after the goal's deficit.

    Expenditure is the basal rate times the activity-level factor plus the net activity energy.
    Only weight loss has a deficit, and it is limited: a weekly loss of at most 1% of the body
    weight, never above 20% of the expenditure or 750 kcal, never leaving the energy
    availability under 30 kcal per kg of fat-free mass (net activity energy is the exercise
    energy), never leaving the target under the larger of the basal rate and 1,200 kcal (women)
    or 1,500 kcal (men), reduced on heavy days, and none at all under 18 or with a body mass index
    under 18.5.
    """
    advisories = _person_advisories(person)
    if person.activity_level is None:
        advisories.append(Advisory.ACTIVITY_LEVEL_ASSUMED)
        factor = defaults.DEFAULT_ACTIVITY_PAL
    else:
        factor = person.activity_level.factor
    basal = round(basal_kcal)
    expenditure = round(basal_kcal * factor + net_activity_kcal)
    deficit = 0
    if isinstance(goal, WeightLossGoal):
        deficit, found = _applied_deficit(
            goal, person, level, expenditure, basal, net_activity_kcal
        )
        advisories += found
    elif not isinstance(goal, MaintenanceGoal | PerformanceGoal):  # pragma: no cover
        raise TypeError("unknown nutrition goal")
    return EnergyTarget(basal, expenditure, deficit, expenditure - deficit, ordered(advisories))


def day_macros(
    person: Person,
    goal: NutritionGoal,
    level: DayLoadLevel,
    target: EnergyTarget,
) -> tuple[Macros, tuple[Advisory, ...]]:
    """Carbohydrate, protein and fat grams of the day, covering the target energy.

    Protein is 1.6 g/kg (2.0 in deficit). Carbohydrates follow the day's load band: the low end
    for weight loss, the middle for maintenance, the high end for performance. Fat is the rest,
    never below 20% of the target energy; if the energy does not allow the carbohydrates, they
    give way (advisory). Grams are whole, never negative.
    """
    kcal = float(target.target_kcal)
    fat_min_g = defaults.FAT_MIN_FRACTION * kcal / defaults.KCAL_PER_G_FAT
    per_kg = (
        defaults.PROTEIN_G_PER_KG_DEFICIT if target.deficit_kcal > 0 else defaults.PROTEIN_G_PER_KG
    )
    protein_cap = max(
        0.0, (kcal - defaults.KCAL_PER_G_FAT * fat_min_g) / defaults.KCAL_PER_G_PROTEIN
    )
    protein = min(per_kg * person.weight_kg, protein_cap)
    low, high = level.carbs_g_per_kg
    if isinstance(goal, WeightLossGoal):
        band = low
    elif isinstance(goal, PerformanceGoal):
        band = high
    else:
        band = (low + high) / 2.0
    wanted = band * person.weight_kg
    room = max(
        0.0,
        (kcal - defaults.KCAL_PER_G_PROTEIN * protein - defaults.KCAL_PER_G_FAT * fat_min_g)
        / defaults.KCAL_PER_G_CARBS,
    )  # never below zero: float rounding at the protein cap can leave a tiny negative number
    carbs = min(wanted, room)
    protein_g, carbs_g = math.floor(protein), math.floor(carbs)
    remaining = kcal - defaults.KCAL_PER_G_PROTEIN * (protein_g + carbs_g)
    fat_g = max(round(remaining / defaults.KCAL_PER_G_FAT), math.ceil(fat_min_g))
    limited = (Advisory.CARBS_LIMITED_BY_ENERGY,) if carbs < wanted - 1.0 else ()
    return Macros(carbs_g, protein_g, fat_g), limited
