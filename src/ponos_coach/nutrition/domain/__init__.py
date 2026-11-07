# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Pure domain of the nutrition feature (no I/O, Qt, network or logging)."""

from ponos_coach.nutrition.domain.advice import (
    Advisory,
    DayLoadLevel,
    classify_day_load,
    ordered,
)
from ponos_coach.nutrition.domain.day import (
    DayInput,
    DayNutrition,
    plan_day,
)
from ponos_coach.nutrition.domain.energy import (
    ActivityEnergy,
    EnergySource,
    Person,
    activity_energy,
    average_valid_bpm,
    basal_metabolic_rate,
    keytel_kcal_per_minute,
    met_kcal,
)
from ponos_coach.nutrition.domain.foods import (
    Food,
    FoodExample,
    FoodGroup,
    FoodPortion,
    MealRole,
    role_of,
    suggest_examples,
    tolerance_g,
)
from ponos_coach.nutrition.domain.macros import (
    Macros,
    split_integer,
)
from ponos_coach.nutrition.domain.meals import (
    HourlyIntake,
    MealItem,
    MealSlot,
    TrainingSession,
    schedule_meals,
)
from ponos_coach.nutrition.domain.settings import (
    ActivityLevel,
    ActivityLevelEntry,
    GoalPeriod,
    MaintenanceGoal,
    MealTimes,
    NutritionGoal,
    PerformanceGoal,
    WeightLossGoal,
    goal_for_day,
    level_for_day,
)
from ponos_coach.nutrition.domain.targets import (
    EnergyTarget,
    day_macros,
    energy_availability,
    energy_target,
    estimated_fat_free_mass_kg,
    max_deficit_for_energy_availability,
    permitted_weekly_loss_g,
    requested_deficit_kcal,
)

__all__ = [
    "ActivityEnergy",
    "ActivityLevel",
    "ActivityLevelEntry",
    "Advisory",
    "DayInput",
    "DayLoadLevel",
    "DayNutrition",
    "EnergySource",
    "EnergyTarget",
    "Food",
    "FoodExample",
    "FoodGroup",
    "FoodPortion",
    "GoalPeriod",
    "HourlyIntake",
    "Macros",
    "MaintenanceGoal",
    "MealItem",
    "MealRole",
    "MealSlot",
    "MealTimes",
    "NutritionGoal",
    "PerformanceGoal",
    "Person",
    "TrainingSession",
    "WeightLossGoal",
    "activity_energy",
    "average_valid_bpm",
    "basal_metabolic_rate",
    "classify_day_load",
    "day_macros",
    "energy_availability",
    "energy_target",
    "estimated_fat_free_mass_kg",
    "goal_for_day",
    "keytel_kcal_per_minute",
    "level_for_day",
    "max_deficit_for_energy_availability",
    "met_kcal",
    "ordered",
    "permitted_weekly_loss_g",
    "plan_day",
    "requested_deficit_kcal",
    "role_of",
    "schedule_meals",
    "split_integer",
    "suggest_examples",
    "tolerance_g",
]
