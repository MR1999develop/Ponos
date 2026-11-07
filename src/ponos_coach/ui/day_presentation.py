# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""What the day page shows, as ready-made texts: testable without a window.

`present_day` turns the use case's result into a `DayView` (activities, totals, one card per
eating moment, advisories) or an `UnavailableView` (what to complete). The decisions (what is
shown, in which order, with which words) are here; the page only lays them out. Health data: never
in logs.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from PySide6.QtCore import QCoreApplication, QDate, QLocale, QTime

from ponos_coach.nutrition.domain import FoodExample, HourlyIntake, MealItem
from ponos_coach.profiles.day_nutrition import (
    DayActivity,
    DayNutritionReport,
    DayNutritionResult,
    MealSuggestion,
    NutritionUnavailable,
)
from ponos_coach.ui.activity_text import duration_text, sport_text
from ponos_coach.ui.nutrition_texts import (
    MissingDataText,
    advisory_text,
    energy_source_text,
    fixed_notice_text,
    food_name,
    foods_unverified_text,
    goal_text,
    in_progress_text,
    lean_mass_note_text,
    load_level_text,
    refuel_hour_text,
    slots_text,
    unavailable_text,
)

_MINUTES_PER_HOUR = 60


@dataclass(frozen=True, slots=True)
class MealCardView:
    """One eating moment: title, time, amounts and example foods.

    The amounts are numbers without units (the headers carry the units).
    """

    title: str
    time: str
    moved_note: str  # empty if the meal was not moved because of training
    kcal: str
    carbs: str
    protein: str
    fat: str
    hourly: str  # empty unless it is the during-training moment
    examples: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DayView:
    """Guidance for a day, all as texts."""

    title: str
    goal: str
    level: str
    activities: tuple[tuple[str, ...], ...]  # time, sport, duration, load, energy, source
    totals: tuple[tuple[str, str], ...]  # label (with unit) and value
    meals: tuple[MealCardView, ...]
    advisories: tuple[str, ...]
    unreadable_note: str  # empty if every activity file could be read
    in_progress_note: str  # empty unless the day is today
    foods_note: str  # empty once the food values are verified against their source
    lean_mass_note: str  # which lean mass the energy availability limit uses; empty if unknown
    notice: str


@dataclass(frozen=True, slots=True)
class UnavailableView:
    """A day without guidance: what is missing and what to do."""

    title: str
    missing: MissingDataText
    notice: str


type DayContent = DayView | UnavailableView


def number_text(value: float | int) -> str:
    """A whole number in the locale's format."""
    return QLocale().toString(round(value))


