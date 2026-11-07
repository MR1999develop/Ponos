# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Nutrition texts: the domain gives categories, here we choose how to word them.

Plain words for everything the user reads about a day: advisories, energy sources, meal
moments, load levels, goals, activity levels, what is missing, and the names of the foods (by their
`food_id`, the key that the translations use). Context "Nutrition" and "Foods" written as literals:
lupdate does not follow aliases.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from PySide6.QtCore import QT_TRANSLATE_NOOP, QCoreApplication, QLocale

from ponos_coach.nutrition.domain import (
    ActivityLevel,
    Advisory,
    DayLoadLevel,
    EnergySource,
    Food,
    MaintenanceGoal,
    MealSlot,
    NutritionGoal,
    PerformanceGoal,
    WeightLossGoal,
)
from ponos_coach.profiles.day_nutrition import UnavailableReason

_GRAMS_PER_KG = 1000.0
_PACE_DECIMALS = 2

# Source names of the foods, by id. An id that is not here shows the name written in the table.
FOOD_NAMES: dict[str, object] = {
    "oats_rolled_dry": QT_TRANSLATE_NOOP("Foods", "Rolled oats, dry"),
    "bread_whole_wheat": QT_TRANSLATE_NOOP("Foods", "Whole-wheat bread"),
    "bread_white": QT_TRANSLATE_NOOP("Foods", "White bread"),
    "rice_white_cooked": QT_TRANSLATE_NOOP("Foods", "White rice, cooked"),
    "rice_brown_cooked": QT_TRANSLATE_NOOP("Foods", "Brown rice, cooked"),
    "pasta_cooked": QT_TRANSLATE_NOOP("Foods", "Pasta, cooked"),
    "pasta_whole_wheat_cooked": QT_TRANSLATE_NOOP("Foods", "Whole-wheat pasta, cooked"),
    "potato_boiled": QT_TRANSLATE_NOOP("Foods", "Potato, boiled"),
    "sweet_potato_baked": QT_TRANSLATE_NOOP("Foods", "Sweet potato, baked"),
    "quinoa_cooked": QT_TRANSLATE_NOOP("Foods", "Quinoa, cooked"),
    "banana": QT_TRANSLATE_NOOP("Foods", "Banana"),
    "apple": QT_TRANSLATE_NOOP("Foods", "Apple"),
    "orange": QT_TRANSLATE_NOOP("Foods", "Orange"),
    "dates_medjool": QT_TRANSLATE_NOOP("Foods", "Dates, Medjool"),
    "honey": QT_TRANSLATE_NOOP("Foods", "Honey"),
    "raisins": QT_TRANSLATE_NOOP("Foods", "Raisins"),
    "orange_juice": QT_TRANSLATE_NOOP("Foods", "Orange juice"),
    "egg_hard_boiled": QT_TRANSLATE_NOOP("Foods", "Egg, hard-boiled"),
    "chicken_breast_roasted": QT_TRANSLATE_NOOP("Foods", "Chicken breast, roasted"),
    "tuna_light_canned_water": QT_TRANSLATE_NOOP("Foods", "Tuna, light, canned in water"),
    "salmon_farmed_cooked": QT_TRANSLATE_NOOP("Foods", "Salmon, farmed, cooked"),
    "greek_yogurt_nonfat": QT_TRANSLATE_NOOP("Foods", "Greek yogurt, plain, nonfat"),
    "yogurt_plain_lowfat": QT_TRANSLATE_NOOP("Foods", "Yogurt, plain, low-fat"),
    "milk_whole": QT_TRANSLATE_NOOP("Foods", "Milk, whole"),
    "mozzarella_part_skim": QT_TRANSLATE_NOOP("Foods", "Mozzarella, part-skim"),
    "lentils_cooked": QT_TRANSLATE_NOOP("Foods", "Lentils, cooked"),
    "chickpeas_cooked": QT_TRANSLATE_NOOP("Foods", "Chickpeas, cooked"),
    "black_beans_cooked": QT_TRANSLATE_NOOP("Foods", "Black beans, cooked"),
    "tofu_firm": QT_TRANSLATE_NOOP("Foods", "Tofu, firm"),
    "olive_oil": QT_TRANSLATE_NOOP("Foods", "Olive oil"),
    "almonds": QT_TRANSLATE_NOOP("Foods", "Almonds"),
    "walnuts": QT_TRANSLATE_NOOP("Foods", "Walnuts"),
    "peanut_butter": QT_TRANSLATE_NOOP("Foods", "Peanut butter"),
    "avocado": QT_TRANSLATE_NOOP("Foods", "Avocado"),
    "butter": QT_TRANSLATE_NOOP("Foods", "Butter"),
    "tomato": QT_TRANSLATE_NOOP("Foods", "Tomato"),
    "broccoli": QT_TRANSLATE_NOOP("Foods", "Broccoli"),
    "spinach": QT_TRANSLATE_NOOP("Foods", "Spinach"),
    "zucchini": QT_TRANSLATE_NOOP("Foods", "Zucchini"),
    "carrot": QT_TRANSLATE_NOOP("Foods", "Carrot"),
    "romaine_lettuce": QT_TRANSLATE_NOOP("Foods", "Romaine lettuce"),
    "green_beans": QT_TRANSLATE_NOOP("Foods", "Green beans"),
}


