# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""What the day page shows, as texts: advisories, moments, foods, units and what is missing.

The reports are built with the real nutrition model and the shipped food table; the person is made
up (no real data).
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from PySide6.QtCore import QCoreApplication, QLocale, QTranslator

from ponos_coach.activities.domain import FileId, FitIdentity, Sport
from ponos_coach.load.domain import Sex
from ponos_coach.nutrition.domain import (
    ActivityEnergy,
    ActivityLevel,
    Advisory,
    DayInput,
    DayLoadLevel,
    EnergySource,
    MaintenanceGoal,
    MealSlot,
    MealTimes,
    Person,
    TrainingSession,
    WeightLossGoal,
    plan_day,
    suggest_examples,
)
from ponos_coach.profiles.day_nutrition import (
    DayActivity,
    DayNutritionReport,
    MealSuggestion,
    NutritionUnavailable,
    UnavailableReason,
)
from ponos_coach.ui.day_presentation import DayView, UnavailableView, present_day
from ponos_coach.ui.nutrition_texts import (
    FOOD_NAMES,
    activity_level_text,
    advisory_text,
    energy_source_text,
    fixed_notice_text,
    goal_text,
    load_level_text,
    refuel_hour_text,
    slot_text,
    unavailable_text,
)
from ponos_coach.ui.translations import APP_CATALOG
from tests.food_support import load_food_table
from tests.ui.nutrition_support import plain
from tests.ui.test_translations import build_real_catalog

DAY = date(2026, 6, 10)


@pytest.fixture(autouse=True)
def english_numbers() -> Iterator[None]:
    previous = QLocale()
    QLocale.setDefault(QLocale(QLocale.Language.English, QLocale.Country.UnitedStates))
    yield
    QLocale.setDefault(previous)


def report_for(
    person: Person,
    goal: WeightLossGoal | MaintenanceGoal,
    sessions: tuple[TrainingSession, ...] = (),
) -> DayNutritionReport:
    nutrition = plan_day(DayInput(person, goal, MealTimes.default(), sessions))
    foods = load_food_table().foods
    suggestions = tuple(
        MealSuggestion(meal, suggest_examples(meal, foods)) for meal in nutrition.meals
    )
    activities = tuple(
        DayActivity(
            FitIdentity(FileId(1, 1, 1, datetime(2026, 6, 10, 5, tzinfo=UTC))),
            Sport.RUNNING,
            datetime(2026, 6, 10, 5, tzinfo=UTC),
            s.start_minute,
            s.duration_min * 60,
            s.load,
            ActivityEnergy(s.net_kcal + 100, s.net_kcal, EnergySource.DECLARED),
        )
        for s in sessions
    )
    return DayNutritionReport(DAY, nutrition, goal, MealTimes.default(), suggestions, activities, 0)


ADULT = Person(Sex.MALE, 30, 178, 72.0, ActivityLevel.LIGHT)
LONG_RIDE = TrainingSession(8 * 60, 180.0, 1400.0, 210.0)


def test_a_rest_day_has_three_meals_in_time_order_with_amounts_and_example_foods() -> None:
    view = present_day(DAY, report_for(ADULT, MaintenanceGoal()))

    assert isinstance(view, DayView)
    assert [(m.title, plain(m.time)) for m in view.meals] == [
        ("Breakfast", "7:30 AM"),
        ("Lunch", "1:00 PM"),
        ("Dinner", "8:00 PM"),
    ]
    assert all(meal.examples for meal in view.meals)
    assert view.activities == ()
    assert view.level == "Rest day"


def test_a_long_session_has_a_during_training_card_with_the_hourly_amounts() -> None:
    view = present_day(DAY, report_for(ADULT, MaintenanceGoal(), (LONG_RIDE,)))

    assert isinstance(view, DayView)
    during = [m for m in view.meals if m.title == "During training"]
    assert len(during) == 1
    assert during[0].hourly.startswith("Indicative: about ")
    assert "g of carbohydrate," in during[0].hourly
    assert "ml of fluid and" in during[0].hourly
    assert "mg of sodium per hour" in during[0].hourly
    assert all(not m.hourly for m in view.meals if m is not during[0])
    assert [m.time for m in view.meals] == sorted((m.time for m in view.meals), key=_clock_minutes)


def _clock_minutes(text: str) -> int:
    clock, meridiem = plain(text).split()
    hour, minute = (int(part) for part in clock.split(":"))
    return (hour % 12 + (12 if meridiem == "PM" else 0)) * 60 + minute


