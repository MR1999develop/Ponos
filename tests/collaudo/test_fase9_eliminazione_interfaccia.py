# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Phase 9 acceptance campaign, deleting activities through the main window: what the windows that
still show a deleted activity do, and the whole list deleted and brought back.

From the Composition Root with the invented profile "Atleta di prova" and the fake Garmin of
`tests/fakes/` (no network).
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from pathlib import Path

import pytest
from PySide6.QtCore import QLocale, QSettings
from pytestqt.qtbot import QtBot

from tests.collaudo.support import format_logs
from tests.ui.garmin_support import activity_count, wait_activities
from tests.ui.test_activity_deletion import (
    RUNS,
    identities,
    select,
    window_with_runs,
)
from tests.ui.workspace_support import WAIT_MS, make_settings


@pytest.fixture
def settings(tmp_path: Path) -> QSettings:
    return make_settings(tmp_path)


@pytest.fixture(autouse=True)
def english() -> Iterator[None]:
    previous = QLocale()
    QLocale.setDefault(QLocale(QLocale.Language.English, QLocale.Country.UnitedStates))
    yield
    QLocale.setDefault(previous)


def test_t_ui_10_the_whole_list_deleted_and_brought_back_with_the_undo_leaves_the_window_working(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = window_with_runs(qtbot, tmp_path, settings)
    before = identities(window)
    select(window, *range(RUNS))

    window.activity_deletion.delete_action.trigger()

    wait_activities(qtbot, window, 0)
    assert activity_count(window) == 0
    assert window.activity_deletion.undo_stack.count() == 1
    window.activity_deletion.undo_action.trigger()
    wait_activities(qtbot, window, RUNS)
    assert identities(window) == before
    assert not window.activity_deletion.page.model.rows


def test_t_ui_10_the_detail_window_of_a_deleted_activity_does_not_break_the_use_as_a_test(
    qtbot: QtBot, tmp_path: Path, settings: QSettings, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    window = window_with_runs(qtbot, tmp_path, settings)
    victim = identities(window)[0]
    window.profile_dialogs.show_activity_detail(victim)
    qtbot.waitUntil(lambda: window.profile_dialogs.detail_dialog is not None, timeout=WAIT_MS)
    dialog = window.profile_dialogs.detail_dialog
    assert dialog is not None
    qtbot.waitUntil(dialog.shows_chart, timeout=WAIT_MS)
    select(window, 0)

    window.activity_deletion.delete_action.trigger()
    wait_activities(qtbot, window, RUNS - 1)
    dialog.use_as_test_button.click()  # a window that still shows the deleted activity

    result = window.threshold.result_dialog
    assert result is not None
    # the evaluation runs off the UI thread and ends in a message, never in an applicable verdict
    qtbot.waitUntil(lambda: not result.is_loading, timeout=WAIT_MS)
    assert not result.apply_button.isEnabled()
    assert window.isVisible()
    assert activity_count(window) == RUNS - 1
    assert "Traceback" not in format_logs(caplog.records)
