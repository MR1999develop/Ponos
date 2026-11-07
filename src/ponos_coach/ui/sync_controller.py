# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Garmin account and synchronisation controller: presentation state and threads.

Lives on the UI thread. Each account operation (token check, connection,
disconnection, synchronisation) runs on its own thread, one at a time; the outcomes come back
as queued signals. Synchronisation has its own connection to the profile database
(see `sync_worker`), so import and overview stay available while it downloads.
Cancellation is cooperative (`threading.Event`); closing the window requests it and then
waits for the thread to finish (`has_stopped`).

The account threads are daemons: a call to the Garmin library cannot be interrupted
(up to 30 s of network timeout). On close, if a thread does not finish within
`abandon_after_s` of the request, it is left to end with the process exit: the
synchronisation is resumable and its SQLite connection in WAL stays consistent.
"""

from __future__ import annotations

import contextlib
import logging
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Final

from PySide6.QtCore import QObject, Signal, SignalInstance, Slot

from ponos_coach.activities.ports import ActivityArchive
from ponos_coach.activities.sync import SyncMode, SyncProgress
from ponos_coach.profiles.domain import ProfileId
from ponos_coach.profiles.ports import ProfileStorage
from ponos_coach.ui.diagnostics import exception_chain
from ponos_coach.ui.sync_model import (
    AccountStatus,
    CommandInputs,
    LoginEnd,
    LoginFailure,
    Operation,
    SyncCommands,
    SyncFinished,
    SyncNotStarted,
    SyncResult,
    account_status_after,
    commands_for,
    new_activity_count,
)
from ponos_coach.ui.sync_text import notification_texts
from ponos_coach.ui.sync_worker import (
    Credentials,
    MfaMailbox,
    SyncServices,
    SyncWorker,
)

_LOG = logging.getLogger(__name__)

__all__ = ["ABANDON_AFTER_S", "SyncController"]

ABANDON_AFTER_S: Final = (
    3.0  # beyond that, a non-interruptible Garmin thread does not hold up closing
)


def _post(signal: SignalInstance, *args: object) -> None:
    """Emit a signal from a worker thread; if the object no longer exists (app closing)
    nobody is listening and the outcome is lost harmlessly."""
    with contextlib.suppress(RuntimeError):
        signal.emit(*args)


class SyncController(QObject):
    """Garmin account of the open profile and synchronisation: state, commands and threads."""

    account_changed = Signal(object)  # AccountStatus
    state_changed = Signal()  # the operation in progress or its availability changes
    progress_changed = Signal(object)  # SyncProgress | None
    mfa_requested = Signal()  # the code is needed: the UI answers with `answer_mfa`
    login_finished = Signal(object)  # LoginResult
    disconnect_finished = Signal(bool)  # true if the tokens were deleted
    sync_finished = Signal(object)  # SyncResult

    # Emitted from the worker threads; the first argument is the operation number.
    _checked = Signal(int, object)
    _logged_in = Signal(int, object)
    _logged_out = Signal(int, bool)
    _progressed = Signal(int, object)
    _synced = Signal(int, object)
    _mfa_asked = Signal(int)

    def __init__(
        self,
        services: SyncServices,
        storage: ProfileStorage,
        archive_for: Callable[[Path], ActivityArchive],
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._services = services
        self._worker = SyncWorker(services, storage, archive_for)
        self._profile_id: ProfileId | None = None
        self._folder: Path | None = None
        self._status = AccountStatus.UNKNOWN
        self._status_profile: ProfileId | None = None
        self._operation: Operation | None = None
        self._operation_profile: ProfileId | None = None
        self._serial = 0
        self._cancel = threading.Event()
        self._thread: threading.Thread | None = None
        self._mailbox: MfaMailbox | None = None
        self._progress: SyncProgress | None = None
        self._auto_pending = False
        self._closing = False
        self._shutdown_at: float | None = None
        self.abandon_after_s = ABANDON_AFTER_S
        self._checked.connect(self._on_checked)
        self._logged_in.connect(self._on_logged_in)
        self._logged_out.connect(self._on_logged_out)
        self._progressed.connect(self._on_progressed)
        self._synced.connect(self._on_synced)
        self._mfa_asked.connect(self._on_mfa_asked)

    # --- Stato ------------------------------------------------------------------------------

    @property
    def account_status(self) -> AccountStatus:
        """What is known about the open profile's account."""
        return self._status

    @property
    def operation(self) -> Operation | None:
        """The job in progress, if any."""
        return self._operation

    @property
    def is_syncing(self) -> bool:
        """True while a synchronisation is in progress (the profile cannot be changed)."""
        return self._operation is Operation.SYNC

    @property
    def progress(self) -> SyncProgress | None:
        """The latest progress of the synchronisation in progress."""
        return self._progress

    @property
    def commands(self) -> SyncCommands:
        """Which Garmin menu commands are available, and why not."""
        return commands_for(
            CommandInputs(
                self._profile_id is not None,
                self._status,
                self._operation,
                self._closing,
            )
        )

    @property
    def has_stopped(self) -> bool:
        """True if no account thread is running any more or, when closing was requested more than
        `abandon_after_s` ago, if the remaining one cannot be interrupted (it stays in a network
        wait
        and dies with the process)."""
        thread = self._thread
        if thread is None or not thread.is_alive():
            return True
        return (
            self._shutdown_at is not None
            and time.monotonic() - self._shutdown_at >= self.abandon_after_s
        )

    @property
    def auto_sync_enabled(self) -> bool:
        """True if synchronisation starts by itself when the profile opens (choice per profile)."""
        profile_id = self._profile_id
        return profile_id is None or self._services.preferences.auto_sync(profile_id)

    def set_auto_sync(self, *, enabled: bool) -> None:
        """Remember for the open profile whether synchronisation starts by itself on opening.
        Turning it off also stops the automatic round that may still be waiting."""
        profile_id = self._profile_id
        if profile_id is None:
            return
        self._services.preferences.set_auto_sync(profile_id, enabled=enabled)
        if not enabled:
            self._auto_pending = False
        self.state_changed.emit()

    # --- Profilo ----------------------------------------------------------------------------

    def profile_opened(self, profile_id: ProfileId, folder: Path) -> None:
        """A profile has opened: its account is checked and, if it is connected and the user has not
        turned off synchronisation on opening, it is synchronised.

        The check starts as soon as no other operation is in progress.
        """
        self._profile_id = profile_id
        self._folder = folder
        self._status_profile = None
        if self._services.tokens is None:
            self._status = AccountStatus.KEYCHAIN_UNAVAILABLE
            self._status_profile = profile_id
            self._auto_pending = False
        else:
            self._status = AccountStatus.UNKNOWN
            self._auto_pending = self._services.preferences.auto_sync(profile_id)
        self.account_changed.emit(self._status)
        self.state_changed.emit()
        self._check_if_needed()

    # --- Comandi ----------------------------------------------------------------------------

    def connect_account(self, credentials: Credentials) -> bool:
        """Log in to Garmin with email and password (and MFA code if needed) and save the tokens.

        The password is not kept: it lives only on the login thread until Garmin answers.
        False (nothing happens) if the command is not available.
        """
        profile_id = self._profile_id
        if profile_id is None or not self.commands.connect.enabled:
            return False
        mailbox = MfaMailbox(self._services.mfa_wait_s)
        self._mailbox = mailbox

        def work(serial: int, cancel: threading.Event) -> None:
            result = self._worker.log_in(
                profile_id,
                credentials,
                lambda: self._ask_mfa(serial, mailbox, cancel),
                cancel,
                mfa_timed_out=lambda: mailbox.timed_out,
            )
            _post(self._logged_in, serial, result)

        self._launch(Operation.LOGIN, profile_id, work)
        return True

    def answer_mfa(self, code: str | None) -> None:
        """The answer to the MFA code request (`None`: the user cancels)."""
        if self._mailbox is not None:
            self._mailbox.answer(code)

    def cancel_connect(self) -> None:
        """Cancel the connection in progress: the wait for the code ends and nothing is saved."""
        if self._operation is Operation.LOGIN:
            self._cancel.set()
            if self._mailbox is not None:
                self._mailbox.cancel()

    def disconnect_account(self) -> bool:
        """Delete the profile's tokens. Activities already downloaded stay."""
        profile_id = self._profile_id
        if profile_id is None or not self.commands.disconnect.enabled:
            return False

        def work(serial: int, _cancel: threading.Event) -> None:
            _post(self._logged_out, serial, self._worker.log_out(profile_id))

        self._launch(Operation.LOGOUT, profile_id, work)
        return True

    def start_sync(self, mode: SyncMode = SyncMode.SYNC, *, automatic: bool = False) -> bool:
        """Start a synchronisation round. False (nothing happens) if the command is not
        available."""
        profile_id, folder = self._profile_id, self._folder
        if profile_id is None or folder is None or not self.commands.sync.enabled:
            return False

        def work(serial: int, cancel: threading.Event) -> None:
            result = self._worker.synchronize(
                profile_id,
                folder,
                mode,
                automatic=automatic,
                cancelled=cancel,
                report=lambda progress: _post(self._progressed, serial, progress),
            )
            _post(self._synced, serial, result)

        self._progress = None
        self._launch(Operation.SYNC, profile_id, work)
        return True

    def cancel_sync(self) -> None:
        """Ask to stop the synchronisation in progress (within one call)."""
        if self._operation is Operation.SYNC:
            self._cancel.set()

    def begin_shutdown(self) -> None:
        """First step of closing: stop what is running. It does not wait: whoever closes watches
        `has_stopped`. From here on no operation starts; can be repeated."""
        self._closing = True
        if self._shutdown_at is None:
            self._shutdown_at = time.monotonic()
        self._cancel.set()
        if self._mailbox is not None:
            self._mailbox.cancel()
        self.state_changed.emit()

    # --- Starting the threads -------------------------------------------------------------------

    def _launch(
        self,
        operation: Operation,
        profile_id: ProfileId,
        work: Callable[[int, threading.Event], None],
    ) -> None:
        self._serial += 1
        serial = self._serial
        self._cancel = threading.Event()
        self._operation = operation
        self._operation_profile = profile_id
        thread = threading.Thread(
            target=self._guarded,
            args=(work, serial, self._cancel),
            name=f"ponos-garmin-{operation.value}",
            daemon=True,  # a non-interruptible network call does not hold up exit
        )
        self._thread = thread
        self.state_changed.emit()
        thread.start()

    @staticmethod
    def _guarded(
        work: Callable[[int, threading.Event], None], serial: int, cancel: threading.Event
    ) -> None:
        """The thread body: an unexpected failure is logged (type only) and does not bring it
        down."""
        try:
            work(serial, cancel)
        except Exception as error:  # thread boundary; the jobs already return their outcomes
            _LOG.error("Garmin: the worker failed: %s", exception_chain(error))

    def _ask_mfa(self, serial: int, mailbox: MfaMailbox, cancel: threading.Event) -> str | None:
        """From the login thread: ask the UI for the code and wait for it. If the user has already
        cancelled the connection nothing is asked."""
        if cancel.is_set():
            return None
        _post(self._mfa_asked, serial)
        return mailbox.wait()

    def _check_if_needed(self) -> None:
        profile_id = self._profile_id
        if (
            profile_id is None
            or self._closing
            or self._operation is not None
            or self._status_profile == profile_id
        ):
            return

        def work(serial: int, _cancel: threading.Event) -> None:
            _post(self._checked, serial, self._worker.check(profile_id))

        self._launch(Operation.CHECK, profile_id, work)

    # --- Outcomes (on the UI thread) --------------------------------------------------------

    def _finish(self, serial: int) -> bool:
        """Close the operation `serial`. False if the outcome is not of the one in progress
        (ignored)."""
        if serial != self._serial or self._operation is None or self._closing:
            return False
        self._operation = None
        self._mailbox = None
        self._progress = None
        return True

    def _is_current_profile(self) -> bool:
        return self._operation_profile is not None and self._operation_profile == self._profile_id

    def _set_status(self, status: AccountStatus) -> None:
        self._status = status
        self._status_profile = self._profile_id
        self.account_changed.emit(status)

    @Slot(int, object)
    def _on_checked(self, serial: int, status: object) -> None:
        current = self._is_current_profile()
        if not self._finish(serial) or not isinstance(status, AccountStatus):
            return
        if current:
            self._set_status(status)
            auto, self._auto_pending = self._auto_pending, False
            if auto and status is AccountStatus.LINKED:
                self.start_sync(SyncMode.SYNC, automatic=True)
        self.state_changed.emit()
        self._check_if_needed()

    @Slot(int, object)
    def _on_logged_in(self, serial: int, result: object) -> None:
        current = self._is_current_profile()
        if not self._finish(serial) or not isinstance(result, LoginEnd | LoginFailure):
            return
        connected = result is LoginEnd.CONNECTED and current
        if connected:
            self._set_status(AccountStatus.LINKED)
        self.state_changed.emit()
        self.login_finished.emit(result)
        if connected:
            self.start_sync(SyncMode.SYNC)  # the first round: the whole history
        self._check_if_needed()

    @Slot(int, bool)
    def _on_logged_out(self, serial: int, deleted: bool) -> None:
        current = self._is_current_profile()
        if not self._finish(serial):
            return
        if current:
            self._set_status(AccountStatus.NOT_LINKED if deleted else AccountStatus.UNKNOWN)
            if not deleted:
                self._status_profile = None  # the token state must be re-read
        self.state_changed.emit()
        self.disconnect_finished.emit(deleted)
        self._check_if_needed()

    @Slot(int, object)
    def _on_progressed(self, serial: int, progress: object) -> None:
        if serial == self._serial and self.is_syncing and isinstance(progress, SyncProgress):
            self._progress = progress
            self.progress_changed.emit(progress)

    @Slot(int, object)
    def _on_mfa_asked(self, serial: int) -> None:
        if (
            serial == self._serial
            and self._operation is Operation.LOGIN
            and not self._closing
            and not self._cancel.is_set()  # already cancelled: the incoming request is ignored
        ):
            self.mfa_requested.emit()

    @Slot(int, object)
    def _on_synced(self, serial: int, result: object) -> None:
        current = self._is_current_profile()
        if not self._finish(serial) or not isinstance(result, SyncFinished | SyncNotStarted):
            return
        if current:
            after = account_status_after(result, self._status)
            if after is not self._status:
                self._set_status(after)
        self.progress_changed.emit(None)
        self.state_changed.emit()
        self.sync_finished.emit(result)
        self._notify(result)
        self._check_if_needed()

    def _notify(self, result: SyncResult) -> None:
        """System notification with the number of new activities, if there are any. It does not
        block and
        never makes the synchronisation fail."""
        count = new_activity_count(result)
        if count <= 0:
            return
        title, message = notification_texts(count)
        try:
            self._services.notifier.notify(title, message)
        except Exception as error:  # the contract says it does not raise; here it is enforced
            _LOG.warning("Garmin: the notification failed: %s", exception_chain(error))
