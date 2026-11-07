# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""The page of a day, from the Composition Root: activities, energy, meals, advisories, what is
missing, and T-UI-02 while the day is calculated.

Made-up profile and activities (no real person): 10 June has a run at 06:00 and one at 12:30,
11 June nothing, 12 June a three hour session. The profile's time zone is Europe/Rome.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest
from PySide6.QtCore import QLocale, QSettings, Qt
from PySide6.QtWidgets import QLabel, QTableView, QWidget
from pytestqt.qtbot import QtBot

from ponos_coach.load.domain import HeartRateLimits, Physiology, Sex, ThresholdHeartRate
from ponos_coach.nutrition.domain import Advisory, EnergySource
from ponos_coach.profiles.day_nutrition import NutritionUnavailable, UnavailableReason
from ponos_coach.profiles.domain import BirthDate, FixedProfileData, Height, ProfileName
from ponos_coach.profiles.use_cases import NewProfileRequest
from ponos_coach.ui.day_page import DayState
from ponos_coach.ui.day_presentation import present_day
from ponos_coach.ui.main_window import MainWindow
from ponos_coach.ui.nutrition_texts import (
    advisory_text,
    energy_source_text,
    fixed_notice_text,
)
from ponos_coach.ui.overview_state import Ready
from tests.ui.nutrition_support import (
    DAWN_AND_LUNCH_DAY,
    EMPTY_DAY,
    LONG_DAY,
    PROFILE,
    clock_minutes,
    open_day,
    plain,
    window_with_days,
)
from tests.ui.test_main_window import latency_of_one_event
from tests.ui.workspace_support import TODAY, WAIT_MS, compose, make_settings


@pytest.fixture
def settings(tmp_path: Path) -> QSettings:
    return make_settings(tmp_path)


@pytest.fixture(autouse=True)
def english_numbers() -> Iterator[None]:
    previous = QLocale()
    QLocale.setDefault(QLocale(QLocale.Language.English, QLocale.Country.UnitedStates))
    yield
    QLocale.setDefault(previous)


def all_label_texts(page: QWidget) -> list[str]:
    """Texts of every label inside a widget."""
    return [label.text() for label in page.findChildren(QLabel)]


def state_of(window: MainWindow) -> DayState:
    """The day page's state, read through a function (earlier checks narrow the type)."""
    return window.day_page.state


