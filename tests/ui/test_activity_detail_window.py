# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Activity detail from the table and overview charts, in the real window.

We start from the window composed as `main()` does; the data are the eight synthetic runs of the
test folder (no real person). T-UI-02 also holds while the charts are being built.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from PySide6.QtCore import QModelIndex, QSettings, Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QTableView
from pytestqt.qtbot import QtBot
from shiboken6 import isValid

from ponos_coach.load.domain import ZoneSeconds
from ponos_coach.profiles.domain import PROFILE_ARCHIVE_DIR
from ponos_coach.ui import activity_detail_loader, overview_page
from ponos_coach.ui.activity_detail import DetailReady
from ponos_coach.ui.activity_detail_dialog import ActivityDetailDialog
from ponos_coach.ui.chart_view import ChartEngine
from ponos_coach.ui.hr_figure import prepare_heart_rate_chart
from ponos_coach.ui.load_figures import prepare_weekly_zones_chart
from ponos_coach.ui.main_window import MainWindow
from ponos_coach.ui.overview_state import Ready
from ponos_coach.ui.tokens import Theme
from tests.ui.test_activity_detail import detail_with
from tests.ui.workspace_support import (
    FILES_IN_FIXTURE_FOLDER,
    WAIT_MS,
    close_window,
    compose,
    create_profile,
    make_import_folder,
    make_settings,
    wait_ready,
)

PROFILE = "Atleta di prova"
DASH = chr(0x2013)  # the separator of a bpm range
BPM_RANGES = ((50, 133), (134, 148), (149, 160), (161, 170), (171, 190))


@pytest.fixture
def settings(tmp_path: Path) -> QSettings:
    return make_settings(tmp_path)


def window_with_activities(
    qtbot: QtBot, tmp_path: Path, settings: QSettings, *, gate: threading.Event | None = None
) -> MainWindow:
    window = compose(tmp_path, settings, gate=gate)
    qtbot.addWidget(window)
    window.start()
    create_profile(qtbot, window, PROFILE)
    with qtbot.waitSignal(window.controller.import_finished, timeout=WAIT_MS):
        window.import_folder(make_import_folder(tmp_path))
    if gate is None:
        wait_ready(qtbot, window, FILES_IN_FIXTURE_FOLDER)
    return window


def open_detail(qtbot: QtBot, window: MainWindow) -> ActivityDetailDialog:
    qtbot.waitUntil(lambda: window.profile_dialogs.detail_dialog is not None, timeout=WAIT_MS)
    dialog = window.profile_dialogs.detail_dialog
    assert dialog is not None
    return dialog


def latency_of_one_event(qtbot: QtBot) -> float:
    started = time.monotonic()
    handled: list[float] = []
    QTimer.singleShot(0, lambda: handled.append(time.monotonic() - started))
    qtbot.waitUntil(lambda: bool(handled), timeout=1000)
    return handled[0]


def show_activities_section(window: MainWindow) -> None:
    """Show the window: the table of the activities is one of its four panes."""
    window.show()


def first_row_index(window: MainWindow) -> QModelIndex:
    table = window.activities_page.table
    return table.model().index(0, 0)


# --- Overview charts ------------------------------------------------------------------