def test_the_totals_have_their_units_in_the_labels_and_the_numbers_in_the_locale() -> None:
    view = present_day(DAY, report_for(ADULT, MaintenanceGoal()))

    assert isinstance(view, DayView)
    labels = [label for label, _ in view.totals]
    assert labels == [
        "Basal metabolism (kcal)",
        "Energy spent (kcal)",
        "Calorie deficit (kcal)",
        "Energy target (kcal)",
        "Carbohydrates (g)",
        "Protein (g)",
        "Fat (g)",
    ]
    basal = dict(view.totals)["Basal metabolism (kcal)"]
    assert basal == f"{round(10 * 72 + 6.25 * 178 - 5 * 30 + 5):,}"  # Mifflin-St Jeor, by hand


def test_a_weight_loss_for_a_minor_shows_both_advisories_in_plain_words() -> None:
    minor = Person(Sex.FEMALE, 16, 165, 55.0, ActivityLevel.ACTIVE)

    view = present_day(DAY, report_for(minor, WeightLossGoal(500)))

    assert isinstance(view, DayView)
    assert advisory_text(Advisory.UNDER_18) in view.advisories
    assert advisory_text(Advisory.DEFICIT_NOT_APPLIED) in view.advisories
    assert view.goal == "Weight loss, 0.50 kg per week"
    assert "dietitian" in advisory_text(Advisory.UNDER_18)


def test_an_unset_activity_level_is_an_advisory() -> None:
    nobody = Person(Sex.MALE, 30, 178, 72.0, None)

    view = present_day(DAY, report_for(nobody, MaintenanceGoal()))

    assert isinstance(view, DayView)
    assert advisory_text(Advisory.ACTIVITY_LEVEL_ASSUMED) in view.advisories


def test_every_view_carries_the_fixed_notice() -> None:
    assert present_day(DAY, report_for(ADULT, MaintenanceGoal())).notice == fixed_notice_text()
    missing = present_day(DAY, NutritionUnavailable(UnavailableReason.MISSING_WEIGHT))
    assert missing.notice == fixed_notice_text()
    for phrase in ("not medical", "pregnant", "eating disorder", "medication", "professional"):
        assert phrase in fixed_notice_text()


@pytest.mark.parametrize("reason", list(UnavailableReason))
def test_what_is_missing_says_what_and_how_to_complete_it(reason: UnavailableReason) -> None:
    view = present_day(DAY, NutritionUnavailable(reason))

    assert isinstance(view, UnavailableView)
    assert view.missing == unavailable_text(reason)
    assert view.missing.title
    assert view.missing.what
    assert view.missing.remedy
    assert view.missing.can_edit_profile is (reason is not UnavailableReason.UNDER_18)


def test_every_category_of_the_domain_has_a_text() -> None:
    assert all(advisory_text(a) for a in Advisory)
    assert all(slot_text(s) for s in MealSlot)
    assert all(energy_source_text(s) for s in EnergySource)
    assert all(load_level_text(level) for level in DayLoadLevel)
    assert all(activity_level_text(level) for level in ActivityLevel)
    assert goal_text(MaintenanceGoal()) == "Maintenance"


def test_every_food_of_the_table_has_a_name_to_translate() -> None:
    ids = {food.food_id for food in load_food_table().foods}

    assert set(FOOD_NAMES) == ids
    table = {food.food_id: food.name for food in load_food_table().foods}
    assert all(FOOD_NAMES[food_id] == name for food_id, name in table.items())


@pytest.fixture
def italian(tmp_path: Path, qapp: QCoreApplication) -> Iterator[None]:
    """The project's real Italian catalog installed for the test."""
    build_real_catalog(tmp_path)
    translator = QTranslator()
    assert translator.load(str(tmp_path / APP_CATALOG))
    qapp.installTranslator(translator)
    yield
    qapp.removeTranslator(translator)


def test_in_italian_the_day_speaks_italian_with_translated_moments_foods_and_notice(
    italian: None,
) -> None:
    view = present_day(DAY, report_for(ADULT, MaintenanceGoal(), (LONG_RIDE,)))

    assert isinstance(view, DayView)
    titles = [meal.title for meal in view.meals]
    assert any("Colazione" in title for title in titles)
    assert "Durante l'allenamento" in titles
    during = next(m for m in view.meals if m.title == "Durante l'allenamento")
    assert during.hourly.startswith("Indicativo: circa ")
    assert view.notice.startswith("Indicazioni generali, non consigli medici")
    assert view.level == "Allenamento molto intenso"
    assert any(
        "Riso" in example or "Pasta" in example or "Pane" in example
        for meal in view.meals
        for example in meal.examples
    )
    assert unavailable_text(UnavailableReason.MISSING_WEIGHT).title == "Nessun peso salvato"


def test_the_hourly_line_is_indicative_for_carbohydrate_fluid_and_sodium() -> None:
    view = present_day(DAY, report_for(ADULT, MaintenanceGoal(), (LONG_RIDE,)))

    assert isinstance(view, DayView)
    hourly = next(m.hourly for m in view.meals if m.hourly)
    assert hourly.startswith("Indicative: about ")
    assert hourly.endswith(" per hour")
    assert "Each hour" not in hourly