def food_name(food: Food) -> str:
    """The translated name of a food (the table's English name if it has no translation entry)."""
    source = FOOD_NAMES.get(food.food_id)
    if source is None:
        return food.name
    return QCoreApplication.translate("Foods", str(source))


@dataclass(frozen=True, slots=True)
class MissingDataText:
    """What to tell the user when a day has no guidance: what is missing and what to do."""

    title: str
    what: str
    remedy: str
    can_edit_profile: bool  # True if "Edit profile" is the way to complete the data


def unavailable_text(reason: UnavailableReason) -> MissingDataText:
    """What is missing for the day's guidance, and what to do about it."""
    translate = QCoreApplication.translate
    complete = translate("Nutrition", "Complete it with Edit profile.")
    check_birth_date = translate("Nutrition", "Check the date of birth with Edit profile.")
    see_professional = translate("Nutrition", "Please talk to a doctor or a sports dietitian.")
    texts = {
        UnavailableReason.MISSING_BODY_DATA: (
            translate("Nutrition", "Height and date of birth are missing"),
            translate("Nutrition", "Nutrition guidance needs your height and date of birth."),
            complete,
        ),
        UnavailableReason.MISSING_WEIGHT: (
            translate("Nutrition", "No weight is saved"),
            translate("Nutrition", "Nutrition guidance needs your weight."),
            complete,
        ),
        UnavailableReason.MISSING_PHYSIOLOGY: (
            translate("Nutrition", "Heart-rate settings are missing"),
            translate(
                "Nutrition",
                "Nutrition guidance needs your heart-rate settings (they include sex).",
            ),
            complete,
        ),
        UnavailableReason.BEFORE_BIRTH: (
            translate("Nutrition", "This day is before the date of birth"),
            translate("Nutrition", "The date of birth in the profile is after this day."),
            check_birth_date,
        ),
        UnavailableReason.AGE_OUT_OF_RANGE: (
            translate("Nutrition", "This day is too far from the date of birth"),
            translate("Nutrition", "Nutrition guidance covers ages up to 130 years."),
            check_birth_date,
        ),
        UnavailableReason.NO_PLAN_FOR_DAY: (
            translate("Nutrition", "The plan does not cover this day"),
            translate(
                "Nutrition",
                "Guidance for a day ahead comes from the training plan, and the plan has "
                "nothing saved for this day.",
            ),
            translate("Nutrition", "Update the plan in the Goals section."),
        ),
        UnavailableReason.UNDER_18: (
            translate("Nutrition", "No guidance under 18"),
            translate("Nutrition", "Calories, macronutrients and portions are not given under 18."),
            see_professional,
        ),
    }
    title, what, remedy = texts[reason]
    can_edit = reason not in (UnavailableReason.UNDER_18, UnavailableReason.NO_PLAN_FOR_DAY)
    return MissingDataText(title, what, remedy, can_edit)


