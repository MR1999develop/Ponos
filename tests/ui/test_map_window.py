# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""T-MAP-02, T-MAP-04, T-MAP-06, T-FILT-04: the Map section in the real window.

Eight made-up runs, all starting at one invented point in the open sea. The map is driven through
the controller (the view the page would report).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import QSettings
from pytestqt.qtbot import QtBot

from ponos_coach.activities.domain import Sport
from ponos_coach.load.activity_profile import ActivityProfileData
from ponos_coach.ui.activity_filters import FilterTexts
from ponos_coach.ui.main_window import MainWindow
from tests.ui.map_support import (
    ACTIVITIES_WITH_POSITION,
    ELSEWHERE,
    FAR,
    NEAR,
    SECTION_ACTIVITIES,
    SECTION_MAP,
    map_window,
    show_view,
)
from tests.ui.workspace_support import WAIT_MS, make_settings


@pytest.fixture
def settings(tmp_path: Path) -> QSettings:
    return make_settings(tmp_path)


def listed(window: MainWindow) -> int:
    return window.map_page.list_model.rowCount()


def shown_in_table(window: MainWindow) -> int:
    return window.activities_page.proxy.rowCount()


def wait_search(qtbot: QtBot, window: MainWindow, count: int) -> None:
    qtbot.waitUntil(
        lambda: (
            window.map_ui.controller.search is not None
            and len(window.map_ui.controller.search.activities) == count
        ),
        timeout=WAIT_MS,
    )


def test_the_map_section_is_in_the_sidebar_and_lists_the_activities_of_the_area(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = map_window(qtbot, tmp_path, settings)

    show_view(qtbot, window, NEAR)

    assert window.sidebar.item(SECTION_MAP).text() == "Map"
    assert listed(window) == ACTIVITIES_WITH_POSITION
    count, totals, extra = window.map_page.summary_texts
    assert count == f"Activities in this area: {ACTIVITIES_WITH_POSITION}"
    assert " km " in totals
    assert "ascent" in totals
    assert extra == ""


def test_an_area_without_activities_says_so_and_how_to_find_them(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = map_window(qtbot, tmp_path, settings)

    show_view(qtbot, window, ELSEWHERE)

    assert listed(window) == 0
    count, totals, extra = window.map_page.summary_texts
    assert count == "Activities in this area: 0"
    assert totals == ""
    assert "No activity in this area" in extra


def test_zoomed_out_the_list_still_counts_the_starts_and_tells_why_there_are_no_tracks(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = map_window(qtbot, tmp_path, settings)

    show_view(qtbot, window, FAR)

    assert listed(window) == ACTIVITIES_WITH_POSITION
    assert "Zoom in to see the tracks" in window.map_page.summary_texts[2]


def test_the_list_follows_the_shared_filter_and_so_does_the_table(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = map_window(qtbot, tmp_path, settings)
    show_view(qtbot, window, NEAR)
    activities = window.activities_page.model.rowCount()

    window.map_ui.filters.apply(FilterTexts(date_from="2026-05-04"))

    wait_search(qtbot, window, 5)  # runs of 4 to 8 May (day 3 to 7 after the first)
    qtbot.waitUntil(lambda: shown_in_table(window) == 5, timeout=WAIT_MS)
    qtbot.waitUntil(lambda: listed(window) == 5, timeout=WAIT_MS)
    assert window.activities_page.model.rowCount() == activities  # the model keeps every row
    assert window.activities_page.count_text == f"5 of {activities} activities"


def test_selecting_in_the_table_selects_on_the_map_and_the_other_way_round(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = map_window(qtbot, tmp_path, settings)
    show_view(qtbot, window, NEAR)
    controller = window.map_ui.controller
    page = window.map_page

    window.sidebar.setCurrentRow(SECTION_ACTIVITIES)
    window.activities_page.table.selectRow(2)
    chosen = window.activities_page.selected_activity()
    assert chosen is not None
    assert controller.selected == chosen.identity
    row = page.table.selectionModel().selectedRows()
    assert row
    assert page.list_model.identity_at(page.list_proxy.mapToSource(row[0]).row()) == chosen.identity

    # Clicking another row of the map's list selects it in the table too.
    page.table.selectRow(0)
    other = controller.selected
    assert other is not None
    assert other != chosen.identity
    now_selected = window.activities_page.selected_activity()
    assert now_selected is not None
    assert now_selected.identity == other


def test_the_page_receives_the_selected_activity_by_position_in_the_search(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = map_window(qtbot, tmp_path, settings)
    show_view(qtbot, window, NEAR)
    controller = window.map_ui.controller
    search = controller.search
    assert search is not None

    controller.select_index(3)

    assert controller.selected == search.activities[3].identity
    assert controller.index_of(search.activities[3].identity) == 3


def test_a_selection_outside_the_list_changes_nothing(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = map_window(qtbot, tmp_path, settings)
    controller = window.map_ui.controller

    for wrong in (-1, 8, 10**9, True, "3", 2.5, None):
        controller.select_index(wrong)

    assert controller.selected is None


def test_the_selected_activity_shows_its_profile_below_the_map(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = map_window(qtbot, tmp_path, settings)
    show_view(qtbot, window, NEAR)
    profiles: list[object] = []
    window.map_ui.controller.profile_changed.connect(profiles.append)

    window.map_ui.controller.select_index(0)

    qtbot.waitUntil(
        lambda: any(isinstance(p, ActivityProfileData) for p in profiles), timeout=WAIT_MS
    )
    profile = next(p for p in profiles if isinstance(p, ActivityProfileData))
    assert len(profile) > 10
    assert profile.altitude_m is not None
    title, _ = window.map_page.detail_texts
    assert "Running" in title
    assert window.map_page.shows_chart


def test_an_activity_removed_by_the_filter_is_no_longer_selected(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = map_window(qtbot, tmp_path, settings)
    show_view(qtbot, window, NEAR)
    controller = window.map_ui.controller
    controller.select_index(0)  # the oldest run
    assert controller.selected is not None

    window.map_ui.filters.apply(FilterTexts(sports=frozenset({Sport.CYCLING})))

    wait_search(qtbot, window, 0)
    assert controller.selected is None
