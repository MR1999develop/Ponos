# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Activity detail window: no map, zone table without a scroll bar, compact summary."""

from __future__ import annotations

from zoneinfo import ZoneInfo

import plotly.graph_objects as go
import pytest
from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QWidget
from pytestqt.qtbot import QtBot

from ponos_coach.activities.domain import Sport
from ponos_coach.load.domain import DomainSeconds, ZoneSeconds
from ponos_coach.nutrition.domain import ActivityEnergy, EnergySource
from ponos_coach.ui.activity_detail import DetailReady
from ponos_coach.ui.activity_detail_dialog import ActivityDetailDialog
from ponos_coach.ui.chart_payload import PreparedChart
from ponos_coach.ui.chart_view import ChartEngine, ChartView
from ponos_coach.ui.tokens import Theme
from tests.ui.test_activity_detail import detail_with

MAX_HEIGHT_PX = 640  # before the rework the window asked for more than that
SUMMARY_KEYS = (
    "start", "moving", "elapsed", "distance", "ascent", "load", "source", "thresholds", "limits",
)  # fmt: skip


def ready_dialog(
    qtbot: QtBot,
    sport: Sport = Sport.CYCLING,
    distance_m: float | None = None,
    energy: ActivityEnergy | None = None,
) -> ActivityDetailDialog:
    dialog = ActivityDetailDialog(
        ChartEngine(lambda _name: None), ZoneInfo("Europe/Rome"), theme=lambda: Theme.LIGHT
    )
    qtbot.addWidget(dialog)
    zones = ZoneSeconds((300.0, 100.0, 0.0, 0.0, 100.0))
    domains = DomainSeconds(300.0, 100.0, 100.0)
    detail = detail_with(zones, domains, sport=sport, distance_m=distance_m, timer_s=3_000.0)
    dialog.show_outcome(DetailReady(detail, PreparedChart(go.Figure(), "", "#fff"), energy))
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


def test_the_domain_table_shows_its_three_rows_without_a_scroll_bar_beside_the_zones(
    qtbot: QtBot,
) -> None:
    dialog = ready_dialog(qtbot)
    table, zones = dialog.domain_table, dialog.zone_table

    content = table.horizontalHeader().height() + sum(
        table.rowHeight(row) for row in range(table.model().rowCount())
    )
    assert table.model().rowCount() == 3
    assert table.height() >= content
    assert not table.verticalScrollBar().isVisible()
    assert table.isVisibleTo(dialog)
    assert table.x() > zones.x() + zones.width() - 1  # beside the zones, not below them
    assert table.height() <= zones.height()  # three rows are lower than five: the window is not


def test_the_window_is_lower_and_the_summary_sits_in_columns(qtbot: QtBot) -> None:
    dialog = ready_dialog(qtbot)

    assert dialog.sizeHint().height() <= MAX_HEIGHT_PX
    tops = {dialog.summary_value_widget(key).y() for key in SUMMARY_KEYS}
    assert len(tops) <= 3  # nine values in at most three rows


def test_a_run_shows_its_average_pace_next_to_the_distance(qtbot: QtBot) -> None:
    dialog = ready_dialog(qtbot, Sport.RUNNING, distance_m=10_000.0)
    pace, distance = dialog.summary_value_widget("pace"), dialog.summary_value_widget("distance")

    assert pace.isVisibleTo(dialog)
    assert dialog.summary_text("pace") == "5:00"
    assert pace.y() == distance.y()
    assert dialog.sizeHint().height() <= MAX_HEIGHT_PX


def test_a_ride_has_no_pace_and_leaves_no_gap_in_the_summary(qtbot: QtBot) -> None:
    dialog = ready_dialog(qtbot, Sport.CYCLING, distance_m=10_000.0)
    distance, ascent = (
        dialog.summary_value_widget("distance"),
        dialog.summary_value_widget("ascent"),
    )

    assert not dialog.summary_value_widget("pace").isVisibleTo(dialog)
    assert ascent.y() == distance.y()
    assert ascent.x() > distance.x()  # the next pair takes the place of the pace


def test_the_active_calories_follow_the_ascent_and_a_declared_total_has_no_mark(
    qtbot: QtBot,
) -> None:
    dialog = ready_dialog(qtbot, energy=ActivityEnergy(700.0, 625.6, EnergySource.DECLARED))

    assert dialog.summary_value_widget("calories").isVisibleTo(dialog)
    assert dialog.summary_text("calories") == "626"


@pytest.mark.parametrize("source", [EnergySource.HEART_RATE, EnergySource.MET])
def test_an_estimated_total_is_shown_as_an_estimate(qtbot: QtBot, source: EnergySource) -> None:
    dialog = ready_dialog(qtbot, energy=ActivityEnergy(700.0, 420.4, source))

    assert dialog.summary_text("calories") == "≈ 420"


def test_without_the_body_data_the_calories_are_left_out_and_leave_no_gap(qtbot: QtBot) -> None:
    dialog = ready_dialog(qtbot, Sport.CYCLING, distance_m=10_000.0)
    load, ascent = dialog.summary_value_widget("load"), dialog.summary_value_widget("ascent")

    assert not dialog.summary_value_widget("calories").isVisibleTo(dialog)
    assert load.y() == ascent.y()  # a ride has neither pace nor calories: the load follows
    assert load.x() > ascent.x()


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