def test_a_day_with_a_run_at_dawn_and_one_at_lunch_lists_both_with_time_duration_load_and_energy(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = window_with_days(qtbot, tmp_path, settings)

    open_day(qtbot, window, DAWN_AND_LUNCH_DAY)
    page = window.day_page
    rows = page.activities_model.rows

    assert page.state is DayState.CONTENT
    assert "June 10, 2026" in page.title_text
    assert [(plain(r[0]), r[1], r[2]) for r in rows] == [
        ("6:00 AM", "Running", "0:30:00"),
        ("12:30 PM", "Running", "0:30:00"),
    ]
    assert all(row[3].isdigit() and int(row[3]) > 0 for row in rows)  # load
    assert all(row[4].isdigit() and int(row[4]) > 0 for row in rows)  # energy, kcal
    assert {row[5] for row in rows} == {energy_source_text(EnergySource.HEART_RATE)}
    assert page.activities_model.headerData(2, Qt.Orientation.Horizontal) == "Duration (h:mm:ss)"
    assert page.activities_model.headerData(4, Qt.Orientation.Horizontal) == "Energy (kcal)"


def test_the_totals_give_basal_metabolism_spending_deficit_target_and_macros(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = window_with_days(qtbot, tmp_path, settings)

    open_day(qtbot, window, DAWN_AND_LUNCH_DAY)
    totals = {row[0]: row[1] for row in window.day_page.totals_model.rows}

    assert list(totals) == [
        "Basal metabolism (kcal)",
        "Energy spent (kcal)",
        "Calorie deficit (kcal)",
        "Energy target (kcal)",
        "Carbohydrates (g)",
        "Protein (g)",
        "Fat (g)",
    ]
    assert totals["Calorie deficit (kcal)"] == "0"  # maintenance
    assert int(totals["Energy spent (kcal)"].replace(",", "")) > int(
        totals["Basal metabolism (kcal)"].replace(",", "")
    )
    assert totals["Energy target (kcal)"] == totals["Energy spent (kcal)"]
    assert window.day_page.summary_line.startswith("Goal: Maintenance. Day: ")


def test_the_meal_cards_come_in_time_order_with_amounts_and_a_moved_lunch(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = window_with_days(qtbot, tmp_path, settings)

    open_day(qtbot, window, DAWN_AND_LUNCH_DAY)
    page = window.day_page
    titles = page.meal_titles
    minutes = [clock_minutes(t) for t in titles]

    assert minutes == sorted(minutes)
    assert any("After training" in t for t in titles)
    assert any(
        "Moved from its usual time because of training." in page.meal_texts(i)
        for i in range(len(titles))
    )
    for index in range(len(titles)):
        texts = page.meal_texts(index)
        assert "Energy (kcal)" in texts
        assert "Carbohydrates (g)" in texts
        assert "Examples" in texts or "No food example matches these amounts." in texts
    assert any(" g" in text for text in page.meal_texts(0)[-1:])  # an example with its grams


def test_a_three_hour_session_has_a_during_training_card_with_the_hourly_amounts(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = window_with_days(qtbot, tmp_path, settings)

    open_day(qtbot, window, LONG_DAY)
    page = window.day_page
    during = [i for i, title in enumerate(page.meal_titles) if title.startswith("During training")]

    assert len(during) == 1
    texts = page.meal_texts(during[0])
    hourly = next(t for t in texts if t.startswith("Indicative: about "))
    assert "g of carbohydrate," in hourly
    assert "ml of fluid and" in hourly
    assert "mg of sodium per hour" in hourly
    assert any("Before training" in t for t in page.meal_titles)
    assert any("After training" in t for t in page.meal_titles)


def test_a_day_without_activities_is_a_rest_day_with_breakfast_lunch_and_dinner(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = window_with_days(qtbot, tmp_path, settings)

    open_day(qtbot, window, EMPTY_DAY)
    page = window.day_page

    assert page.activities_model.rowCount() == 0
    assert "No activities on this day." in all_label_texts(page)
    assert [t.split(" at ")[0] for t in page.meal_titles] == ["Breakfast", "Lunch", "Dinner"]
    assert [plain(t.split(" at ")[1]) for t in page.meal_titles] == [
        "7:30 AM",
        "1:00 PM",
        "8:00 PM",
    ]
    assert "Rest day" in page.summary_line


def test_the_advisories_and_the_fixed_notice_are_always_on_screen_with_the_guidance(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = window_with_days(qtbot, tmp_path, settings)

    open_day(qtbot, window, EMPTY_DAY)
    page = window.day_page
    notice = page.findChild(QLabel, "dayNotice")

    assert advisory_text(Advisory.ACTIVITY_LEVEL_ASSUMED) in page.advisory_texts  # no level yet
    assert page.advisories_visible()
    assert notice is not None
    assert notice.isVisibleTo(window)
    assert page.notice_text == fixed_notice_text()
    assert "not medical or personalised nutrition advice" in page.notice_text
    assert "pregnant" in page.notice_text


def test_a_profile_without_body_data_says_what_to_complete_and_opens_edit_profile(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = compose(tmp_path, settings)
    qtbot.addWidget(window)
    window.start()
    qtbot.waitUntil(lambda: window.new_profile_dialog is not None, timeout=WAIT_MS)
    dialog = window.new_profile_dialog
    assert dialog is not None
    dialog.reject()
    request = NewProfileRequest(
        ProfileName.parse(PROFILE),
        tmp_path / "profiles" / PROFILE,
        "Europe/Rome",
        Physiology(HeartRateLimits(50, 190), ThresholdHeartRate(171), Sex.FEMALE),
        TODAY,
    )  # like a profile from a previous version: without height, date of birth and weight
    assert window.controller.create_profile(request)
    qtbot.waitUntil(lambda: isinstance(window.controller.overview_state, Ready), timeout=WAIT_MS)

    open_day(qtbot, window, EMPTY_DAY)
    page = window.day_page

    assert page.state is DayState.UNAVAILABLE
    assert page.missing_texts == (
        "Height and date of birth are missing",
        "Nutrition guidance needs your height and date of birth.",
        "Complete it with Edit profile.",
    )
    assert page.notice_text == fixed_notice_text()
    qtbot.waitUntil(window.edit_profile_action.isEnabled, timeout=WAIT_MS)
    page.edit_profile_button.click()
    qtbot.waitUntil(lambda: window.profile_dialogs.edit_dialog is not None, timeout=WAIT_MS)


def test_a_profile_with_body_data_but_no_weight_asks_for_the_weight(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = compose(tmp_path, settings)
    qtbot.addWidget(window)
    window.start()
    qtbot.waitUntil(lambda: window.new_profile_dialog is not None, timeout=WAIT_MS)
    dialog = window.new_profile_dialog
    assert dialog is not None
    dialog.reject()
    request = NewProfileRequest(
        ProfileName.parse(PROFILE),
        tmp_path / "profiles" / PROFILE,
        "Europe/Rome",
        Physiology(HeartRateLimits(50, 190), ThresholdHeartRate(171), Sex.MALE),
        TODAY,
        fixed=FixedProfileData(Height(172), BirthDate.on(date(1990, 3, 4), TODAY)),
    )
    assert window.controller.create_profile(request)
    qtbot.waitUntil(lambda: isinstance(window.controller.overview_state, Ready), timeout=WAIT_MS)

    open_day(qtbot, window, EMPTY_DAY)

    assert window.day_page.missing_texts[0] == "No weight is saved"


def test_t_ui_02_the_ui_stays_responsive_while_a_day_is_calculated_and_leaving_drops_it(
    qtbot: QtBot, tmp_path: Path, settings: QSettings, gate: threading.Event
) -> None:
    gate.set()
    window = window_with_days(qtbot, tmp_path, settings, gate=gate)
    gate.clear()  # from here the decoding of an activity file waits

    open_day(qtbot, window, DAWN_AND_LUNCH_DAY, wait=False)

    assert window.day_page.state is DayState.LOADING
    assert latency_of_one_event(qtbot) < 0.1  # T-UI-02
    assert state_of(window) is DayState.LOADING
    window.calendar_actions["back"].trigger()  # leave while it is being calculated
    gate.set()
    qtbot.wait(300)  # the answer arrives and is dropped
    assert window.current_page is window.calendar_page
    assert state_of(window) is not DayState.CONTENT

    gate.clear()
    open_day(qtbot, window, DAWN_AND_LUNCH_DAY, wait=False)
    gate.set()
    qtbot.waitUntil(lambda: window.day_page.state is DayState.CONTENT, timeout=WAIT_MS)


def test_the_day_page_has_named_controls_in_reading_order(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = window_with_days(qtbot, tmp_path, settings)
    open_day(qtbot, window, DAWN_AND_LUNCH_DAY)
    page = window.day_page

    tables = page.findChildren(QTableView)

    assert page.back_button.text() == "Back to calendar"
    assert page.back_button.focusPolicy() != 0
    assert tables
    assert all(table.accessibleName() for table in tables)
    assert window.calendar_page.table.accessibleName() == "Calendar"


def test_an_empty_profile_has_a_calendar_but_cannot_open_nutrition_settings(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = compose(tmp_path, settings)
    qtbot.addWidget(window)

    assert not window.nutrition_action.isEnabled()
    assert window.nutrition_action.toolTip() == "Create or open a profile first."
    window.nutrition.open_day(EMPTY_DAY)  # no profile: nothing happens
    assert window.nutrition.current_day is None


def test_the_shipped_food_values_are_marked_as_not_yet_verified_above_the_examples(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = window_with_days(qtbot, tmp_path, settings)

    open_day(qtbot, window, DAWN_AND_LUNCH_DAY)

    assert window.day_page.foods_note_text == "Food values not yet verified against the source."
    assert "Food values not yet verified against the source." in all_label_texts(window.day_page)


def test_today_says_that_the_day_is_in_progress_and_a_past_day_does_not(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = window_with_days(qtbot, tmp_path, settings)

    open_day(qtbot, window, TODAY)
    today_note = window.day_page.in_progress_text
    today_labels = all_label_texts(window.day_page)
    open_day(qtbot, window, DAWN_AND_LUNCH_DAY)

    assert "not over yet" in today_note
    assert today_note in today_labels
    assert window.day_page.in_progress_text == ""


def test_a_day_of_a_minor_shows_no_numbers_and_no_edit_profile_button(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = window_with_days(qtbot, tmp_path, settings)
    page = window.day_page

    page.show_content(present_day(EMPTY_DAY, NutritionUnavailable(UnavailableReason.UNDER_18)))

    assert page.state is DayState.UNAVAILABLE
    assert page.missing_texts[2] == "Please talk to a doctor or a sports dietitian."
    assert page.edit_profile_button.isHidden()
    page.show_content(
        present_day(EMPTY_DAY, NutritionUnavailable(UnavailableReason.MISSING_WEIGHT))
    )
    assert not page.edit_profile_button.isHidden()
