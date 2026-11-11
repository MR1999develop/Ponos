# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Activity detail: the speed or pace chart under the HR chart, in the real window.

Acceptance: a run with distance shows its pace in min/km on an upside-down axis, a ride shows
km/h; a file without distance or speed shows no chart and says why. The files are synthetic.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pytest
from PySide6.QtCore import QSettings, Qt
from PySide6.QtTest import QTest
from pytestqt.qtbot import QtBot

from ponos_coach.activities.domain import DistanceSeries, smoothed_speed
from ponos_coach.load.domain import ZoneSeconds
from ponos_coach.ui.activity_detail import DetailReady
from ponos_coach.ui.activity_detail_dialog import ActivityDetailDialog
from ponos_coach.ui.chart_view import ChartEngine, ValueRange
from ponos_coach.ui.hr_figure import HeartRateLabels, prepare_heart_rate_chart
from ponos_coach.ui.main_window import MainWindow
from ponos_coach.ui.speed_figure import SpeedLabels, prepare_speed_chart
from ponos_coach.ui.tokens import Theme, tokens_for
from tests.fixtures.generators import fit_writer as fw
from tests.fixtures.generators.tracks import SEA_LAT_DEG, SEA_LON_DEG
from tests.ui.helpers import CHROME
from tests.ui.test_activity_detail import detail_with
from tests.ui.test_activity_detail_window import (
    first_row_index,
    open_detail,
    show_activities_section,
    window_with_activities,
)
from tests.ui.workspace_support import (
    WAIT_MS,
    compose,
    create_profile,
    make_settings,
    wait_ready,
)

START = datetime(2026, 5, 3, 7, 0, tzinfo=UTC)
PROFILE = "Atleta di prova"
RUN_PACE_S_PER_KM = 300.0  # 5:00 per km
RIDE_SPEED_M_S = 8.0  # 28.8 km/h
STEP_S = 10
SECONDS = 900


@pytest.fixture
def settings(tmp_path: Path) -> QSettings:
    return make_settings(tmp_path)


