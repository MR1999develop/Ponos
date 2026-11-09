# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Builders shared by the map window tests: a window with eight made-up runs and a map view.

The runs are the generated ones of the import tests (one per day, all starting at the same invented
point in the open sea, no real person or place). The map is driven through the controller (the
view the page would report), so these tests do not depend on the web engine's timing; the tests
of the page itself are in `test_map_page_web.py`.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSettings
from pytestqt.qtbot import QtBot

from ponos_coach.activities.domain import GeoBox
from ponos_coach.ui.main_window import MainWindow
from ponos_coach.ui.map_messages import MapView
from tests.fixtures.generators.tracks import SEA_LAT_DEG, SEA_LON_DEG
from tests.ui.workspace_support import (
    FILES_IN_FIXTURE_FOLDER,
    WAIT_MS,
    TileFakes,
    compose,
    create_profile,
    make_import_folder,
    wait_ready,
)

PROFILE = "Atleta di prova"
SECTION_ACTIVITIES = 1
SECTION_MAP = 3
ACTIVITIES_WITH_POSITION = FILES_IN_FIXTURE_FOLDER  # all eight runs have positions

# A view around the invented point at sea: zoomed in (tracks are drawn from zoom 11) and zoomed out.
NEAR = MapView(
    GeoBox(SEA_LAT_DEG - 0.05, SEA_LON_DEG - 0.05, SEA_LAT_DEG + 0.05, SEA_LON_DEG + 0.05), 13
)
FAR = MapView(GeoBox(-60.0, -60.0, 0.0, 20.0), 5)
ELSEWHERE = MapView(GeoBox(40.0, 0.0, 41.0, 1.0), 13)  # nothing there


def map_window(
    qtbot: QtBot, tmp_path: Path, settings: QSettings, tiles: TileFakes | None = None
) -> MainWindow:
    """A profile with eight runs, ready, with the Map section open and the engines started."""
    window = compose(tmp_path, settings, tiles=tiles)
    qtbot.addWidget(window)
    window.start()
    create_profile(qtbot, window, PROFILE)
    with qtbot.waitSignal(window.controller.import_finished, timeout=WAIT_MS):
        window.import_folder(make_import_folder(tmp_path))
    wait_ready(qtbot, window, ACTIVITIES_WITH_POSITION)
    window.sidebar.setCurrentRow(SECTION_MAP)
    window.map_ui.page.start_engines()
    qtbot.waitUntil(lambda: window.map_ui.controller.search is not None, timeout=WAIT_MS)
    return window


def show_view(qtbot: QtBot, window: MainWindow, view: MapView) -> None:
    """Report a view as the page would, and wait for the answer about that area."""
    answers: list[object] = []
    controller = window.map_ui.controller
    controller.area_changed.connect(answers.append)
    try:
        controller.set_view(view)
        qtbot.waitUntil(lambda: bool(answers), timeout=WAIT_MS)
    finally:
        controller.area_changed.disconnect(answers.append)
