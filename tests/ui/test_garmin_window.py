# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Garmin account and synchronization in the window, from the Composition Root (phase 2).

Garmin, keyring and notification are the fakes of `tests/fakes/`: no network, no real
login. The tests cover T-UI-02 (responsive window while downloading) and T-SEC-07 (the
password does not appear in the logs).
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from pathlib import Path

import pytest
from PySide6.QtCore import QSettings, QTimer
from PySide6.QtWidgets import QMenu
from pytestqt.qtbot import QtBot

from ponos_coach import __main__ as entry
from ponos_coach.activities.ports import AuthenticationRequiredError, GarminSession
from ponos_coach.profiles.ports import TokenStoreUnavailableError
from ponos_coach.ui.main_window import MainWindow
from ponos_coach.ui.sync_model import AccountStatus
from ponos_coach.ui.sync_worker import MFA_WAIT_S
from tests.fakes.garmin import (
    PLACEHOLDER_TOKEN,
    TEST_EMAIL,
    TEST_MFA_CODE,
    TEST_PASSWORD,
    Call,
    FakeActivitySource,
    FakeGarminAccount,
    OriginalKind,
    make_remote_history,
)
from tests.ui.garmin_support import (
    PROFILE_NAME,
    GatedGarmin,
    activity_count,
    answer_mfa_dialog,
    click_button,
    link_account,
    open_box,
    open_login_dialog,
    remote_runs,
    submit_login,
    sync_and_wait,
    sync_of,
    wait_account,
    wait_activities,
)
from tests.ui.workspace_support import (
    FILES_IN_FIXTURE_FOLDER,
    WAIT_MS,
    GarminFakes,
    close_window,
    compose,
    create_profile,
    fakes_of,
    make_fakes,
    make_import_folder,
    make_settings,
)


@pytest.fixture
def settings(tmp_path: Path) -> QSettings:
    return make_settings(tmp_path)


def window_with_profile(
    qtbot: QtBot,
    tmp_path: Path,
    settings: QSettings,
    fakes: GarminFakes,
    *,
    mfa_wait_s: float = MFA_WAIT_S,
) -> MainWindow:
    """Window with a new profile open and no account (the check has finished)."""
    window = compose(tmp_path, settings, fakes=fakes, mfa_wait_s=mfa_wait_s)
    qtbot.addWidget(window)
    window.start()
    create_profile(qtbot, window, PROFILE_NAME)
    wait_account(qtbot, window, AccountStatus.NOT_LINKED)
    return window


def linked_window(
    qtbot: QtBot, tmp_path: Path, settings: QSettings, fakes: GarminFakes, *, mfa: bool = False
) -> MainWindow:
    """Window with the account connected and the first sync round finished."""
    window = window_with_profile(qtbot, tmp_path, settings, fakes)
    link_account(qtbot, window, mfa=mfa)
    return window


def history(count: int, *, mfa_required: bool = False) -> GarminFakes:
    return make_fakes(
        mfa_required=mfa_required, source=FakeActivitySource(make_remote_history(count))
    )


# --- Commands and reasons -----


