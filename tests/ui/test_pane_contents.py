# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""What fits inside the four panes (user's requests of 2026-11-09).

The Overview shows one chart at a time, chosen on its left; the map keeps a usable height; the
calendar leaves no empty band under the month.
Made-up profile (no real person).
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QListWidget, QPushButton
from pytestqt.qtbot import QtBot

from tests.ui.nutrition_support import window_with_days
from tests.ui.workspace_support import WAIT_MS, make_settings

MAP_MIN_HEIGHT_PX = 200


def test_the_overview_shows_one_chart_at_a_time_chosen_from_a_list_on_its_left(
    qtbot: QtBot, tmp_path: Path
) -> None:
    window = window_with_days(qtbot, tmp_path, make_settings(tmp_path))
    window.show()
    page = window.overview_page
    qtbot.waitUntil(lambda: page.current_widget() is page.charts_widget, timeout=WAIT_MS)
    charts = page.chart_list
    assert isinstance(charts, QListWidget)
    assert [charts.item(row).text() for row in range(charts.count())] == [
        "Fitness and fatigue",
        "Weekly load",
        "Time in zones",
    ]
    assert (
        charts.mapTo(page, charts.rect().topLeft()).x()
        < page.chart.mapTo(page, page.chart.rect().topLeft()).x()
    )  # the list is on the left of the chart
    assert charts.currentRow() == 0
    assert page.chart.isVisible()
    assert not page.load_chart.isVisible() and not page.zones_chart.isVisible()

    charts.setCurrentRow(2)

    assert page.zones_chart.isVisible()
    assert not page.chart.isVisible() and not page.load_chart.isVisible()


def test_the_map_keeps_a_usable_height_in_its_pane(qtbot: QtBot, tmp_path: Path) -> None:
    window = window_with_days(qtbot, tmp_path, make_settings(tmp_path))
    window.show()
    window.resize(1280, 720)  # a small screen: the map must not shrink to its detail chart
    qtbot.wait(50)

    assert window.map_page.map_view.height() >= MAP_MIN_HEIGHT_PX


def test_under_the_calendar_the_day_panel_takes_only_the_room_of_its_text(
    qtbot: QtBot, tmp_path: Path
) -> None:
    window = window_with_days(qtbot, tmp_path, make_settings(tmp_path))
    window.show()
    window.resize(1600, 900)
    qtbot.wait(50)
    page = window.calendar_page

    assert page.panel.height() <= 3 * page.table.height() // 10  # the month gets the room


def test_the_map_has_no_activity_detail_button(qtbot: QtBot, tmp_path: Path) -> None:
    # The user's request of 2026-11-09: the list and Ctrl+D open the detail; no button here.
    window = window_with_days(qtbot, tmp_path, make_settings(tmp_path))

    texts = [b.text() for b in window.map_page.findChildren(QPushButton)]

    assert "Activity detail..." not in texts