def advisory_text(advisory: Advisory) -> str:
    """An advisory in plain words."""
    texts = {
        Advisory.UNDER_18: QCoreApplication.translate(
            "Nutrition",
            "Please talk to a doctor or a sports dietitian.",
        ),
        Advisory.LOW_BMI: QCoreApplication.translate(
            "Nutrition",
            "Your body mass index is below 18.5. "
            "Please talk to a doctor or a dietitian before changing how you eat.",
        ),
        Advisory.ACTIVITY_LEVEL_ASSUMED: QCoreApplication.translate(
            "Nutrition",
            "No daily activity level is set for this day, so a sedentary level is assumed. "
            "You can set it in Nutrition settings.",
        ),
        Advisory.DEFICIT_NOT_APPLIED: QCoreApplication.translate(
            "Nutrition",
            "This app does not suggest a calorie deficit for this day.",
        ),
        Advisory.DEFICIT_LIMITED: QCoreApplication.translate(
            "Nutrition", "The calorie deficit was reduced to stay within cautious limits."
        ),
        Advisory.DEFICIT_REDUCED_FOR_LOAD: QCoreApplication.translate(
            "Nutrition", "The calorie deficit is smaller because today's training is heavy."
        ),
        Advisory.DEFICIT_PAUSED_FOR_RACE: QCoreApplication.translate(
            "Nutrition",
            "No calorie deficit in the week of a race or in the taper before it: "
            "weight loss is best kept away from races.",
        ),
        Advisory.CARBS_LIMITED_BY_ENERGY: QCoreApplication.translate(
            "Nutrition",
            "The energy target is too low for the usual amount of carbohydrates, "
            "so carbohydrates are lower than today's training would suggest.",
        ),
        Advisory.INTAKES_SCALED: QCoreApplication.translate(
            "Nutrition", "The amounts around training were reduced to fit the day's totals."
        ),
        Advisory.LONG_WEIGHT_LOSS: QCoreApplication.translate(
            "Nutrition",
            "This weight-loss period has lasted a long time. Long periods of weight loss are "
            "best alternated with periods of maintenance.",
        ),
    }
    return texts[advisory]


def fixed_notice_text() -> str:
    """The notice that is always shown with the guidance."""
    return QCoreApplication.translate(
        "Nutrition",
        "General guidance, not medical or personalised nutrition advice. "
        "If you have a medical condition, are pregnant, have an eating disorder "
        "or take medication, please consult a professional.",
    )


def in_progress_text() -> str:
    """The note for the current day: it is not over, so the values are partial."""
    return QCoreApplication.translate(
        "Nutrition",
        "This day is not over yet: the values are partial and change as activities "
        "and the energy spent are added.",
    )


def estimate_from_plan_text() -> str:
    """The note for a day ahead: the activities are the planned sessions, not recorded ones."""
    return QCoreApplication.translate(
        "Nutrition", "Estimate from the plan: the activities are the planned sessions."
    )


def foods_unverified_text() -> str:
    """The note shown while the food values have not been checked against their source."""
    return QCoreApplication.translate(
        "Nutrition", "Food values not yet verified against the source."
    )


def energy_source_text(source: EnergySource) -> str:
    """Where an activity's calories come from."""
    texts = {
        EnergySource.DECLARED: QCoreApplication.translate("Nutrition", "Declared by the device"),
        EnergySource.HEART_RATE: QCoreApplication.translate(
            "Nutrition", "Estimated from heart rate"
        ),
        EnergySource.MET: QCoreApplication.translate("Nutrition", "Estimated from the sport"),
    }
    return texts[source]


