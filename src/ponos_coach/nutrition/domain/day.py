# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Nutrition guidance for one day: the single entry point of the model.

`plan_day` is a pure, deterministic function: the same day input always gives the same guidance.
It is general guidance, never medical advice: the result always carries the advisories that apply
(minors, low body mass index, limited deficit...) for the interface to word.
"""

from __future__ import annotations

from dataclasses import dataclass

from ponos_coach.nutrition.domain import defaults
from ponos_coach.nutrition.domain.advice import Advisory, DayLoadLevel, classify_day_load, ordered
from ponos_coach.nutrition.domain.energy import Person, basal_metabolic_rate
from ponos_coach.nutrition.domain.macros import Macros
from ponos_coach.nutrition.domain.meals import MealItem, TrainingSession, schedule_meals
from ponos_coach.nutrition.domain.settings import MealTimes, NutritionGoal
from ponos_coach.nutrition.domain.targets import day_macros, energy_availability, energy_target


@dataclass(frozen=True, slots=True)
class DayInput:
    """Everything the model needs for one day.

    `sessions` are the day's training sessions in any order; `minutes_to_next_session` is the
    time from the end of the last of them to the next session (the following day's), if there is
    one known. `weight_loss_days` is how long the weight-loss period the day belongs to has lasted,
    in days (0 on its first day); None outside a weight-loss period.
    """

    person: Person
    goal: NutritionGoal
    meal_times: MealTimes
    sessions: tuple[TrainingSession, ...] = ()
    minutes_to_next_session: float | None = None
    weight_loss_days: int | None = None

    def __post_init__(self) -> None:
        if self.weight_loss_days is not None and self.weight_loss_days < 0:
            raise ValueError("a weight-loss period cannot have a negative duration")
        if self.minutes_to_next_session is not None and self.minutes_to_next_session < 0:
            raise ValueError("the next session cannot start before the last one ends")


@dataclass(frozen=True, slots=True)
class DayNutrition:
    """Guidance for a day: energy, level, macro totals, items and advisories.

    `macros` is the day's total in whole grams and equals the sum of the `meals`; its energy can
    differ from `target_kcal` by a few kcal because of rounding.
    """

    basal_kcal: int
    expenditure_kcal: int
    deficit_kcal: int
    target_kcal: int
    level: DayLoadLevel
    macros: Macros
    meals: tuple[MealItem, ...]
    advisories: tuple[Advisory, ...]


def _long_weight_loss(day: DayInput, deficit_kcal: int, target_kcal: int, net_kcal: float) -> bool:
    """True if a deficit is applied today, the weight-loss period has lasted over the limit and
    the day's energy availability is under the balanced value (CSN, GJSN)."""
    if day.weight_loss_days is None or deficit_kcal <= 0:
        return False
    if day.weight_loss_days <= defaults.LONG_WEIGHT_LOSS_WEEKS * defaults.DAYS_PER_WEEK:
        return False
    availability = energy_availability(day.person, target_kcal, net_kcal)
    return availability < defaults.BALANCED_ENERGY_AVAILABILITY_KCAL_PER_KG_FFM


def plan_day(day: DayInput) -> DayNutrition:
    """Energy target, macros and meal items for a day.

    Steps: net activity energy and load level of the day, expenditure and target (with the
    deficit's safety limits), macro totals, then the split into meals and training intakes.
    """
    person = day.person
    net_kcal = sum(s.net_kcal for s in day.sessions)
    level = classify_day_load(
        sum(s.load for s in day.sessions), sum(s.duration_min for s in day.sessions)
    )
    target = energy_target(person, day.goal, level, basal_metabolic_rate(person), net_kcal)
    macros, macro_advisories = day_macros(person, day.goal, level, target)
    meals, scaled = schedule_meals(
        macros, person.weight_kg, day.meal_times, day.sessions, day.minutes_to_next_session
    )
    extra = [Advisory.INTAKES_SCALED] if scaled else []
    if _long_weight_loss(day, target.deficit_kcal, target.target_kcal, net_kcal):
        extra.append(Advisory.LONG_WEIGHT_LOSS)
    return DayNutrition(
        target.basal_kcal,
        target.expenditure_kcal,
        target.deficit_kcal,
        target.target_kcal,
        level,
        macros,
        meals,
        ordered([*target.advisories, *macro_advisories, *extra]),
    )
