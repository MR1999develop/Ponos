# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Builders and gestures shared by the threshold test window tests.

The profile and the activities are made up (no real person). The test run is the fixture
`run_threshold_test.fit` (14 March 2026, 08:00 in Rome): threshold 168 bpm, pace 278 s/km. The
tests' today is 30 June 2026 and the profile starts with the estimated threshold of 171 bpm.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QSettings
from pytestqt.qtbot import QtBot

from ponos_coach.activities.domain import Sport
from ponos_coach.load.use_cases import ProfileActivity
from ponos_coach.ui.main_window import MainWindow
from ponos_coach.ui.overview_state import Ready
from ponos_coach.ui.threshold_dialogs import ThresholdHistoryPanel, ThresholdResultDialog
from tests.ui.workspace_support import WAIT_MS, compose, create_profile, wait_ready

PROFILE = "Atleta di prova"
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "data" / "fit"
THRESHOLD_RUN = "run_threshold_test.fit"
NO_DISTANCE_RUN = "run_threshold_no_distance.fit"
BIKE = "bike_easy.fit"
SHORT_RUN = "run_gps_loop.fit"  # 20 minutes: too short for the test
SECTION_ACTIVITIES = 1


def plain(text: str) -> str:
    """The text with the narrow no-break space of the English time format as a normal space."""
    return text.replace(chr(0x202F), " ")


def folder_with(tmp_path: Path, *names: str) -> Path:
    """A folder with copies of fixture files."""
    folder = tmp_path / "to-import"
    folder.mkdir(exist_ok=True)
    for name in names:
        shutil.copy(FIXTURES / name, folder / name)
    return folder


def window_with(
    qtbot: QtBot,
    tmp_path: Path,
    settings: QSettings,
    *names: str,
) -> MainWindow:
    """A profile made from the interface, with the fixture files imported."""
    window = compose(tmp_path, settings)
    qtbot.addWidget(window)
    window.start()
    create_profile(qtbot, window, PROFILE)
    folder = folder_with(tmp_path, *names)
    count = len(list(folder.glob("*.fit")))
    with qtbot.waitSignal(window.controller.import_finished, timeout=WAIT_MS):
        window.import_folder(folder)
    wait_ready(qtbot, window, count)
    return window


def activities_of(window: MainWindow) -> list[ProfileActivity]:
    """The activities of the overview, as the controller holds them."""
    state = window.controller.overview_state
    assert isinstance(state, Ready)
    return list(state.overview.activities)


def select_where(window: MainWindow, wanted: Callable[[ProfileActivity], bool]) -> ProfileActivity:
    """Show the Activities list and select the first activity that satisfies `wanted`."""
    window.sidebar.setCurrentRow(SECTION_ACTIVITIES)
    page = window.activities_page
    for row in range(page.proxy.rowCount()):
        activity = page.model.activity_at(page.proxy.mapToSource(page.proxy.index(row, 0)).row())
        if activity is not None and wanted(activity):
            page.table.selectRow(row)
            return activity
    raise AssertionError("no activity satisfies the condition")


def select_run(window: MainWindow) -> ProfileActivity:
    """Select the first run of the list."""
    return select_where(window, lambda a: a.sport is Sport.RUNNING)


def first_load(window: MainWindow) -> float:
    """The load of the first activity of the overview."""
    return activities_of(window)[0].load.load


def open_review(qtbot: QtBot, window: MainWindow) -> ThresholdResultDialog:
    """Use the selected activity as the test and wait for the dialog to leave the working state."""
    qtbot.waitUntil(window.use_as_test_action.isEnabled, timeout=WAIT_MS)
    window.use_as_test_action.trigger()
    qtbot.waitUntil(lambda: window.threshold.result_dialog is not None, timeout=WAIT_MS)
    dialog = window.threshold.result_dialog
    assert dialog is not None
    qtbot.waitUntil(lambda: not dialog.is_loading, timeout=WAIT_MS)
    return dialog


def apply_test(qtbot: QtBot, window: MainWindow) -> None:
    """Select the run, review it and apply it; wait until the dialog is gone."""
    select_run(window)
    dialog = open_review(qtbot, window)
    assert dialog.is_reviewing
    dialog.apply_button.click()
    qtbot.waitUntil(lambda: window.threshold.result_dialog is None, timeout=WAIT_MS)


def open_history(qtbot: QtBot, window: MainWindow) -> ThresholdHistoryPanel:
    """Open "Threshold tests..." (Settings > Training plan) and return the history panel."""
    qtbot.waitUntil(window.threshold_history_action.isEnabled, timeout=WAIT_MS)
    window.threshold_history_action.trigger()
    return window.threshold.history_panel
