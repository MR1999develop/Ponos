# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Activity detail window: no map, zone table without a scroll bar, compact summary."""

from __future__ import annotations

from zoneinfo import ZoneInfo

import plotly.graph_objects as go
from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QWidget
from pytestqt.qtbot import QtBot

from ponos_coach.load.domain import ZoneSeconds
from ponos_coach.ui.activity_detail import DetailReady
from ponos_coach.ui.activity_detail_dialog import ActivityDetailDialog
from ponos_coach.ui.chart_payload import PreparedChart
from ponos_coach.ui.chart_view import ChartEngine, ChartView
from ponos_coach.ui.tokens import Theme
from tests.ui.test_activity_detail import detail_with

MAX_HEIGHT_PX = 640  # before the rework the window asked for more than that
SUMMARY_KEYS = (
    "start", "moving", "elapsed", "distance", "ascent", "load", "source", "threshold", "limits",
)  # fmt: skip


def ready_dialog(qtbot: QtBot) -> ActivityDetailDialog:
    dialog = ActivityDetailDialog(
        ChartEngine(lambda _name: None), ZoneInfo("Europe/Rome"), theme=lambda: Theme.LIGHT
    )
    qtbot.addWidget(dialog)
    zones = ZoneSeconds((300.0, 100.0, 0.0, 0.0, 100.0))
    dialog.show_outcome(DetailReady(detail_with(zones), PreparedChart(go.Figure(), "", "#fff")))
    dialog.show()
    qtbot.waitExposed(dialog)
    for _ in range(5):
        QCoreApplication.processEvents()
    return dialog


def test_the_window_has_no_map(qtbot: QtBot) -> None:
    dialog = ready_dialog(qtbot)

    assert not hasattr(dialog, "track")
    assert not hasattr(dialog, "shows_track")
    assert dialog.findChild(QWidget, "detailNoGps") is None
    assert dialog.findChildren(ChartView) == [dialog.chart]


def test_the_zone_table_shows_every_row_without_a_vertical_scroll_bar(qtbot: QtBot) -> None:
    dialog = ready_dialog(qtbot)
    table = dialog.zone_table

    content = table.horizontalHeader().height() + sum(
        table.rowHeight(row) for row in range(table.model().rowCount())
    )
    assert table.height() >= content
    assert not table.verticalScrollBar().isVisible()


def test_the_window_is_lower_and_the_summary_sits_in_columns(qtbot: QtBot) -> None:
    dialog = ready_dialog(qtbot)

    assert dialog.sizeHint().height() <= MAX_HEIGHT_PX
    tops = {dialog.summary_value_widget(key).y() for key in SUMMARY_KEYS}
    assert len(tops) <= 3  # nine values in at most three rows


def test_warming_up_creates_the_chart_page_before_any_data_arrives(qtbot: QtBot) -> None:
    engine = ChartEngine(lambda _name: None)
    dialog = ActivityDetailDialog(engine, ZoneInfo("Europe/Rome"), theme=lambda: Theme.LIGHT)
    qtbot.addWidget(dialog)
    before = dialog.chart.engine_started

    dialog.warm_up_chart()

    assert (before, dialog.chart.engine_started) == (False, True)
    assert not dialog.shows_chart()  # still the "loading" message
    dialog.close()
    engine.dispose()
