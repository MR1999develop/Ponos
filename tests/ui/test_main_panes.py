# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""The four panes of the main window: place, resizing, focus, and the commands without a menu."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from PySide6.QtCore import QLocale, QSettings, Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import QListWidget, QMenu
from pytestqt.qtbot import QtBot

from ponos_coach.ui.main_panes import Pane
from tests.ui.map_support import map_window
from tests.ui.workspace_support import compose, make_settings


@pytest.fixture(autouse=True)
def english_dates() -> Iterator[None]:
    """Month names in English, whatever the language of the test computer."""
    previous = QLocale()
    QLocale.setDefault(QLocale(QLocale.Language.English, QLocale.Country.UnitedStates))
    yield
    QLocale.setDefault(previous)


@pytest.fixture
def settings(tmp_path: Path) -> QSettings:
    return make_settings(tmp_path)


def test_the_four_panes_are_in_their_place_from_the_start(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = compose(tmp_path, settings)
    qtbot.addWidget(window)
    window.show()
    qtbot.waitExposed(window)
    panes = window.panes

    assert panes.orientation() == Qt.Orientation.Horizontal
    assert panes.left_column.orientation() == Qt.Orientation.Vertical
    assert panes.left_column.widget(0) is window.overview_page
    assert panes.left_column.widget(1) is window.calendar_page
    activities_pane = panes.right_column.widget(0)
    assert activities_pane is not None
    assert panes.right_column.widget(1) is window.map_page
    assert activities_pane.isAncestorOf(window.activities_page)
    assert activities_pane.isAncestorOf(window.map_ui.filter_bar)
    for pane in Pane:
        assert panes.pane(pane).isVisible()


def test_no_pane_can_be_collapsed_and_the_dividers_can_be_grabbed(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = compose(tmp_path, settings)
    qtbot.addWidget(window)
    window.show()
    qtbot.waitExposed(window)

    for splitter in (window.panes, window.panes.left_column, window.panes.right_column):
        assert not splitter.childrenCollapsible()
        assert splitter.handleWidth() >= 4
        splitter.setSizes([1, 100000])  # as far as the user can drag it
        assert all(size > 0 for size in splitter.sizes())


def test_the_panes_are_reached_from_the_keyboard_by_the_view_menu(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = compose(tmp_path, settings)
    qtbot.addWidget(window)
    window.show()
    qtbot.waitExposed(window)
    window.activateWindow()

    window.focus_pane(Pane.CALENDAR)
    assert window.focusWidget() is window.calendar_page.table
    view = next(m for m in window.menuBar().findChildren(QMenu) if m.title() == "View")
    assert [a.text() for a in view.actions()] == [
        "Go to Overview",
        "Go to Calendar",
        "Go to Activities",
        "Go to Map",
    ]
    assert all(not a.shortcut().isEmpty() for a in view.actions())


def test_the_focus_goes_to_the_table_of_the_activities_when_there_are_some(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = map_window(qtbot, tmp_path, settings)
    window.show()
    qtbot.waitExposed(window)
    window.activateWindow()

    window.focus_pane(Pane.ACTIVITIES)

    assert window.focusWidget() is window.activities_page.table


def test_the_commands_of_the_old_calendar_menu_keep_their_shortcuts_without_a_menu(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = compose(tmp_path, settings)
    qtbot.addWidget(window)
    window.show()
    qtbot.waitExposed(window)
    qtbot.waitActive(window)
    assert "Calendar" not in [a.text() for a in window.menuBar().actions()]
    page = window.calendar_page
    actions = window.calendar_actions
    # A key press needs an active window, which the offscreen platform does not give: what makes
    # a shortcut work is that the action is on a widget of the window, with the right context.
    for name, keys in (("previous", "Ctrl+Left"), ("next", "Ctrl+Right"), ("today", "Ctrl+T")):
        assert actions[name].shortcut() == QKeySequence(keys)
        assert actions[name] in page.actions()
        assert actions[name].shortcutContext() == Qt.ShortcutContext.WidgetWithChildrenShortcut
    actions["next"].trigger()  # and the command itself still moves the calendar
    actions["today"].trigger()
    assert page.title_text == "June 2026"
    race = window.plan.actions["add_race"]
    assert race in window.actions()  # Ctrl+R works from the whole window
    assert window.plan.actions["add_form_goal"] in window.actions()
    assert race.shortcut().toString() == "Ctrl+R"


def test_there_is_no_section_list(qtbot: QtBot, tmp_path: Path, settings: QSettings) -> None:
    window = compose(tmp_path, settings)
    qtbot.addWidget(window)

    assert window.findChild(QListWidget, "sidebar") is None
    assert not hasattr(window, "sidebar")
