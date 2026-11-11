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
from functools import partial
from pathlib import Path

import pytest
from PySide6.QtCore import QLocale, QSettings, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLabel, QScrollArea, QTableView, QWidget
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
    last_day_page,
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
    return last_day_page(window).state


def test_a_day_with_a_run_at_dawn_and_one_at_lunch_lists_both_with_time_duration_load_and_energy(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = window_with_days(qtbot, tmp_path, settings)

    open_day(qtbot, window, DAWN_AND_LUNCH_DAY)
    page = last_day_page(window)
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
    totals = {row[0]: row[1] for row in last_day_page(window).totals_model.rows}

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
    assert last_day_page(window).summary_line.startswith("Goal: Maintenance. Day: ")


def test_every_row_of_the_day_tables_is_on_screen_even_with_a_larger_font(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = window_with_days(qtbot, tmp_path, settings)
    font = window.font()
    font.setPointSizeF(font.pointSizeF() * 1.5)  # as with display scaling or a large system font
    window.setFont(font)
    window.show()
    qtbot.waitExposed(window)

    open_day(qtbot, window, DAWN_AND_LUNCH_DAY)

    tables = last_day_page(window).findChildren(QTableView)
    assert tables

    def last_row_inside(table: QTableView) -> bool:
        rows = table.model().rowCount()
        if rows == 0:
            return True
        last = table.visualRect(table.model().index(rows - 1, 0))
        return last.height() > 0 and last.bottom() < table.viewport().height()

    for table in tables:
        qtbot.waitUntil(partial(last_row_inside, table), timeout=WAIT_MS)


def test_the_meal_cards_come_in_time_order_with_amounts_and_a_moved_lunch(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = window_with_days(qtbot, tmp_path, settings)

    open_day(qtbot, window, DAWN_AND_LUNCH_DAY)
    page = last_day_page(window)
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
        assert "Protein (g)" in texts and "Fat (g)" in texts
        assert "Examples" not in texts  # energy and macronutrients only, no example foods


def test_a_three_hour_session_has_a_during_training_card_with_the_hourly_amounts(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = window_with_days(qtbot, tmp_path, settings)

    open_day(qtbot, window, LONG_DAY)
    page = last_day_page(window)
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
    page = last_day_page(window)

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
    page = last_day_page(window)
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
    page = last_day_page(window)

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

    assert last_day_page(window).missing_texts[0] == "No weight is saved"


def test_t_ui_02_the_ui_stays_responsive_while_a_day_is_calculated_and_leaving_drops_it(
    qtbot: QtBot, tmp_path: Path, settings: QSettings, gate: threading.Event
) -> None:
    gate.set()
    window = window_with_days(qtbot, tmp_path, settings, gate=gate)
    gate.clear()  # from here the decoding of an activity file waits

    open_day(qtbot, window, DAWN_AND_LUNCH_DAY, wait=False)

    assert last_day_page(window).state is DayState.LOADING
    assert latency_of_one_event(qtbot) < 0.1  # T-UI-02
    assert state_of(window) is DayState.LOADING
    day_window = window.nutrition.day_window(DAWN_AND_LUNCH_DAY)
    assert day_window is not None
    day_window.close()  # leave while it is being calculated
    assert window.nutrition.day_windows == ()
    with qtbot.assertNotEmitted(window.nutrition.loader.day_loaded, wait=300):
        gate.set()  # the answer arrives and is dropped

    gate.clear()
    open_day(qtbot, window, DAWN_AND_LUNCH_DAY, wait=False)
    gate.set()
    qtbot.waitUntil(lambda: last_day_page(window).state is DayState.CONTENT, timeout=WAIT_MS)


def test_the_day_page_has_named_controls_in_reading_order(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = window_with_days(qtbot, tmp_path, settings)
    open_day(qtbot, window, DAWN_AND_LUNCH_DAY)
    page = last_day_page(window)

    tables = page.findChildren(QTableView)

    assert tables
    assert all(table.accessibleName() for table in tables)
    assert window.calendar_page.table.accessibleName() == "Calendar"


def test_an_empty_profile_has_a_calendar_and_no_day_window(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = compose(tmp_path, settings)
    qtbot.addWidget(window)

    window.nutrition.open_day(EMPTY_DAY)  # no profile: nothing happens
    assert window.nutrition.day_windows == ()


def test_the_meals_show_energy_and_macronutrients_but_no_food_examples(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = window_with_days(qtbot, tmp_path, settings)

    open_day(qtbot, window, DAWN_AND_LUNCH_DAY)

    texts = all_label_texts(last_day_page(window))
    meal = last_day_page(window).meal_texts(0)
    assert "Energy (kcal)" in meal and "Protein (g)" in meal
    assert "Examples" not in texts
    assert "No food example matches these amounts." not in texts
    assert "Food values not yet verified against the source." not in texts


def test_today_says_that_the_day_is_in_progress_and_a_past_day_does_not(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = window_with_days(qtbot, tmp_path, settings)

    open_day(qtbot, window, TODAY)
    today_note = last_day_page(window).in_progress_text
    today_labels = all_label_texts(last_day_page(window))
    open_day(qtbot, window, DAWN_AND_LUNCH_DAY)

    assert "not over yet" in today_note
    assert today_note in today_labels
    assert last_day_page(window).in_progress_text == ""


def test_a_day_of_a_minor_shows_no_numbers_and_no_edit_profile_button(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = window_with_days(qtbot, tmp_path, settings)
    page = open_day(qtbot, window, EMPTY_DAY)

    page.show_content(present_day(EMPTY_DAY, NutritionUnavailable(UnavailableReason.UNDER_18)))

    assert page.state is DayState.UNAVAILABLE
    assert page.missing_texts[2] == "Please talk to a doctor or a sports dietitian."
    assert page.edit_profile_button.isHidden()
    page.show_content(
        present_day(EMPTY_DAY, NutritionUnavailable(UnavailableReason.MISSING_WEIGHT))
    )
    assert not page.edit_profile_button.isHidden()


def test_every_day_opens_in_a_window_of_its_own_with_its_own_content(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = window_with_days(qtbot, tmp_path, settings)

    first = open_day(qtbot, window, DAWN_AND_LUNCH_DAY)
    second = open_day(qtbot, window, EMPTY_DAY)

    windows = window.nutrition.day_windows
    assert [w.day for w in windows] == [DAWN_AND_LUNCH_DAY, EMPTY_DAY]
    assert windows[0].page is first and windows[1].page is second
    assert windows[0].windowTitle() == first.title_text != windows[1].windowTitle()
    assert first.state is DayState.CONTENT
    assert first.activities_model.rows != second.activities_model.rows
    assert all(w.isVisible() and not w.isModal() for w in windows)


def test_opening_a_day_again_brings_its_window_forward_instead_of_opening_a_second(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = window_with_days(qtbot, tmp_path, settings)
    open_day(qtbot, window, DAWN_AND_LUNCH_DAY)

    open_day(qtbot, window, DAWN_AND_LUNCH_DAY)

    assert len(window.nutrition.day_windows) == 1


def test_closing_one_day_window_leaves_the_other_open_and_working(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = window_with_days(qtbot, tmp_path, settings)
    open_day(qtbot, window, DAWN_AND_LUNCH_DAY)
    open_day(qtbot, window, EMPTY_DAY)
    closing = window.nutrition.day_window(DAWN_AND_LUNCH_DAY)
    assert closing is not None

    closing.close()

    assert [w.day for w in window.nutrition.day_windows] == [EMPTY_DAY]
    kept = window.nutrition.day_windows[0]
    with qtbot.waitSignal(window.nutrition.loader.day_loaded, timeout=WAIT_MS):
        window.nutrition.reload_days()  # the surviving day is calculated again and still shows
    assert kept.isVisible()


def test_new_day_windows_cascade_and_a_data_change_calculates_every_open_day_again(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = window_with_days(qtbot, tmp_path, settings)
    open_day(qtbot, window, DAWN_AND_LUNCH_DAY)
    open_day(qtbot, window, EMPTY_DAY)
    first, second = window.nutrition.day_windows

    assert second.pos() != first.pos()
    with qtbot.waitSignals(
        [window.nutrition.loader.day_loaded, window.nutrition.loader.day_loaded], timeout=WAIT_MS
    ):
        window.nutrition.data_changed()


def test_another_profile_closes_every_day_window(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = window_with_days(qtbot, tmp_path, settings)
    open_day(qtbot, window, DAWN_AND_LUNCH_DAY)
    open_day(qtbot, window, LONG_DAY)

    window.nutrition.profile_changed()

    assert window.nutrition.day_windows == ()


def test_closing_the_application_closes_the_day_windows(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = window_with_days(qtbot, tmp_path, settings)
    open_day(qtbot, window, DAWN_AND_LUNCH_DAY)

    window.nutrition.close_dialogs()

    assert window.nutrition.day_windows == ()


def test_clicking_an_activity_of_the_day_opens_its_window_and_a_second_click_brings_it_forward(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = window_with_days(qtbot, tmp_path, settings)
    open_day(qtbot, window, DAWN_AND_LUNCH_DAY)
    page = last_day_page(window)
    table = page.findChild(QTableView, "Day activities")
    assert table is not None

    def click_row(row: int) -> None:
        rect = table.visualRect(table.model().index(row, 1))
        QTest.mouseClick(table.viewport(), Qt.MouseButton.LeftButton, pos=rect.center())

    click_row(0)
    qtbot.waitUntil(lambda: len(window.profile_dialogs.detail_dialogs) == 1, timeout=WAIT_MS)
    first = window.profile_dialogs.detail_dialogs[0]

    click_row(1)
    qtbot.waitUntil(lambda: len(window.profile_dialogs.detail_dialogs) == 2, timeout=WAIT_MS)

    click_row(0)  # already open: no third window
    assert window.profile_dialogs.detail_dialogs[0] is first
    assert len(window.profile_dialogs.detail_dialogs) == 2


def test_the_day_window_opens_showing_everything_without_a_scroll_bar(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    # The user's request of 2026-11-10: the window of a day grows to its content (within the
    # screen), so it opens with no scroll bar.
    window = window_with_days(qtbot, tmp_path, settings)

    page = open_day(qtbot, window, DAWN_AND_LUNCH_DAY)
    day_window = window.nutrition.day_window(DAWN_AND_LUNCH_DAY)
    assert day_window is not None
    qtbot.waitExposed(day_window)
    qtbot.wait(50)

    scroll = page.findChild(QScrollArea, "dayScroll")
    assert isinstance(scroll, QScrollArea)
    assert not scroll.verticalScrollBar().isVisible()
    assert not scroll.horizontalScrollBar().isVisible()
    assert page.content_overflow().height() == 0