def slot_text(slot: MealSlot) -> str:
    """The name of an eating occasion."""
    texts = {
        MealSlot.BREAKFAST: QCoreApplication.translate("Nutrition", "Breakfast"),
        MealSlot.LUNCH: QCoreApplication.translate("Nutrition", "Lunch"),
        MealSlot.DINNER: QCoreApplication.translate("Nutrition", "Dinner"),
        MealSlot.PRE_TRAINING: QCoreApplication.translate("Nutrition", "Before training"),
        MealSlot.DURING_TRAINING: QCoreApplication.translate("Nutrition", "During training"),
        MealSlot.POST_TRAINING: QCoreApplication.translate("Nutrition", "After training"),
    }
    return texts[slot]


def refuel_hour_text(hour: int) -> str:
    """The title of an hourly intake after a session that is followed by another soon (hour 1 to
    4 after the end)."""
    # {hour}: the hour after the end of the session, as a number.
    return QCoreApplication.translate("Nutrition", "Refuel, hour {hour}").format(
        hour=QLocale().toString(hour)
    )


def lean_mass_note_text(*, measured: bool) -> str:
    """Which lean mass the limit on energy availability of the day uses."""
    if measured:
        return QCoreApplication.translate(
            "Nutrition", "The limit on energy availability uses your measured lean mass."
        )
    return QCoreApplication.translate(
        "Nutrition",
        "The limit on energy availability uses an estimate of your lean mass. You can enter a "
        "measured value in Nutrition settings.",
    )


def slots_text(slots: Sequence[MealSlot]) -> str:
    """The names of the occasions that one item merges, in display order."""
    return " + ".join(slot_text(slot) for slot in slots)


def load_level_text(level: DayLoadLevel) -> str:
    """How demanding the day's training is."""
    texts = {
        DayLoadLevel.REST: QCoreApplication.translate("Nutrition", "Rest day"),
        DayLoadLevel.LIGHT: QCoreApplication.translate("Nutrition", "Light training"),
        DayLoadLevel.MODERATE: QCoreApplication.translate("Nutrition", "Moderate training"),
        DayLoadLevel.HIGH: QCoreApplication.translate("Nutrition", "High training"),
        DayLoadLevel.VERY_HIGH: QCoreApplication.translate("Nutrition", "Very high training"),
    }
    return texts[level]


def activity_level_text(level: ActivityLevel) -> str:
    """The daily activity outside sport."""
    texts = {
        ActivityLevel.SEDENTARY: QCoreApplication.translate("Nutrition", "Sedentary"),
        ActivityLevel.LIGHT: QCoreApplication.translate("Nutrition", "Lightly active"),
        ActivityLevel.ACTIVE: QCoreApplication.translate("Nutrition", "Active"),
        ActivityLevel.VERY_ACTIVE: QCoreApplication.translate("Nutrition", "Very active"),
    }
    return texts[level]


def pace_text(weekly_loss_g: int) -> str:
    """A weekly weight loss in kilograms, in the locale's format."""
    kilograms = QLocale().toString(weekly_loss_g / _GRAMS_PER_KG, "f", _PACE_DECIMALS)
    # {pace}: kilograms per week, as a number.
    return QCoreApplication.translate("Nutrition", "{pace} kg per week").format(pace=kilograms)


def goal_name_text(goal: NutritionGoal) -> str:
    """The goal's name (for the calendar legend and the table)."""
    match goal:
        case MaintenanceGoal():
            return QCoreApplication.translate("Nutrition", "Maintenance")
        case PerformanceGoal():
            return QCoreApplication.translate("Nutrition", "Performance")
        case WeightLossGoal():
            return QCoreApplication.translate("Nutrition", "Weight loss")


def goal_text(goal: NutritionGoal) -> str:
    """The goal with its pace when it has one."""
    if isinstance(goal, WeightLossGoal):
        # {goal}: the goal's name; {pace}: the pace, for example "0.50 kg per week".
        return QCoreApplication.translate("Nutrition", "{goal}, {pace}").format(
            goal=goal_name_text(goal), pace=pace_text(goal.weekly_loss_g)
        )
    return goal_name_text(goal)