def fit_with_distance(sport: fw.Sport, sub_sport: fw.SubSport, speed_m_s: float) -> bytes:
    """A 15 minute activity at a constant speed, with HR, position and cumulative distance."""
    first = fw.datetime_to_fit(START)
    samples = [
        fw.Sample(
            first + i * STEP_S,
            140 + i % 10,
            lat_deg=SEA_LAT_DEG + i * 0.0001,
            lon_deg=SEA_LON_DEG,
            altitude_m=10.0,
            distance_m=round(speed_m_s * i * STEP_S, 2),
        )
        for i in range(SECONDS // STEP_S + 1)
    ]
    return fw.build_activity(fw.ActivitySpec(START, sport, sub_sport, samples))


def window_with_one_activity(
    qtbot: QtBot, tmp_path: Path, settings: QSettings, data: bytes
) -> MainWindow:
    window = compose(tmp_path, settings)
    qtbot.addWidget(window)
    window.start()
    create_profile(qtbot, window, PROFILE)
    folder = tmp_path / "to-import"
    folder.mkdir()
    (folder / "activity.fit").write_bytes(data)
    with qtbot.waitSignal(window.controller.import_finished, timeout=WAIT_MS):
        window.import_folder(folder)
    wait_ready(qtbot, window, 1)
    return window


def open_first_detail(qtbot: QtBot, window: MainWindow) -> ActivityDetailDialog:
    table = window.activities_page.table
    show_activities_section(window)
    qtbot.waitExposed(window)
    table.selectRow(0)
    rect = table.visualRect(first_row_index(window))
    QTest.mouseClick(table.viewport(), Qt.MouseButton.LeftButton, pos=rect.center())
    QTest.mouseDClick(table.viewport(), Qt.MouseButton.LeftButton, pos=rect.center())
    dialog = open_detail(qtbot, window)
    qtbot.waitUntil(dialog.shows_chart, timeout=WAIT_MS)
    return dialog


# --- Acceptance: the window of a run and of a ride ----------------------------------------


def test_the_detail_of_a_run_with_distance_shows_the_pace_in_min_per_km_upside_down(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    data = fit_with_distance(fw.Sport.RUNNING, fw.SubSport.GENERIC, 1000.0 / RUN_PACE_S_PER_KM)
    window = window_with_one_activity(qtbot, tmp_path, settings, data)

    dialog = open_first_detail(qtbot, window)

    qtbot.waitUntil(lambda: dialog.speed_chart.current_figure is not None, timeout=WAIT_MS)
    assert dialog.shows_speed_chart()
    figure = dialog.speed_chart.current_figure
    assert figure is not None
    assert figure.layout.yaxis.title.text == "Pace (min/km)"
    assert figure.layout.xaxis.title.text == "Time (min)"
    start, end = figure.layout.yaxis.range
    assert start > end  # upside down: faster is higher
    assert "5:00" in figure.layout.yaxis.ticktext
    np.testing.assert_allclose(figure.data[0].y, RUN_PACE_S_PER_KM, atol=0.5)
    hr = dialog.chart.current_figure
    assert hr is not None
    assert hr.layout.xaxis.title.text == figure.layout.xaxis.title.text  # the same time axis
    assert dialog.notice_text == ""  # nothing to excuse


def test_the_detail_of_a_ride_with_distance_shows_the_speed_in_km_per_h(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    data = fit_with_distance(fw.Sport.CYCLING, fw.SubSport.ROAD, RIDE_SPEED_M_S)
    window = window_with_one_activity(qtbot, tmp_path, settings, data)

    dialog = open_first_detail(qtbot, window)

    qtbot.waitUntil(lambda: dialog.speed_chart.current_figure is not None, timeout=WAIT_MS)
    figure = dialog.speed_chart.current_figure
    assert figure is not None
    assert figure.layout.yaxis.title.text == "Speed (km/h)"
    np.testing.assert_allclose(figure.data[0].y, RIDE_SPEED_M_S * 3.6, rtol=0.01)
    assert figure.layout.yaxis.range is None  # not upside down


def test_without_distance_or_speed_there_is_no_chart_and_a_line_says_why(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = window_with_activities(qtbot, tmp_path, settings)  # runs without distance

    dialog = open_first_detail(qtbot, window)

    assert dialog.shows_chart()  # the HR chart is there
    assert not dialog.shows_speed_chart()
    assert dialog.speed_chart.current_figure is None
    assert dialog.notice_text == (
        "This activity has no distance or speed data, so there is no pace chart."
    )
    assert not dialog.speed_chart.isVisibleTo(dialog)  # hidden, so it leaves no gap


# --- The dialog with prepared charts -------------------------------------------------------


def dialog_with(qtbot: QtBot, *, hr: bool, speed: bool) -> ActivityDetailDialog:
    run = DistanceSeries(np.arange(0.0, 301.0), 3.0 * np.arange(0.0, 301.0))
    series = smoothed_speed(run)
    assert series is not None
    dialog = ActivityDetailDialog(
        ChartEngine(lambda _name: None), ZoneInfo("Europe/Rome"), theme=lambda: Theme.LIGHT
    )
    qtbot.addWidget(dialog)
    labels = SpeedLabels("Time", "Speed", "Pace", CHROME)
    detail = replace(
        detail_with(ZoneSeconds((300.0, 0.0, 0.0, 0.0, 0.0))), speed=series if speed else None
    )

    hr_chart = None
    if hr:
        assert detail.trace is not None
        hr_chart = prepare_heart_rate_chart(
            detail.trace,
            detail.limits,
            detail.zone_starts_bpm,
            HeartRateLabels("Time", "Heart rate", "Heart rate", "Discarded", CHROME),
            tokens_for(Theme.LIGHT),
        )
    speed_chart = (
        prepare_speed_chart(series, detail.sport, labels, tokens_for(Theme.LIGHT))
        if speed
        else None
    )
    dialog.show()
    dialog.show_outcome(DetailReady(detail, hr_chart, None, speed_chart))
    return dialog


def test_both_charts_show_when_there_is_heart_rate_and_speed(qtbot: QtBot) -> None:
    dialog = dialog_with(qtbot, hr=True, speed=True)

    assert dialog.shows_chart()
    assert dialog.shows_speed_chart()
    assert dialog.speed_chart.parent() is dialog.chart.parent()  # one under the other


def test_without_heart_rate_the_speed_chart_still_shows_and_the_notice_says_so(
    qtbot: QtBot,
) -> None:
    dialog = dialog_with(qtbot, hr=False, speed=True)

    assert dialog.shows_chart_page()
    assert not dialog.shows_chart()
    assert dialog.shows_speed_chart()
    assert dialog.notice_text.startswith("This activity has no heart-rate samples")


def test_zooming_one_chart_moves_the_other_without_an_echo(qtbot: QtBot) -> None:
    dialog = dialog_with(qtbot, hr=True, speed=True)
    scripts: dict[str, list[str]] = {"speed": [], "hr": []}
    dialog.speed_chart.run_script = scripts["speed"].append  # type: ignore[method-assign, assignment]
    dialog.chart.run_script = scripts["hr"].append  # type: ignore[method-assign, assignment]

    dialog.chart.x_range_changed.emit(ValueRange(1.0, 4.5))
    dialog.speed_chart.x_range_changed.emit(None)

    assert scripts["speed"] == ["ponosSetXRange(1.0, 4.5);"]
    assert scripts["hr"] == ["ponosSetXRange(null, null);"]
