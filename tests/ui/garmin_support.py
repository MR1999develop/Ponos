# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Gestures common to the Garmin account and window synchronization tests."""

from __future__ import annotations

import threading

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QMessageBox
from pytestqt.qtbot import QtBot

from ponos_coach.activities.domain import ActivityPage, GarminActivityId, RemoteActivity
from ponos_coach.ui.main_window import MainWindow
from ponos_coach.ui.overview_state import Ready
from ponos_coach.ui.sync_controller import SyncController
from tests.fakes.garmin import (
    FIRST_ACTIVITY_ID,
    TEST_EMAIL,
    TEST_MFA_CODE,
    TEST_PASSWORD,
    FakeActivitySource,
)
from tests.fixtures.generators.import_files import RUN_SECONDS, run_start
from tests.ui.workspace_support import WAIT_MS

PROFILE_NAME = "Atleta di prova"


def sync_of(window: MainWindow) -> SyncController:
    """The window's synchronization controller."""
    return window.controller.sync


def remote_runs(*numbers: int) -> list[RemoteActivity]:
    """Garmin runs with the given numbers (run `n` is the same as `fit_run(n)`)."""
    return [
        RemoteActivity(
            GarminActivityId(FIRST_ACTIVITY_ID + number),
            run_start(number),
            float(RUN_SECONDS),
            "running",
            True,
        )
        for number in numbers
    ]


class GatedGarmin:
    """`ActivitySource` decorator: each download waits at a gate, with a deadline.

    `entered` fires when a download has reached the gate (the sync is there).
    """

    def __init__(self, inner: FakeActivitySource, gate: threading.Event) -> None:
        self._inner = inner
        self._gate = gate
        self.entered = threading.Event()

    def list_page(self, offset: int, limit: int) -> ActivityPage:
        return self._inner.list_page(offset, limit)

    def download_original(self, activity_id: GarminActivityId, max_bytes: int) -> bytes:
        self.entered.set()
        if not self._gate.wait(timeout=30):
            raise TimeoutError("cancello mai aperto")
        return self._inner.download_original(activity_id, max_bytes)


def wait_account(qtbot: QtBot, window: MainWindow, status: object) -> None:
    """Wait until the open profile's account is in this state."""
    qtbot.waitUntil(lambda: sync_of(window).account_status is status, timeout=WAIT_MS)


def open_login_dialog(qtbot: QtBot, window: MainWindow) -> None:
    """Choose "Connect Garmin account..." and wait for the dialog."""
    window.garmin.connect_action.trigger()
    qtbot.waitUntil(lambda: window.garmin.login_dialog is not None, timeout=WAIT_MS)


def submit_login(
    qtbot: QtBot,
    window: MainWindow,
    email: str = TEST_EMAIL,
    password: str = TEST_PASSWORD,
) -> None:
    """Write email and password in the dialog (opening it if needed) and press "Connect"."""
    if window.garmin.login_dialog is None:
        open_login_dialog(qtbot, window)
    dialog = window.garmin.login_dialog
    assert dialog is not None
    dialog.email_edit.clear()
    QTest.keyClicks(dialog.email_edit, email)
    QTest.keyClicks(dialog.password_edit, password)
    QTest.mouseClick(dialog.connect_button, Qt.MouseButton.LeftButton)


def answer_mfa_dialog(qtbot: QtBot, window: MainWindow, code: str | None = TEST_MFA_CODE) -> None:
    """Wait for the code dialog and fill it in (or cancel it if `code` is `None`)."""
    qtbot.waitUntil(lambda: window.garmin.mfa_dialog is not None, timeout=WAIT_MS)
    dialog = window.garmin.mfa_dialog
    assert dialog is not None
    if code is None:
        dialog.reject()
        return
    QTest.keyClicks(dialog.code_edit, code)
    QTest.mouseClick(dialog.verify_button, Qt.MouseButton.LeftButton)


def link_account(qtbot: QtBot, window: MainWindow, *, mfa: bool) -> None:
    """Connect the account from the dialog and wait for the end of the first sync round."""
    with qtbot.waitSignal(sync_of(window).sync_finished, timeout=WAIT_MS):
        submit_login(qtbot, window)
        if mfa:
            answer_mfa_dialog(qtbot, window)


def sync_and_wait(qtbot: QtBot, window: MainWindow, *, verify: bool = False) -> None:
    """Give the "Synchronise" command (or "Full check") and wait for the end of the round."""
    action = window.garmin.verify_action if verify else window.garmin.sync_action
    assert action.isEnabled()
    with qtbot.waitSignal(sync_of(window).sync_finished, timeout=WAIT_MS):
        action.trigger()


def open_box(qtbot: QtBot, window: MainWindow, name: str) -> QMessageBox:
    """Wait for and return the message with this name."""

    def find() -> QMessageBox | None:
        return next(
            (
                box
                for box in window.open_boxes
                if isinstance(box, QMessageBox) and box.objectName() == name
            ),
            None,
        )

    qtbot.waitUntil(lambda: find() is not None, timeout=WAIT_MS)
    box = find()
    assert box is not None
    return box


def click_button(box: QMessageBox, text: str) -> None:
    """Press the button with this text."""
    next(button for button in box.buttons() if button.text() == text).click()


def activity_count(window: MainWindow) -> int:
    """How many activities the overview shows (0 if it is not ready)."""
    state = window.controller.overview_state
    return len(state.overview.activities) if isinstance(state, Ready) else 0


def wait_activities(qtbot: QtBot, window: MainWindow, count: int) -> None:
    """Wait until the overview is ready with this number of activities."""
    qtbot.waitUntil(lambda: activity_count(window) == count, timeout=WAIT_MS)
