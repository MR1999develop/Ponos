# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""The Calendar section: month navigation, what each past day shows, and opening a day.

From the Composition Root, with a made-up profile and activities in June 2026 (today is 30 June).
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest
from PySide6.QtCore import QLocale, QSettings, Qt
from PySide6.QtGui import QBrush
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QListWidget, QPushButton
from pytestqt.qtbot import QtBot

from ponos_coach.nutrition.domain import GoalPeriod, WeightLossGoal
from ponos_coach.ui.calendar_model import GoalKind
from ponos_coach.ui.calendar_page import CalendarPage
from ponos_coach.ui.tokens import Theme
from tests.ui.nutrition_support import (
    DAWN_AND_LUNCH_DAY,
    EMPTY_DAY,
    LONG_DAY,
    cell_of,
    open_day,
    window_with_days,
)
from tests.ui.workspace_support import compose, make_settings


@pytest.fixture
def settings(tmp_path: Path) -> QSettings:
    return make_settings(tmp_path)


@pytest.fixture(autouse=True)
def english_numbers() -> Iterator[None]:
    previous = QLocale()
    QLocale.setDefault(QLocale(QLocale.Language.English, QLocale.Country.UnitedStates))
    yield
    QLocale.setDefault(previous)


def test_the_sidebar_offers_the_calendar_and_opening_it_shows_the_current_month(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = compose(tmp_path, settings)
    qtbot.addWidget(window)
    sidebar = window.findChild(QListWidget, "sidebar")
    assert sidebar is not None

    item = sidebar.item(2)
    assert item.text() == "Calendar"
    assert item.flags() & Qt.ItemFlag.ItemIsEnabled
    sidebar.setCurrentRow(2)

    assert window.current_page is window.calendar_page
    assert window.calendar_page.title_text == "June 2026"


def test_each_past_day_shows_its_sports_and_load_and_a_day_without_activity_only_its_number(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = window_with_days(qtbot, tmp_path, settings)

    busy = cell_of(window, DAWN_AND_LUNCH_DAY)
    model = window.calendar_page.model
    index = model.index_of(DAWN_AND_LUNCH_DAY)
    lines = str(model.data(index)).split("\n")
    empty_index = model.index_of(EMPTY_DAY)

    assert busy.activity_count == 2
    assert lines[0] == "10"
    assert lines[1] == "Run"
    assert lines[2].endswith(" hrTSS")
    assert busy.load > 0
    assert str(model.data(empty_index)) == "11"
    assert "Activities: 2 (Running)" in str(model.data(index, Qt.ItemDataRole.ToolTipRole))
    assert "Running" in str(model.data(index, Qt.ItemDataRole.AccessibleDescriptionRole))
    assert cell_of(window, LONG_DAY).activity_count == 1


def test_navigation_by_buttons_moves_one_month_and_next_stops_a_year_ahead(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = window_with_days(qtbot, tmp_path, settings)
    window.sidebar.setCurrentRow(2)
    page = window.calendar_page

    assert page.next_button.isEnabled()  # the plan fills the months ahead
    page.next_button.click()
    assert page.title_text == "July 2026"
    page.previous_button.click()
    page.previous_button.click()
    assert page.title_text == "May 2026"
    assert page.current_month == date(2026, 5, 1)
    page.previous_button.click()
    page.previous_button.click()
    assert page.title_text == "March 2026"
    # No "Today" button on the page (user's request): Ctrl+T and the day menu bring today back.
    assert page.findChild(QPushButton, "calendarToday") is None
    page.go_today()
    assert page.title_text == "June 2026"
    assert page.selected_day() == date(2026, 6, 30)
    for _ in range(12):
        page.next_button.click()
    assert page.title_text == "June 2027"
    assert not page.next_button.isEnabled()
    assert page.next_button.toolTip() == "The calendar shows up to a year ahead."


def test_navigation_by_keyboard_uses_page_keys_and_the_menu_shortcuts(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = window_with_days(qtbot, tmp_path, settings)
    page = window.calendar_page
    actions = window.calendar_actions

    assert not actions["previous"].isEnabled()  # on the Overview
    assert actions["previous"].toolTip() == "Open the Calendar section first."
    window.sidebar.setCurrentRow(2)
    assert actions["previous"].isEnabled()
    assert actions["next"].isEnabled()
    assert actions["today"].isEnabled()

    QTest.keyClick(page.table, Qt.Key.Key_PageUp)
    assert page.title_text == "May 2026"
    actions["previous"].trigger()
    assert page.title_text == "April 2026"
    QTest.keyClick(page.table, Qt.Key.Key_PageDown)
    actions["next"].trigger()
    assert page.title_text == "June 2026"
    actions["previous"].trigger()
    actions["today"].trigger()
    assert page.title_text == "June 2026"
    actions["next"].trigger()
    assert page.title_text == "July 2026"


def test_arrow_keys_move_between_past_days_and_enter_opens_the_day(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = window_with_days(qtbot, tmp_path, settings)
    window.sidebar.setCurrentRow(2)
    page = window.calendar_page
    page.select_day(date(2026, 6, 10))

    QTest.keyClick(page.table, Qt.Key.Key_Right)
    assert page.selected_day() == date(2026, 6, 11)
    QTest.keyClick(page.table, Qt.Key.Key_Down)
    assert page.selected_day() == date(2026, 6, 18)
    with qtbot.waitSignal(page.day_requested, timeout=1000) as opened:
        QTest.keyClick(page.table, Qt.Key.Key_Return)

    assert opened.args == [date(2026, 6, 18)]
    assert window.current_page is window.calendar_page  # the day opens in a window of its own
    assert window.nutrition.day_window(date(2026, 6, 18)) is not None


def test_opening_a_day_leaves_the_calendar_in_place_and_there_is_no_back_command(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = window_with_days(qtbot, tmp_path, settings)
    open_day(qtbot, window, DAWN_AND_LUNCH_DAY)
    actions = window.calendar_actions

    assert "back" not in actions
    assert window.current_page is window.calendar_page
    assert actions["open_day"].isEnabled()
    assert window.sidebar.currentRow() == 2


def test_the_theme_changes_the_goal_tints_of_the_cells(qtbot: QtBot) -> None:
    page = CalendarPage(date(2026, 6, 15))
    qtbot.addWidget(page)
    page.set_periods([GoalPeriod(date(2026, 6, 1), None, WeightLossGoal(500))])
    index = page.model.index_of(date(2026, 6, 3))

    light = page.model.data(index, Qt.ItemDataRole.BackgroundRole)
    page.apply_theme(Theme.DARK)
    dark = page.model.data(index, Qt.ItemDataRole.BackgroundRole)
    cell = page.model.day_at(index.row(), index.column())

    assert isinstance(light, QBrush)
    assert isinstance(dark, QBrush)
    assert light.color().name() != dark.color().name()
    assert cell is not None
    assert cell.goal is GoalKind.WEIGHT_LOSS


# --- Days after today (they hold the plan) ------------------------------------------------------


def test_days_after_today_can_be_selected_but_have_no_day_page(qtbot: QtBot) -> None:
    page = CalendarPage(date(2026, 6, 15))
    qtbot.addWidget(page)
    requested: list[object] = []
    page.day_requested.connect(requested.append)
    future = page.model.index_of(date(2026, 6, 20))

    page.select_day(date(2026, 6, 20))
    page.table.activated.emit(future)
    page.request_selected_day()

    assert page.selected_day() == date(2026, 6, 20)
    assert page.model.flags(future) & Qt.ItemFlag.ItemIsEnabled
    assert requested == []  # the nutrition day page is for the days up to today
    assert "No plan for this day" in str(page.model.data(future, Qt.ItemDataRole.ToolTipRole))
    assert "No plan for this day." in page.panel.detail_text


def test_the_arrow_keys_reach_the_days_after_today_and_enter_opens_nothing_there(
    qtbot: QtBot,
) -> None:
    page = CalendarPage(date(2026, 6, 15))
    qtbot.addWidget(page)
    requested: list[object] = []
    page.day_requested.connect(requested.append)
    page.select_day(date(2026, 6, 15))

    QTest.keyClick(page.table, Qt.Key.Key_Right)
    QTest.keyClick(page.table, Qt.Key.Key_Down)
    QTest.keyClick(page.table, Qt.Key.Key_Return)

    assert page.selected_day() == date(2026, 6, 23)
    assert requested == []