def test_without_a_profile_the_garmin_commands_are_off_and_say_why(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = compose(tmp_path, settings)
    qtbot.addWidget(window)

    garmin = window.garmin
    for action in (garmin.connect_action, garmin.disconnect_action, garmin.sync_action):
        assert not action.isEnabled()
        assert action.toolTip() == "Create or open a profile first."
    assert not garmin.stop_action.isEnabled()


def test_the_garmin_menu_sits_after_the_plan_menu_with_every_command(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = compose(tmp_path, settings)
    qtbot.addWidget(window)

    titles = [action.text() for action in window.menuBar().actions()]
    garmin_menu = next(a.menu() for a in window.menuBar().actions() if a.text() == "Garmin")

    assert titles == [
        "File",
        "Profile",
        "Threshold test",
        "Calendar",
        "Map",
        "Plan",
        "Garmin",
        "Language",
        "Help",
    ]
    assert isinstance(garmin_menu, QMenu)
    assert [a.text() for a in garmin_menu.actions() if not a.isSeparator()] == [
        "Connect Garmin account...",
        "Disconnect Garmin account",
        "Synchronise",
        "Full check",
        "Stop synchronisation",
        "Synchronise when a profile opens",
    ]


def test_a_new_profile_without_an_account_offers_only_to_connect(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = window_with_profile(qtbot, tmp_path, settings, make_fakes())

    garmin = window.garmin
    assert garmin.connect_action.isEnabled()
    assert not garmin.sync_action.isEnabled()
    assert garmin.sync_action.toolTip() == "Connect a Garmin account first."
    assert not garmin.disconnect_action.isEnabled()
    assert window.import_files_action.isEnabled()


# --- Connecting the account -----


def test_connecting_without_mfa_saves_the_tokens_and_downloads_the_whole_history(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    fakes = history(4)
    window = window_with_profile(qtbot, tmp_path, settings, fakes)

    link_account(qtbot, window, mfa=False)

    profile = window.controller.profile
    assert profile is not None
    assert fakes.tokens.saved[profile.profile_id].reveal() == PLACEHOLDER_TOKEN
    assert sync_of(window).account_status is AccountStatus.LINKED
    assert window.garmin.login_dialog is None
    wait_activities(qtbot, window, 4)
    assert window.activities_page.model.rowCount() == 4
    assert fakes.account.mfa_prompts == 0
    assert window.garmin.connect_action.isEnabled() is False  # already connected
    assert window.garmin.disconnect_action.isEnabled()
    assert window.garmin.sync_action.isEnabled()


def test_connecting_with_mfa_asks_for_the_code_in_a_second_dialog(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    fakes = history(2, mfa_required=True)
    window = window_with_profile(qtbot, tmp_path, settings, fakes)

    link_account(qtbot, window, mfa=True)

    assert fakes.account.mfa_prompts == 1
    assert sync_of(window).account_status is AccountStatus.LINKED
    assert window.garmin.mfa_dialog is None
    wait_activities(qtbot, window, 2)


def test_cancelling_the_mfa_code_connects_nothing_and_shows_no_error(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    fakes = history(2, mfa_required=True)
    window = window_with_profile(qtbot, tmp_path, settings, fakes)

    with qtbot.waitSignal(sync_of(window).login_finished, timeout=WAIT_MS):
        submit_login(qtbot, window)
        answer_mfa_dialog(qtbot, window, None)

    qtbot.waitUntil(lambda: window.garmin.login_dialog is None, timeout=WAIT_MS)
    qtbot.waitUntil(lambda: sync_of(window).operation is None, timeout=WAIT_MS)
    assert fakes.tokens.saved == {}
    assert sync_of(window).account_status is AccountStatus.NOT_LINKED
    assert window.open_boxes == []
    assert window.garmin.connect_action.isEnabled()


def test_wrong_credentials_are_explained_in_the_dialog_and_can_be_retried(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    fakes = history(2)
    window = window_with_profile(qtbot, tmp_path, settings, fakes)

    with qtbot.waitSignal(sync_of(window).login_finished, timeout=WAIT_MS):
        submit_login(qtbot, window, password="sbagliata")  # noqa: S106

    dialog = window.garmin.login_dialog
    assert dialog is not None
    assert "did not accept" in dialog.message_text
    assert dialog.password_edit.text() == ""
    assert not dialog.is_busy
    assert fakes.tokens.saved == {}

    link_account(qtbot, window, mfa=False)

    assert sync_of(window).account_status is AccountStatus.LINKED


def test_connect_is_off_until_email_and_password_are_filled_and_says_why(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = window_with_profile(qtbot, tmp_path, settings, make_fakes())

    open_login_dialog(qtbot, window)
    dialog = window.garmin.login_dialog
    assert dialog is not None

    assert not dialog.connect_button.isEnabled()
    assert dialog.connect_button.toolTip() == "Enter your email and password."
    assert dialog.password_edit.echoMode().name == "Password"
    dialog.email_edit.setText(TEST_EMAIL)
    dialog.password_edit.setText("x")
    assert dialog.connect_button.isEnabled()
    dialog.reject()


def test_the_password_is_wiped_from_the_dialog_and_never_reaches_the_logs(
    qtbot: QtBot, tmp_path: Path, settings: QSettings, caplog: pytest.LogCaptureFixture
) -> None:
    """T-SEC-07."""
    fakes = history(2, mfa_required=True)
    window = window_with_profile(qtbot, tmp_path, settings, fakes)

    with caplog.at_level(logging.DEBUG):
        with qtbot.waitSignal(sync_of(window).sync_finished, timeout=WAIT_MS):
            submit_login(qtbot, window)
            dialog = window.garmin.login_dialog
            assert dialog is not None
            assert dialog.password_edit.text() == ""  # emptied right after being read
            answer_mfa_dialog(qtbot, window)
        wait_activities(qtbot, window, 2)

    secrets = (TEST_PASSWORD, TEST_EMAIL, TEST_MFA_CODE, PLACEHOLDER_TOKEN)
    assert not [secret for secret in secrets if secret in caplog.text]


def test_without_a_system_keychain_the_garmin_commands_say_so_and_import_still_works(
    qtbot: QtBot, tmp_path: Path, settings: QSettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_keychain(_base_dir: Path) -> object:
        raise TokenStoreUnavailableError("Secret Service non disponibile")

    monkeypatch.setattr(entry, "create_token_store", no_keychain)
    window = compose(tmp_path, settings, fakes=make_fakes(), default_token_store=True)
    qtbot.addWidget(window)
    window.start()
    create_profile(qtbot, window, PROFILE_NAME)

    garmin = window.garmin
    assert sync_of(window).account_status is AccountStatus.KEYCHAIN_UNAVAILABLE
    for action in (garmin.connect_action, garmin.disconnect_action, garmin.sync_action):
        assert not action.isEnabled()
        assert "keychain" in action.toolTip()
    assert window.import_files_action.isEnabled()
    assert fakes_of(window).account.login_calls == 0


def test_a_keychain_that_stops_answering_disables_the_commands_with_the_reason(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    fakes = make_fakes()
    fakes.tokens.unavailable = True
    window = compose(tmp_path, settings, fakes=fakes)
    qtbot.addWidget(window)
    window.start()
    create_profile(qtbot, window, PROFILE_NAME)

    wait_account(qtbot, window, AccountStatus.KEYCHAIN_UNAVAILABLE)

    assert not window.garmin.connect_action.isEnabled()
    assert "keychain" in window.garmin.connect_action.toolTip()


# --- Disconnecting -----


def test_disconnecting_asks_with_the_verb_and_deletes_only_the_tokens(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    fakes = history(3)
    window = linked_window(qtbot, tmp_path, settings, fakes)
    wait_activities(qtbot, window, 3)

    window.garmin.disconnect_action.trigger()
    box = open_box(qtbot, window, "disconnectConfirmation")
    assert {b.text() for b in box.buttons()} == {"Disconnect", "Cancel"}
    assert box.defaultButton().text() == "Cancel"
    click_button(box, "Disconnect")

    wait_account(qtbot, window, AccountStatus.NOT_LINKED)
    assert fakes.tokens.saved == {}
    assert activity_count(window) == 3  # the downloaded activities stay
    assert window.garmin.connect_action.isEnabled()


def test_cancelling_the_confirmation_keeps_the_account(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    fakes = history(1)
    window = linked_window(qtbot, tmp_path, settings, fakes)

    window.garmin.disconnect_action.trigger()
    click_button(open_box(qtbot, window, "disconnectConfirmation"), "Cancel")

    assert sync_of(window).account_status is AccountStatus.LINKED
    assert len(fakes.tokens.saved) == 1


def test_a_keychain_that_cannot_delete_the_tokens_is_reported(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    fakes = history(1)
    window = linked_window(qtbot, tmp_path, settings, fakes)
    fakes.tokens.unavailable = True

    window.garmin.disconnect_action.trigger()
    click_button(open_box(qtbot, window, "disconnectConfirmation"), "Disconnect")

    box = open_box(qtbot, window, "disconnectFailed")
    assert "keychain" in box.informativeText()


# --- Synchronization -----


def test_opening_a_linked_profile_synchronises_by_itself_and_only_fetches_the_new(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    fakes = history(3)
    first = linked_window(qtbot, tmp_path, settings, fakes)
    wait_activities(qtbot, first, 3)
    close_window(qtbot, first)
    fakes.source.add_activity(remote_runs(40, 41)[0])
    fakes.source.add_activity(remote_runs(40, 41)[1])
    downloads_before = len(fakes.source.downloads)

    second = compose(tmp_path, settings, fakes=fakes)
    qtbot.addWidget(second)
    with qtbot.waitSignal(sync_of(second).sync_finished, timeout=WAIT_MS):
        second.start()

    wait_activities(qtbot, second, 5)
    assert len(fakes.source.downloads) - downloads_before == 2
    qtbot.waitUntil(lambda: len(fakes.notifier.calls) == 2, timeout=WAIT_MS)
    assert "2" in fakes.notifier.calls[1][1]


def test_f5_fetches_what_is_new_and_updates_table_and_overview(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    fakes = history(2)
    window = linked_window(qtbot, tmp_path, settings, fakes)
    wait_activities(qtbot, window, 2)
    fakes.source.add_activity(remote_runs(50)[0])

    sync_and_wait(qtbot, window)

    wait_activities(qtbot, window, 3)
    assert window.activities_page.model.rowCount() == 3
    assert window.garmin.sync_action.shortcut().toString() == "F5"
    qtbot.waitUntil(lambda: len(fakes.notifier.calls) == 2, timeout=WAIT_MS)


def test_the_full_check_lists_everything_again_and_downloads_nothing_already_known(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    fakes = history(3)
    window = linked_window(qtbot, tmp_path, settings, fakes)
    wait_activities(qtbot, window, 3)
    lists_before = fakes.source.call_count(Call.LIST_PAGE)
    downloads_before = len(fakes.source.downloads)

    sync_and_wait(qtbot, window, verify=True)

    assert fakes.source.call_count(Call.LIST_PAGE) > lists_before
    assert len(fakes.source.downloads) == downloads_before
    assert len(fakes.notifier.calls) == 1  # nothing new: no second notification


def test_the_notification_is_sent_once_with_the_number_of_new_activities(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    fakes = history(4)
    window = linked_window(qtbot, tmp_path, settings, fakes)

    qtbot.waitUntil(lambda: len(fakes.notifier.calls) == 1, timeout=WAIT_MS)
    title, message = fakes.notifier.calls[0]
    assert title == "New activities from Garmin"
    assert message == "New activities downloaded: 4"
    sync_and_wait(qtbot, window)  # nothing new
    assert len(fakes.notifier.calls) == 1


def test_a_notification_that_fails_changes_nothing_for_the_user(
    qtbot: QtBot, tmp_path: Path, settings: QSettings, caplog: pytest.LogCaptureFixture
) -> None:
    fakes = history(3)
    fakes.notifier.fail_with = RuntimeError("percorso privato C:/utente")
    window = window_with_profile(qtbot, tmp_path, settings, fakes)

    with caplog.at_level(logging.DEBUG):
        link_account(qtbot, window, mfa=False)
        wait_activities(qtbot, window, 3)

    assert len(fakes.notifier.calls) == 1
    assert window.open_boxes == []
    assert "utente" not in caplog.text  # only the type of the failure is written
    assert "RuntimeError" in caplog.text
    assert window.garmin.sync_action.isEnabled()


def test_the_summary_names_what_was_not_downloaded_in_plain_words(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    fakes = history(3)
    broken = remote_runs(0)[0].garmin_id
    fakes.source.set_kind(broken, OriginalKind.TCX)
    window = window_with_profile(qtbot, tmp_path, settings, fakes)

    link_account(qtbot, window, mfa=False)

    box = open_box(qtbot, window, "syncSummary")
    assert box.text() == "New activities synchronised: 2"
    assert "Activities not downloaded: 1" in box.informativeText()
    assert "the file format is not supported" in box.informativeText()
    assert "New activities downloaded: 2" in box.informativeText()
    wait_activities(qtbot, window, 2)


# --- Responsiveness, Interrupt, closing -----


def gated_window(
    qtbot: QtBot,
    tmp_path: Path,
    settings: QSettings,
    gate: threading.Event,
    *,
    activities: int = 3,
) -> tuple[MainWindow, GatedGarmin, GarminFakes]:
    """Window with the account connected and a sync stopped at the first download."""
    inner = FakeActivitySource(make_remote_history(activities))
    gated = GatedGarmin(inner, gate)
    fakes = make_fakes(mfa_required=False, source=inner, served=gated)
    window = window_with_profile(qtbot, tmp_path, settings, fakes)
    submit_login(qtbot, window)
    qtbot.waitUntil(gated.entered.is_set, timeout=WAIT_MS)
    return window, gated, fakes


def test_the_window_stays_responsive_and_shows_progress_while_downloading(
    qtbot: QtBot, tmp_path: Path, settings: QSettings, gate: threading.Event
) -> None:
    """T-UI-02."""
    window, _, _ = gated_window(qtbot, tmp_path, settings, gate)
    ticks: list[int] = []
    timer = QTimer(window)
    timer.timeout.connect(lambda: ticks.append(1))
    timer.start(10)

    qtbot.wait(300)  # the UI thread runs while the Garmin thread is stopped at the gate

    assert len(ticks) >= 5
    assert sync_of(window).is_syncing
    assert window.garmin.status_text.startswith("Downloading activity 1 of 3")
    assert not window.garmin.stop_button.isHidden()
    assert window.garmin.stop_button.isEnabled()
    assert window.import_files_action.isEnabled()  # you can import while it downloads
    assert not window.new_profile_action.isEnabled()  # but not change profile
    assert "Wait for the current operation" in window.new_profile_action.toolTip()
    assert not window.garmin.sync_action.isEnabled()
    assert window.garmin.stop_action.isEnabled()
    gate.set()
    qtbot.waitUntil(lambda: not sync_of(window).is_syncing, timeout=WAIT_MS)
    assert window.garmin.stop_button.isHidden()
    wait_activities(qtbot, window, 3)


def test_stop_ends_the_round_keeps_what_was_downloaded_and_resumes_later(
    qtbot: QtBot, tmp_path: Path, settings: QSettings, gate: threading.Event
) -> None:
    window, _, fakes = gated_window(qtbot, tmp_path, settings, gate, activities=4)

    with qtbot.waitSignal(sync_of(window).sync_finished, timeout=WAIT_MS) as blocker:
        window.garmin.stop_button.click()
        gate.set()

    result = blocker.args[0] if blocker.args else None
    assert result is not None
    assert result.outcome.cancelled
    assert window.open_boxes == []  # stopping is not a problem: the status bar is enough
    assert window.statusBar().currentMessage() == "Synchronisation stopped"
    assert len(fakes.tokens.saved) == 1  # the account stays connected

    sync_and_wait(qtbot, window)

    wait_activities(qtbot, window, 4)


def test_authentication_lost_midway_keeps_the_downloads_and_invites_to_connect_again(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    fakes = history(4)
    fakes.source.fail_call(Call.DOWNLOAD, 2, AuthenticationRequiredError("scaduti"))
    window = window_with_profile(qtbot, tmp_path, settings, fakes)

    link_account(qtbot, window, mfa=False)

    box = open_box(qtbot, window, "syncSummary")
    assert "Garmin needs you to sign in again." in box.informativeText()
    assert {b.text() for b in box.buttons()} == {"Connect account...", "Close"}
    assert sync_of(window).account_status is AccountStatus.NEEDS_RECONNECT
    assert not window.garmin.sync_action.isEnabled()
    assert "sign in again" in window.garmin.sync_action.toolTip()
    wait_activities(qtbot, window, 1)  # what had arrived is not lost

    with qtbot.waitSignal(sync_of(window).sync_finished, timeout=WAIT_MS):
        click_button(box, "Connect account...")
        qtbot.waitUntil(lambda: window.garmin.login_dialog is not None, timeout=WAIT_MS)
        submit_login(qtbot, window)

    wait_activities(qtbot, window, 4)
    assert sync_of(window).account_status is AccountStatus.LINKED


def test_revoked_tokens_at_opening_say_so_and_lose_nothing(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    fakes = history(2)
    first = linked_window(qtbot, tmp_path, settings, fakes)
    wait_activities(qtbot, first, 2)
    close_window(qtbot, first)
    fakes.account.revoke_tokens()

    second = compose(tmp_path, settings, fakes=fakes)
    qtbot.addWidget(second)
    with qtbot.waitSignal(sync_of(second).sync_finished, timeout=WAIT_MS):
        second.start()

    box = open_box(qtbot, second, "syncSummary")
    assert "sign in again" in box.informativeText()
    assert sync_of(second).account_status is AccountStatus.NEEDS_RECONNECT
    wait_activities(qtbot, second, 2)  # the already downloaded activities are there


def test_closing_the_window_during_a_sync_waits_for_the_thread_and_then_closes(
    qtbot: QtBot, tmp_path: Path, settings: QSettings, gate: threading.Event
) -> None:
    window, _, _ = gated_window(qtbot, tmp_path, settings, gate)

    window.close()

    assert not window.is_closed  # the Garmin thread is still at the gate
    gate.set()
    qtbot.waitUntil(lambda: window.is_closed, timeout=WAIT_MS)
    assert sync_of(window).has_stopped


def test_importing_files_while_synchronising_works_and_leaves_no_database_error(
    qtbot: QtBot,
    tmp_path: Path,
    settings: QSettings,
    gate: threading.Event,
    caplog: pytest.LogCaptureFixture,
) -> None:
    inner = FakeActivitySource(remote_runs(40, 41, 42))
    gated = GatedGarmin(inner, gate)
    fakes = make_fakes(mfa_required=False, source=inner, served=gated)
    window = window_with_profile(qtbot, tmp_path, settings, fakes)
    folder = make_import_folder(tmp_path)

    with caplog.at_level(logging.WARNING):
        submit_login(qtbot, window)
        qtbot.waitUntil(gated.entered.is_set, timeout=WAIT_MS)
        with qtbot.waitSignal(window.controller.import_finished, timeout=WAIT_MS):
            window.import_folder(folder)
        assert sync_of(window).is_syncing  # the import finished while Garmin was stopped
        with qtbot.waitSignal(sync_of(window).sync_finished, timeout=WAIT_MS):
            gate.set()
        wait_activities(qtbot, window, FILES_IN_FIXTURE_FOLDER + 3)

    errors = [record for record in caplog.records if record.levelno >= logging.ERROR]
    assert errors == []
    failures = [
        b for b in window.open_boxes if b.objectName() in {"errorMessage", "unexpectedError"}
    ]
    assert failures == []


def test_a_remote_activity_already_imported_from_a_file_is_matched_not_downloaded(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    fakes = make_fakes(mfa_required=False, source=FakeActivitySource(remote_runs(0, 1)))
    window = window_with_profile(qtbot, tmp_path, settings, fakes)
    with qtbot.waitSignal(window.controller.import_finished, timeout=WAIT_MS):
        window.import_folder(make_import_folder(tmp_path))
    wait_activities(qtbot, window, FILES_IN_FIXTURE_FOLDER)

    link_account(qtbot, window, mfa=False)

    assert fakes.source.downloads == []  # matched without downloading again
    assert activity_count(window) == FILES_IN_FIXTURE_FOLDER
    assert fakes.notifier.calls == []  # nothing new: no notification


# --- Closing with a Garmin thread that does not stop -----


class _BlockedLoginAccount(FakeGarminAccount):
    """A login that stays inside the library (slow network) until the test opens the gate."""

    def __init__(self, source: FakeActivitySource, gate: threading.Event) -> None:
        super().__init__(source, mfa_required=False)
        self._gate = gate
        self.entered = threading.Event()

    def login(
        self, email: str, password: str, prompt_mfa_code: Callable[[], str | None]
    ) -> GarminSession:
        self.entered.set()
        self._gate.wait(timeout=30)
        return super().login(email, password, prompt_mfa_code)


def _garmin_thread(name: str) -> threading.Thread:
    return next(t for t in threading.enumerate() if t.name == f"ponos-garmin-{name}")


def test_closing_does_not_wait_for_a_sync_call_that_cannot_be_interrupted(
    qtbot: QtBot, tmp_path: Path, settings: QSettings, gate: threading.Event
) -> None:
    window, _, _ = gated_window(qtbot, tmp_path, settings, gate)
    sync_of(window).abandon_after_s = 0.2

    window.close()
    qtbot.waitUntil(lambda: window.is_closed, timeout=WAIT_MS)

    assert window.open_boxes == []  # no "still closing" warning
    thread = _garmin_thread("sync")
    assert thread.is_alive()  # still inside the call: dies with the process
    assert thread.daemon
    assert sync_of(window).has_stopped


def test_closing_does_not_wait_for_a_login_call_that_cannot_be_interrupted(
    qtbot: QtBot, tmp_path: Path, settings: QSettings, gate: threading.Event
) -> None:
    inner = FakeActivitySource(make_remote_history(1))
    account = _BlockedLoginAccount(inner, gate)
    base = make_fakes(source=inner)
    fakes = GarminFakes(inner, account, base.tokens, base.notifier)
    window = window_with_profile(qtbot, tmp_path, settings, fakes)
    submit_login(qtbot, window)
    qtbot.waitUntil(account.entered.is_set, timeout=WAIT_MS)
    sync_of(window).abandon_after_s = 0.2

    window.close()
    qtbot.waitUntil(lambda: window.is_closed, timeout=WAIT_MS)

    assert window.open_boxes == []
    thread = _garmin_thread("login")
    assert thread.is_alive()
    assert thread.daemon


def test_a_sync_thread_that_stops_by_itself_is_not_abandoned_early(
    qtbot: QtBot, tmp_path: Path, settings: QSettings, gate: threading.Event
) -> None:
    window, _, _ = gated_window(qtbot, tmp_path, settings, gate)  # abandon_after_s: 3 s

    window.close()

    assert not window.is_closed
    gate.set()
    qtbot.waitUntil(lambda: window.is_closed, timeout=WAIT_MS)


# --- Synchronization on opening the profile -----


def test_the_auto_sync_switch_is_on_by_default_and_off_without_a_profile(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    empty = compose(tmp_path / "empty", settings)
    qtbot.addWidget(empty)
    assert not empty.garmin.auto_sync_action.isEnabled()
    assert empty.garmin.auto_sync_action.toolTip() == "Create or open a profile first."

    window = window_with_profile(qtbot, tmp_path, settings, history(1))

    action = window.garmin.auto_sync_action
    assert action.isCheckable()
    assert action.isEnabled()
    assert action.isChecked()


def test_with_the_switch_off_opening_the_profile_does_not_synchronise_but_f5_still_does(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    fakes = history(3)
    first = linked_window(qtbot, tmp_path, settings, fakes)
    wait_activities(qtbot, first, 3)
    first.garmin.auto_sync_action.trigger()
    assert not first.garmin.auto_sync_action.isChecked()
    close_window(qtbot, first)
    fakes.source.add_activity(remote_runs(40)[0])
    resumes_before = fakes.account.resume_calls

    second = compose(tmp_path, settings, fakes=fakes)
    qtbot.addWidget(second)
    second.start()
    wait_account(qtbot, second, AccountStatus.LINKED)

    assert sync_of(second).operation is None  # no automatic round
    assert fakes.account.resume_calls == resumes_before
    assert not second.garmin.auto_sync_action.isChecked()  # the choice was remembered
    assert second.garmin.sync_action.isEnabled()
    sync_and_wait(qtbot, second)
    wait_activities(qtbot, second, 4)


def test_turning_the_switch_back_on_restores_the_automatic_round(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    fakes = history(2)
    first = linked_window(qtbot, tmp_path, settings, fakes)
    first.garmin.auto_sync_action.trigger()
    first.garmin.auto_sync_action.trigger()
    assert first.garmin.auto_sync_action.isChecked()
    close_window(qtbot, first)

    second = compose(tmp_path, settings, fakes=fakes)
    qtbot.addWidget(second)
    with qtbot.waitSignal(sync_of(second).sync_finished, timeout=WAIT_MS):
        second.start()


# --- Expired or already cancelled MFA code -----


def test_an_mfa_code_that_does_not_arrive_in_time_says_so_in_the_dialog(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    fakes = history(2, mfa_required=True)
    window = window_with_profile(qtbot, tmp_path, settings, fakes, mfa_wait_s=0.3)

    submit_login(qtbot, window)  # the code is not typed
    dialog = window.garmin.login_dialog
    assert dialog is not None
    qtbot.waitUntil(lambda: dialog.message_text != "", timeout=WAIT_MS)

    assert dialog.message_text == (
        "Time ran out waiting for the verification code. Connect the account again."
    )
    assert window.garmin.mfa_dialog is None
    assert fakes.tokens.saved == {}
    assert sync_of(window).account_status is AccountStatus.NOT_LINKED
    qtbot.waitUntil(lambda: sync_of(window).operation is None, timeout=WAIT_MS)
    assert not dialog.connect_button.isEnabled()  # the password must be retyped


class _LateMfaAccount(FakeGarminAccount):
    """Ask for the MFA code only after the test gives the go-ahead (the user has cancelled)."""

    def __init__(self, source: FakeActivitySource, proceed: threading.Event) -> None:
        super().__init__(source, mfa_required=True)
        self._proceed = proceed
        self.entered = threading.Event()

    def login(
        self, email: str, password: str, prompt_mfa_code: Callable[[], str | None]
    ) -> GarminSession:
        self.entered.set()
        self._proceed.wait(timeout=30)
        return super().login(email, password, prompt_mfa_code)


def _late_mfa_window(
    qtbot: QtBot, tmp_path: Path, settings: QSettings, gate: threading.Event
) -> tuple[MainWindow, _LateMfaAccount]:
    inner = FakeActivitySource(make_remote_history(1))
    account = _LateMfaAccount(inner, gate)
    base = make_fakes(source=inner)
    fakes = GarminFakes(inner, account, base.tokens, base.notifier)
    return window_with_profile(qtbot, tmp_path, settings, fakes), account


def test_an_mfa_request_arriving_after_the_user_cancelled_opens_no_dialog(
    qtbot: QtBot, tmp_path: Path, settings: QSettings, gate: threading.Event
) -> None:
    window, account = _late_mfa_window(qtbot, tmp_path, settings, gate)
    submit_login(qtbot, window)
    qtbot.waitUntil(account.entered.is_set, timeout=WAIT_MS)
    dialog = window.garmin.login_dialog
    assert dialog is not None
    dialog.reject()  # the user cancels while Garmin is still answering

    with qtbot.assertNotEmitted(sync_of(window).mfa_requested):
        with qtbot.waitSignal(sync_of(window).login_finished, timeout=WAIT_MS):
            gate.set()  # now the library asks for the code
        qtbot.wait(50)  # let a possible late signal arrive

    assert window.garmin.mfa_dialog is None
    assert window.open_boxes == []
    assert fakes_of(window).tokens.saved == {}


def test_a_late_mfa_signal_after_cancelling_is_ignored_by_the_controller(
    qtbot: QtBot, tmp_path: Path, settings: QSettings, gate: threading.Event
) -> None:
    window, account = _late_mfa_window(qtbot, tmp_path, settings, gate)
    controller = sync_of(window)
    submit_login(qtbot, window)
    qtbot.waitUntil(account.entered.is_set, timeout=WAIT_MS)
    controller.cancel_connect()

    with qtbot.assertNotEmitted(controller.mfa_requested):
        # The run the thread does not reproduce: the late signal arrives after the cancel.
        controller._mfa_asked.emit(controller._serial)
        qtbot.wait(50)
    gate.set()
    qtbot.waitUntil(lambda: controller.operation is None, timeout=WAIT_MS)
