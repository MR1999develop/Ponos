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
from PySide6.QtWidgets import QListWidget
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


def test_navigation_by_buttons_moves_one_month_and_next_stops_at_the_current_month(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = window_with_days(qtbot, tmp_path, settings)
    window.sidebar.setCurrentRow(2)
    page = window.calendar_page

    assert not page.next_button.isEnabled()  # the calendar shows the past only, for now
    assert page.next_button.toolTip()
    page.previous_button.click()
    assert page.title_text == "May 2026"
    assert page.current_month == date(2026, 5, 1)
    assert page.next_button.isEnabled()
    page.previous_button.click()
    page.previous_button.click()
    assert page.title_text == "March 2026"
    page.today_button.click()
    assert page.title_text == "June 2026"
    assert page.selected_day() == date(2026, 6, 30)


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
    assert not actions["next"].isEnabled()  # already at the current month
    assert actions["next"].toolTip()
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
    assert window.current_page is window.day_page
    assert window.nutrition.current_day == date(2026, 6, 18)


def test_double_click_on_a_past_day_opens_it_and_back_returns_to_the_calendar(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = window_with_days(qtbot, tmp_path, settings)
    open_day(qtbot, window, DAWN_AND_LUNCH_DAY)
    actions = window.calendar_actions

    assert actions["back"].isEnabled()
    assert not actions["open_day"].isEnabled()  # the day page is not the calendar
    actions["back"].trigger()

    assert window.current_page is window.calendar_page
    assert window.nutrition.current_day is None
    assert not actions["back"].isEnabled()
    assert actions["back"].toolTip() == "No day is open."
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


# --- Days after today (the plan arrives later) ---------------------------------------------------


def test_days_after_today_cannot_be_selected_or_opened(qtbot: QtBot) -> None:
    page = CalendarPage(date(2026, 6, 15))
    qtbot.addWidget(page)
    requested: list[object] = []
    page.day_requested.connect(requested.append)
    future = page.model.index_of(date(2026, 6, 20))

    page.select_day(date(2026, 6, 20))
    page.table.activated.emit(future)

    assert page.selected_day() is None
    assert not page.model.flags(future) & Qt.ItemFlag.ItemIsEnabled
    assert requested == []
    assert "Not available yet" in str(page.model.data(future, Qt.ItemDataRole.ToolTipRole))


def test_the_arrow_keys_never_land_on_a_day_after_today(qtbot: QtBot) -> None:
    page = CalendarPage(date(2026, 6, 15))
    qtbot.addWidget(page)
    page.select_day(date(2026, 6, 15))

    QTest.keyClick(page.table, Qt.Key.Key_Right)
    QTest.keyClick(page.table, Qt.Key.Key_Down)

    landed = page.selected_day()
    assert landed is not None
    assert landed <= date(2026, 6, 15)