def clock_text(minute_of_day: int) -> str:
    """A time of day from minutes after midnight, in the locale's short format."""
    moment = QTime(minute_of_day // _MINUTES_PER_HOUR, minute_of_day % _MINUTES_PER_HOUR)
    return QLocale().toString(moment, QLocale.FormatType.ShortFormat)


def day_title(day: date) -> str:
    """The day's long date."""
    return QLocale().toString(QDate(day.year, day.month, day.day), QLocale.FormatType.LongFormat)


def activity_headers() -> tuple[str, ...]:
    """Headers of the activities table, with units."""
    translate = QCoreApplication.translate
    return (
        translate("DayPage", "Time"),
        translate("DayPage", "Sport"),
        translate("DayPage", "Duration (h:mm:ss)"),
        translate("DayPage", "Load (hrTSS)"),
        translate("DayPage", "Energy (kcal)"),
        translate("DayPage", "Energy from"),
    )


ACTIVITY_NUMERIC_COLUMNS = (2, 3, 4)


def summary_headers() -> tuple[str, str]:
    """Headers of the totals table."""
    return (
        QCoreApplication.translate("DayPage", "Total"),
        QCoreApplication.translate("DayPage", "Amount"),
    )


def _activity_row(activity: DayActivity) -> tuple[str, ...]:
    return (
        clock_text(activity.local_start_minute),
        sport_text(activity.sport),
        duration_text(activity.duration_s),
        number_text(activity.load),
        number_text(activity.energy.total_kcal),
        energy_source_text(activity.energy.source),
    )


def _totals(report: DayNutritionReport) -> tuple[tuple[str, str], ...]:
    translate = QCoreApplication.translate
    nutrition = report.nutrition
    macros = nutrition.macros
    return (
        (translate("DayPage", "Basal metabolism (kcal)"), number_text(nutrition.basal_kcal)),
        (translate("DayPage", "Energy spent (kcal)"), number_text(nutrition.expenditure_kcal)),
        (translate("DayPage", "Calorie deficit (kcal)"), number_text(nutrition.deficit_kcal)),
        (translate("DayPage", "Energy target (kcal)"), number_text(nutrition.target_kcal)),
        (translate("DayPage", "Carbohydrates (g)"), number_text(macros.carbs_g)),
        (translate("DayPage", "Protein (g)"), number_text(macros.protein_g)),
        (translate("DayPage", "Fat (g)"), number_text(macros.fat_g)),
    )


def _example_text(example: FoodExample) -> str:
    return " + ".join(f"{food_name(p.food)} {number_text(p.grams)} g" for p in example.portions)


def _hourly_text(hourly: HourlyIntake) -> str:
    # {carbs}, {fluid} and {sodium}: amounts per hour, as numbers.
    return QCoreApplication.translate(
        "DayPage",
        "Indicative: about {carbs} g of carbohydrate, {fluid} ml of fluid and {sodium} mg "
        "of sodium per hour",
    ).format(
        carbs=number_text(hourly.carbs_g),
        fluid=number_text(hourly.fluid_ml),
        sodium=number_text(hourly.sodium_mg),
    )


def _meal_title(meal: MealItem) -> str:
    """The name of the moment; an hourly refuel intake says its hour instead of the slot."""
    if meal.refuel_hour is not None:
        return refuel_hour_text(meal.refuel_hour)
    return slots_text(meal.slots)


def _meal_card(suggestion: MealSuggestion) -> MealCardView:
    meal = suggestion.meal
    moved = (
        QCoreApplication.translate("DayPage", "Moved from its usual time because of training.")
        if meal.moved
        else ""
    )
    return MealCardView(
        _meal_title(meal),
        clock_text(meal.minute),
        moved,
        number_text(meal.macros.kcal),
        number_text(meal.macros.carbs_g),
        number_text(meal.macros.protein_g),
        number_text(meal.macros.fat_g),
        "" if meal.hourly is None else _hourly_text(meal.hourly),
        tuple(_example_text(example) for example in suggestion.examples),
    )


def _unreadable_note(count: int) -> str:
    if count == 0:
        return ""
    return QCoreApplication.translate(
        "DayPage",
        "Activity files that could not be read: %n. The guidance may be less exact.",
        "",
        count,
    )


def _present_report(
    report: DayNutritionReport,
    *,
    in_progress: bool,
    verified: bool,
    lean_mass_measured: bool | None,
) -> DayView:
    return DayView(
        day_title(report.day),
        goal_text(report.goal),
        load_level_text(report.nutrition.level),
        tuple(_activity_row(a) for a in report.activities),
        _totals(report),
        tuple(_meal_card(m) for m in report.meals),
        tuple(advisory_text(a) for a in report.nutrition.advisories),
        _unreadable_note(report.unreadable_activities),
        in_progress_text() if in_progress else "",
        "" if verified else foods_unverified_text(),
        "" if lean_mass_measured is None else lean_mass_note_text(measured=lean_mass_measured),
        fixed_notice_text(),
    )


def present_day(
    day: date,
    result: DayNutritionResult,
    *,
    today: date | None = None,
    foods_verified: bool = True,
    lean_mass_measured: bool | None = None,
) -> DayContent:
    """The page content of a day from the use case's result.

    `today` (the profile's today) marks the current day as in progress; `foods_verified` is False
    while the food table's values have not been checked against their source;
    `lean_mass_measured` says whether the energy availability limit used a measured lean mass
    (True) or the estimate (False); None leaves the note out.
    """
    if isinstance(result, NutritionUnavailable):
        return UnavailableView(day_title(day), unavailable_text(result.reason), fixed_notice_text())
    return _present_report(
        result,
        in_progress=day == today,
        verified=foods_verified,
        lean_mass_measured=lean_mass_measured,
    )