def test_the_deficit_advisories_do_not_judge_and_the_low_bmi_one_is_neutral_about_the_goal() -> (
    None
):
    assert advisory_text(Advisory.DEFICIT_NOT_APPLIED) == (
        "This app does not suggest a calorie deficit for this day."
    )
    assert "safe" not in advisory_text(Advisory.DEFICIT_NOT_APPLIED)
    assert "deficit" not in advisory_text(Advisory.LOW_BMI)
    assert "dietitian" in advisory_text(Advisory.LOW_BMI)
    limited = advisory_text(Advisory.DEFICIT_LIMITED)
    assert "reduced" in limited
    assert "cautious limits" in limited


def test_unverified_food_values_get_a_note_and_verified_ones_do_not() -> None:
    report = report_for(ADULT, MaintenanceGoal())

    unverified = present_day(DAY, report, foods_verified=False)
    verified = present_day(DAY, report, foods_verified=True)

    assert isinstance(unverified, DayView)
    assert isinstance(verified, DayView)
    assert unverified.foods_note == "Food values not yet verified against the source."
    assert verified.foods_note == ""


def test_under_18_has_no_numbers_and_points_to_a_doctor_or_a_sports_dietitian() -> None:
    view = present_day(DAY, NutritionUnavailable(UnavailableReason.UNDER_18))

    assert isinstance(view, UnavailableView)
    assert view.missing.can_edit_profile is False
    assert "Edit profile" not in view.missing.remedy
    assert view.missing.remedy == "Please talk to a doctor or a sports dietitian."


@pytest.mark.parametrize(
    "reason", [UnavailableReason.AGE_OUT_OF_RANGE, UnavailableReason.BEFORE_BIRTH]
)
def test_a_date_of_birth_out_of_range_invites_to_check_it(reason: UnavailableReason) -> None:
    view = present_day(DAY, NutritionUnavailable(reason))

    assert isinstance(view, UnavailableView)
    assert view.missing.remedy == "Check the date of birth with Edit profile."
    assert view.missing.can_edit_profile is True


def test_the_long_weight_loss_advisory_is_neutral_and_suggests_maintenance_periods() -> None:
    text = advisory_text(Advisory.LONG_WEIGHT_LOSS)

    assert text == (
        "This weight-loss period has lasted a long time. Long periods of weight loss are best "
        "alternated with periods of maintenance."
    )
    assert "doctor" not in text  # no medical judgement


def test_the_hourly_intakes_after_a_session_say_their_hour() -> None:
    nutrition = plan_day(
        DayInput(
            ADULT,
            MaintenanceGoal(),
            MealTimes.default(),
            (LONG_RIDE,),
            minutes_to_next_session=240.0,  # another session four hours after the end
        )
    )
    foods = load_food_table().foods
    report = DayNutritionReport(
        DAY,
        nutrition,
        MaintenanceGoal(),
        MealTimes.default(),
        tuple(MealSuggestion(m, suggest_examples(m, foods)) for m in nutrition.meals),
        (),
        0,
    )

    view = present_day(DAY, report)

    assert isinstance(view, DayView)
    hours = [card.title for card in view.meals if card.title.startswith("Refuel")]
    assert hours == [f"Refuel, hour {hour}" for hour in range(1, len(hours) + 1)]
    assert len(hours) >= 2
    assert refuel_hour_text(1) == "Refuel, hour 1"


def test_the_day_says_which_lean_mass_the_energy_availability_limit_uses() -> None:
    report = report_for(ADULT, MaintenanceGoal())

    unknown = present_day(DAY, report)
    estimated = present_day(DAY, report, lean_mass_measured=False)
    measured = present_day(DAY, report, lean_mass_measured=True)

    assert isinstance(unknown, DayView)
    assert isinstance(estimated, DayView)
    assert isinstance(measured, DayView)
    assert unknown.lean_mass_note == ""
    assert "estimate of your lean mass" in estimated.lean_mass_note
    assert "Nutrition settings" in estimated.lean_mass_note
    assert measured.lean_mass_note == (
        "The limit on energy availability uses your measured lean mass."
    )


def test_only_today_is_in_progress() -> None:
    report = report_for(ADULT, MaintenanceGoal())

    today = present_day(DAY, report, today=DAY)
    other = present_day(DAY, report, today=date(2026, 6, 11))
    unknown = present_day(DAY, report)

    assert isinstance(today, DayView)
    assert isinstance(other, DayView)
    assert isinstance(unknown, DayView)
    assert "not over yet" in today.in_progress_note
    assert "partial" in today.in_progress_note
    assert other.in_progress_note == ""
    assert unknown.in_progress_note == ""
