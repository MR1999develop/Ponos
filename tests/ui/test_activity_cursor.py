# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""One pointer for an activity (the user's request of 2026-11-11): moving over its HR or speed
chart marks the same moment on its track and elevation profile, and the other way round.

The time is the common key: every series of the file counts seconds from its first record.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import QSettings
from pytestqt.qtbot import QtBot

from ponos_coach.activities.domain import ActivityIdentity
from ponos_coach.load.activity_profile import ActivityProfileData
from ponos_coach.ui.main_window import MainWindow
from tests.builders import fit_identity
from tests.ui.map_support import NEAR, map_window, show_view
from tests.ui.workspace_support import WAIT_MS, make_settings

OTHER: ActivityIdentity = fit_identity(999)


@pytest.fixture
def settings(tmp_path: Path) -> QSettings:
    return make_settings(tmp_path)


def window_with_profile(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> tuple[MainWindow, ActivityProfileData]:
    """The map with its first activity selected and its elevation profile received."""
    window = map_window(qtbot, tmp_path, settings)
    show_view(qtbot, window, NEAR)
    profiles: list[object] = []
    window.map_ui.controller.profile_changed.connect(profiles.append)
    window.map_ui.controller.select_index(0)
    qtbot.waitUntil(
        lambda: any(isinstance(p, ActivityProfileData) for p in profiles), timeout=WAIT_MS
    )
    profile = next(p for p in profiles if isinstance(p, ActivityProfileData))
    return window, profile


def record_map(window: MainWindow) -> tuple[list[tuple[str, object]], list[str]]:
    """What the page sends to the map and to the elevation profile from now on."""
    calls: list[tuple[str, object]] = []
    scripts: list[str] = []

    def call(function: str, payload: object) -> None:
        calls.append((function, payload))

    window.map_page.map_view.call = call  # type: ignore[method-assign]
    window.map_page.chart.run_script = scripts.append  # type: ignore[method-assign, assignment]
    return calls, scripts


def test_another_view_s_pointer_marks_the_nearest_point_on_track_and_profile(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window, profile = window_with_profile(qtbot, tmp_path, settings)
    calls, scripts = record_map(window)
    index = 5
    km = float(profile.distance_m[index]) / 1000.0

    window.map_page.show_cursor_at(OTHER, float(profile.seconds[index]))  # another activity
    assert (calls, scripts) == ([], [])
    window.map_page.show_cursor_at(profile.identity, float(profile.seconds[index]) + 0.2)
    assert calls == [("setCursor", {"index": index})]
    assert scripts == [f"ponosCursor({km!r});"]
    window.map_page.show_cursor_at(profile.identity, None)  # the pointer left
    assert calls[-1] == ("setCursor", {"index": None})
    assert scripts[-1] == "ponosCursor(null);"


def test_the_pointer_on_the_profile_or_on_the_track_is_reported_with_its_time(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window, profile = window_with_profile(qtbot, tmp_path, settings)
    record_map(window)
    reports: list[tuple[object, object]] = []
    window.map_page.cursor_moved.connect(lambda who, at: reports.append((who, at)))

    window.map_page.chart.hovered.emit(3)
    window.map_page.chart.hover_left.emit()
    window.map_page.map_view.bridge.point_hovered.emit(7)
    window.map_page.map_view.bridge.point_left.emit()

    assert reports == [
        (profile.identity, float(profile.seconds[3])),
        (profile.identity, None),
        (profile.identity, float(profile.seconds[7])),
        (profile.identity, None),
    ]


def test_the_detail_window_and_the_map_of_one_activity_share_the_pointer(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window, profile = window_with_profile(qtbot, tmp_path, settings)
    calls, _ = record_map(window)
    window.profile_dialogs.show_activity_detail(profile.identity)
    dialog = window.profile_dialogs.detail_dialog
    assert dialog is not None
    hover: dict[str, list[str]] = {"hr": [], "speed": []}
    dialog.chart.run_script = hover["hr"].append  # type: ignore[method-assign, assignment]
    dialog.speed_chart.run_script = hover["speed"].append  # type: ignore[method-assign, assignment]

    # the map's pointer at point 4 shows that minute on both charts
    window.map_page.map_view.bridge.point_hovered.emit(4)
    minutes = float(profile.seconds[4]) / 60.0
    assert hover["hr"] == [f"ponosHoverAt({minutes!r});"]
    assert hover["speed"] == [f"ponosHoverAt({minutes!r});"]

    # the HR chart's pointer at point 6's minute marks point 6 on the track
    calls.clear()
    dialog.chart.hovered_x.emit(float(profile.seconds[6]) / 60.0)
    assert ("setCursor", {"index": 6}) in calls
    dialog.speed_chart.hovered_x.emit(None)
    assert calls[-1] == ("setCursor", {"index": None})