def test_the_overview_shows_three_charts_pmc_weekly_load_and_zones(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = window_with_activities(qtbot, tmp_path, settings)
    page = window.overview_page

    qtbot.waitUntil(
        lambda: (
            page.zones_chart.current_figure is not None
            and page.load_chart.current_figure is not None
            and page.chart.current_figure is not None
        ),
        timeout=WAIT_MS,
    )

    assert page.current_widget() is page.charts_widget
    load = page.load_chart.current_figure
    zones = page.zones_chart.current_figure
    assert load is not None
    assert zones is not None
    assert [t.name for t in load.data] == ["Running"]
    assert load.layout.yaxis.title.text == "Load (TRIMPeq per week)"
    assert load.layout.xaxis.title.text == "Week (starting Monday)"
    assert zones.data[0].name == "Z1 <78%"
    assert zones.layout.yaxis.title.text == "Time in zone (hours per week)"
    assert zones.layout.legend.title.text == "Heart-rate zone (% of threshold)"
    assert all(trace.type == "bar" for trace in (*load.data, *zones.data))
    assert load.layout.yaxis.rangemode == "tozero"


def test_t_ui_02_the_ui_stays_responsive_while_the_charts_are_built(
    qtbot: QtBot, tmp_path: Path, settings: QSettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    release = threading.Event()
    threads: list[int] = []
    real = prepare_weekly_zones_chart

    def slow(*args: object, **kwargs: object) -> object:
        threads.append(threading.get_ident())
        release.wait(timeout=30)
        return real(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(overview_page, "prepare_weekly_zones_chart", slow)
    window = window_with_activities(qtbot, tmp_path, settings)
    page = window.overview_page

    qtbot.waitUntil(lambda: bool(threads), timeout=WAIT_MS)

    assert isinstance(window.controller.overview_state, Ready)
    assert page.current_widget() is not page.charts_widget  # the charts are not ready yet
    assert latency_of_one_event(qtbot) < 0.1  # T-UI-02
    assert threading.get_ident() not in threads  # outside the UI thread
    release.set()
    qtbot.waitUntil(lambda: page.current_widget() is page.charts_widget, timeout=WAIT_MS)


# --- Activity detail ------------------------------------------------------------------


def test_double_click_opens_the_detail_with_summary_chart_and_time_in_zones(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = window_with_activities(qtbot, tmp_path, settings)
    table = window.activities_page.table
    show_activities_section(window)
    qtbot.waitExposed(window)
    table.selectRow(0)
    rect = table.visualRect(first_row_index(window))

    QTest.mouseClick(table.viewport(), Qt.MouseButton.LeftButton, pos=rect.center())
    QTest.mouseDClick(table.viewport(), Qt.MouseButton.LeftButton, pos=rect.center())

    dialog = open_detail(qtbot, window)
    qtbot.waitUntil(dialog.shows_chart, timeout=WAIT_MS)
    assert "Running" in dialog.title_text
    assert dialog.summary_text("moving") == "0:30:00"
    assert dialog.summary_text("threshold") == "171"
    assert dialog.summary_text("limits") == "50 to 190"
    assert dialog.zone_model.rowCount() == 5
    zone_times = [dialog.zone_model.data(dialog.zone_model.index(r, 2)) for r in range(5)]
    assert sum(_seconds(text) for text in zone_times) > 1500  # almost all of the 1800 s counted
    assert dialog.zone_model.headerData(1, Qt.Orientation.Horizontal) == "Heart rate (bpm)"
    zone_bpm = [dialog.zone_model.data(dialog.zone_model.index(r, 1)) for r in range(5)]
    # threshold 171, limits 50 to 190: Z2 starts at 133.4 -> 134, Z3 at 148.8 -> 149, ...
    assert zone_bpm == [f"{a}{DASH}{b}" for a, b in BPM_RANGES]
    assert dialog.zone_model.headerData(4, Qt.Orientation.Horizontal) == "Load (TRIMPeq)"
    zone_loads = [dialog.zone_model.data(dialog.zone_model.index(r, 4)) for r in range(5)]
    assert all(isinstance(text, str) and text for text in zone_loads)
    assert dialog.shows_track()  # the synthetic runs have positions: the map is beside the summary
    assert dialog.no_gps_text == ""
    figure = dialog.chart.current_figure
    assert figure is not None
    assert figure.data[0].name == "Heart rate"
    assert len(figure.layout.shapes) == 5  # the zone bands
    assert figure.layout.xaxis.title.text == "Time (min)"
    assert figure.layout.yaxis.title.text == "Heart rate (bpm)"


def _seconds(text: object) -> int:
    assert isinstance(text, str)
    hours, minutes, seconds = (int(part) for part in text.split(":"))
    return hours * 3600 + minutes * 60 + seconds


def test_enter_in_the_table_opens_the_detail_and_the_command_is_in_the_menu(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = window_with_activities(qtbot, tmp_path, settings)
    table = window.activities_page.table
    show_activities_section(window)
    assert not window.detail_action.isEnabled()
    assert window.detail_action.toolTip() == "Select an activity in the Activities list first."

    table.selectRow(2)
    table.setFocus()
    assert window.detail_action.isEnabled()
    QTest.keyClick(table, Qt.Key.Key_Return)

    dialog = open_detail(qtbot, window)
    qtbot.waitUntil(dialog.shows_chart, timeout=WAIT_MS)
    dialog.close()
    qtbot.waitUntil(lambda: window.profile_dialogs.detail_dialog is None, timeout=WAIT_MS)
    window.detail_action.trigger()  # also from the menu, with the row still selected
    again = open_detail(qtbot, window)
    qtbot.waitUntil(again.shows_chart, timeout=WAIT_MS)


def test_two_activities_open_in_two_windows_and_the_same_one_is_not_opened_twice(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = window_with_activities(qtbot, tmp_path, settings)
    table = window.activities_page.table
    show_activities_section(window)
    dialogs = window.profile_dialogs

    for row in (0, 2):
        table.selectRow(row)
        window.detail_action.trigger()
    qtbot.waitUntil(lambda: len(dialogs.detail_dialogs) == 2, timeout=WAIT_MS)
    first, second = dialogs.detail_dialogs
    qtbot.waitUntil(lambda: first.shows_chart() and second.shows_chart(), timeout=WAIT_MS)
    assert first.title_text != second.title_text  # each window keeps its own activity

    table.selectRow(0)
    window.detail_action.trigger()  # already open: brought forward, not opened again
    assert dialogs.detail_dialogs == (first, second)

    first.close()
    qtbot.waitUntil(lambda: dialogs.detail_dialogs == (second,), timeout=WAIT_MS)
    assert second.shows_chart()


def test_a_profile_change_closes_the_detail_windows_of_the_old_profile(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = window_with_activities(qtbot, tmp_path, settings)
    show_activities_section(window)
    dialogs = window.profile_dialogs
    window.activities_page.table.selectRow(0)
    window.detail_action.trigger()
    old = open_detail(qtbot, window)
    qtbot.waitUntil(old.shows_chart, timeout=WAIT_MS)

    dialogs.profile_changed()

    assert dialogs.detail_dialogs == ()
    assert dialogs.detail_dialog is None
    window.detail_action.trigger()  # the same identity opens a new window, not the old one
    new = open_detail(qtbot, window)
    assert new is not old


def test_a_missing_file_gives_a_clear_message_instead_of_a_chart(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = window_with_activities(qtbot, tmp_path, settings)
    archive = tmp_path / "profiles" / PROFILE / PROFILE_ARCHIVE_DIR
    for original in archive.rglob("*"):
        if original.is_file():
            original.unlink()
    window.activities_page.table.selectRow(0)

    window.detail_action.trigger()

    dialog = open_detail(qtbot, window)
    page = dialog.message_page
    qtbot.waitUntil(
        lambda: page.title_text == "The activity detail is not available", timeout=WAIT_MS
    )

    assert "no longer in the archive" in page.body_text
    assert "Import the original file again" in page.body_text
    assert not dialog.shows_chart()
    assert dialog.title_text == ""  # no summary of an activity that cannot be read


def test_the_detail_figure_is_built_outside_the_ui_thread(
    qtbot: QtBot, tmp_path: Path, settings: QSettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    threads: list[int] = []
    real = prepare_heart_rate_chart

    def recording(*args: object, **kwargs: object) -> object:
        threads.append(threading.get_ident())
        return real(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(activity_detail_loader, "prepare_heart_rate_chart", recording)
    window = window_with_activities(qtbot, tmp_path, settings)
    window.activities_page.table.selectRow(0)

    window.detail_action.trigger()

    dialog = open_detail(qtbot, window)
    qtbot.waitUntil(dialog.shows_chart, timeout=WAIT_MS)
    assert threads
    assert threading.get_ident() not in threads


def test_closing_the_window_with_the_detail_open_finishes_the_closing(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = window_with_activities(qtbot, tmp_path, settings)
    window.activities_page.table.selectRow(0)
    window.detail_action.trigger()
    dialog = open_detail(qtbot, window)
    qtbot.waitUntil(dialog.shows_chart, timeout=WAIT_MS)

    close_window(qtbot, window)

    assert window.is_closed
    assert window.profile_dialogs.detail_dialog is None


def test_closing_a_window_with_several_charts_and_details_open_destroys_every_page_first(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = window_with_activities(qtbot, tmp_path, settings)
    page = window.overview_page
    window.activities_page.table.selectRow(0)
    window.detail_action.trigger()
    first = open_detail(qtbot, window)
    qtbot.waitUntil(first.shows_chart, timeout=WAIT_MS)
    first.close()  # one detail already closed and one still open when the window closes
    qtbot.waitUntil(lambda: window.profile_dialogs.detail_dialog is None, timeout=WAIT_MS)
    window.detail_action.trigger()
    second = open_detail(qtbot, window)
    qtbot.waitUntil(second.shows_chart, timeout=WAIT_MS)
    views = [page.chart, page.load_chart, page.zones_chart, second.chart]
    pages = [view.page for view in views]
    assert all(isValid(web_page) for web_page in pages)

    close_window(qtbot, window)

    assert window.is_closed
    assert not any(isValid(web_page) for web_page in pages)  # destroyed immediately, not queued
    assert not any(view.engine_started for view in views if isValid(view))


def test_releasing_the_web_engine_twice_is_harmless_and_after_it_no_page_is_created(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = window_with_activities(qtbot, tmp_path, settings)
    view = window.overview_page.chart
    web_page = view.page

    window.release_web_engine()  # as from `aboutToQuit`
    window.release_web_engine()

    assert not isValid(web_page)
    assert not view.engine_started
    view.start_engine()  # the engine is gone: no new page
    assert not view.engine_started


def test_without_heart_rate_the_detail_hides_the_table_of_the_zones(qtbot: QtBot) -> None:
    dialog = ActivityDetailDialog(
        ChartEngine(lambda _name: None), ZoneInfo("Europe/Rome"), theme=lambda: Theme.LIGHT
    )
    qtbot.addWidget(dialog)
    dialog.show()

    dialog.show_outcome(DetailReady(detail_with(ZoneSeconds.ZERO), None))  # without HR: estimated

    table = dialog.findChild(QTableView, "detailZones")
    assert table is not None
    assert not table.isVisibleTo(dialog)  # five rows at 0:00:00 would be lying
    assert dialog.message_page.title_text == "No heart-rate data"
