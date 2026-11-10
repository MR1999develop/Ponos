# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""What fits inside the four panes (user's requests of 2026-11-09).

The Overview shows the PMC, or the weekly load above the time in zones, chosen on its left; the
map keeps a usable height; the calendar leaves no empty band under the month.
Made-up profile (no real person).
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QLabel, QListWidget, QPushButton
from pytestqt.qtbot import QtBot

from tests.ui.nutrition_support import window_with_days
from tests.ui.workspace_support import WAIT_MS, make_settings

MAP_MIN_HEIGHT_PX = 200


def test_the_overview_shows_the_pmc_or_the_two_weekly_charts_one_above_the_other(
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
        "Weekly load and time in zones",
    ]
    assert (
        charts.mapTo(page, charts.rect().topLeft()).x()
        < page.chart.mapTo(page, page.chart.rect().topLeft()).x()
    )  # the list is on the left of the chart
    assert charts.currentRow() == 0
    assert page.chart.isVisible()
    assert not page.load_chart.isVisible() and not page.zones_chart.isVisible()

    charts.setCurrentRow(1)

    assert page.load_chart.isVisible() and page.zones_chart.isVisible()
    assert not page.chart.isVisible()
    load_top = page.load_chart.mapTo(page, page.load_chart.rect().topLeft())
    zones_top = page.zones_chart.mapTo(page, page.zones_chart.rect().topLeft())
    assert load_top.x() == zones_top.x()
    assert load_top.y() + page.load_chart.height() <= zones_top.y()  # the load above the zones


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

    assert page.panel.height() == page.panel.minimumHeight()  # the month gets the rest


def test_the_map_has_no_activity_detail_button(qtbot: QtBot, tmp_path: Path) -> None:
    # The user's request of 2026-11-09: the list and Ctrl+D open the detail; no button here.
    window = window_with_days(qtbot, tmp_path, make_settings(tmp_path))

    texts = [b.text() for b in window.map_page.findChildren(QPushButton)]

    assert "Activity detail..." not in texts


def test_the_calendar_legend_is_one_row_with_the_no_colour_note_as_a_tooltip(
    qtbot: QtBot, tmp_path: Path
) -> None:
    # The user's request of 2026-11-09: the legend under the calendar takes less room.
    window = window_with_days(qtbot, tmp_path, make_settings(tmp_path))
    window.show()
    window.resize(1600, 900)
    qtbot.wait(50)
    page = window.calendar_page
    labels = page.findChildren(QLabel)
    goal = next(label for label in labels if label.text() == "Nutrition goal:")

    assert goal.toolTip() == "No colour: no goal period, maintenance assumed."
    assert not [label for label in labels if label.text().startswith("No colour")]
    legend = page.legend_widget
    assert legend.height() < 2 * goal.sizeHint().height()  # a single row
