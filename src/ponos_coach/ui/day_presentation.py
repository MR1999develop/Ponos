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

from ponos_coach.activities.domain import ActivityIdentity
from ponos_coach.nutrition.domain import HourlyIntake, MealItem
from ponos_coach.profiles.day_nutrition import (
    DayActivity,
    DayNutritionReport,
    DayNutritionResult,
    NutritionUnavailable,
)
from ponos_coach.ui.activity_text import duration_text, sport_text
from ponos_coach.ui.nutrition_texts import (
    MissingDataText,
    advisory_text,
    energy_source_text,
    estimate_from_plan_text,
    fixed_notice_text,
    goal_text,
    in_progress_text,
    lean_mass_note_text,
    load_level_text,
    refuel_hours_text,
    slots_text,
    unavailable_text,
)

_MINUTES_PER_HOUR = 60


@dataclass(frozen=True, slots=True)
class MealCardView:
    """One eating moment: title, time and amounts (no example foods: user's request of 2026-11-10).

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
    lean_mass_note: str  # which lean mass the energy availability limit uses; empty if unknown
    notice: str
    estimate_note: str = ""  # empty unless the day is ahead: its activities are the plan's
    # One per row of `activities`: the activity to open, `None` for a session of the plan.
    activity_ids: tuple[ActivityIdentity | None, ...] = ()
    planned: str = ""  # the planned session in full (phase, blocks, load); empty without a plan


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
        translate("DayPage", "Load (TRIMPeq)"),
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
    """The name of the moment; a refuel intake says the hours it covers instead of the slot."""
    hours = meal.refuel_hours
    if hours is not None:
        return refuel_hours_text(*hours)
    return slots_text(meal.slots)


def _meal_card(meal: MealItem) -> MealCardView:
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
    planned: str,
) -> DayView:
    return DayView(
        day_title(report.day),
        goal_text(report.goal),
        load_level_text(report.nutrition.level),
        tuple(_activity_row(a) for a in report.activities),
        _totals(report),
        tuple(_meal_card(m) for m in report.nutrition.meals),
        tuple(advisory_text(a) for a in report.nutrition.advisories),
        _unreadable_note(report.unreadable_activities),
        in_progress_text() if in_progress else "",
        lean_mass_note_text(measured=report.lean_mass_measured),
        fixed_notice_text(),
        estimate_from_plan_text() if report.from_plan else "",
        tuple(a.identity for a in report.activities),
        planned,
    )


def present_day(
    day: date,
    result: DayNutritionResult,
    *,
    today: date | None = None,
    planned: str = "",
) -> DayContent:
    """The page content of a day from the use case's result.

    `today` (the profile's today) marks the current day as in progress. The note on the lean
    mass comes from the report (`lean_mass_measured`). `planned` is the day's planned session in
    full, as the calendar shows it.
    """
    if isinstance(result, NutritionUnavailable):
        return UnavailableView(day_title(day), unavailable_text(result.reason), fixed_notice_text())
    return _present_report(
        result,
        in_progress=day == today,
        planned=planned,
    )
