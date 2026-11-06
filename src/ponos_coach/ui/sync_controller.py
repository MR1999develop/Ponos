# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Controller dell'account Garmin e della sincronizzazione: stato di presentazione e thread.

Vive nel thread della UI. Ogni operazione dell'account (controllo dei token, collegamento,
scollegamento, sincronizzazione) gira in un thread proprio, una alla volta; gli esiti tornano
come segnali accodati. La sincronizzazione ha una sua connessione al database del profilo
(vedi `sync_worker`), quindi import e panoramica restano disponibili mentre scarica.
L'annullamento è cooperativo (`threading.Event`); la chiusura della finestra lo chiede e poi
attende che il thread finisca (`has_stopped`).
"""

from __future__ import annotations

import contextlib
import logging
import threading
from collections.abc import Callable
from pathlib import Path

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

__all__ = ["SyncController"]


def _post(signal: SignalInstance, *args: object) -> None:
    """Emette un segnale da un thread di lavoro; se l'oggetto non c'è più (app in chiusura)
    nessuno è in ascolto e l'esito si perde senza danni."""
    with contextlib.suppress(RuntimeError):
        signal.emit(*args)


class SyncController(QObject):
    """Account Garmin del profilo aperto e sincronizzazione: stato, comandi e thread."""

    account_changed = Signal(object)  # AccountStatus
    state_changed = Signal()  # cambia l'operazione in corso o la sua disponibilità
    progress_changed = Signal(object)  # SyncProgress | None
    mfa_requested = Signal()  # serve il codice: la UI risponde con `answer_mfa`
    login_finished = Signal(object)  # LoginResult
    disconnect_finished = Signal(bool)  # vero se i token sono stati cancellati
    sync_finished = Signal(object)  # SyncResult

    # Emessi dai thread di lavoro; il primo argomento è il numero dell'operazione.
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
        self._checked.connect(self._on_checked)
        self._logged_in.connect(self._on_logged_in)
        self._logged_out.connect(self._on_logged_out)
        self._progressed.connect(self._on_progressed)
        self._synced.connect(self._on_synced)
        self._mfa_asked.connect(self._on_mfa_asked)

    # --- Stato ------------------------------------------------------------------------------

    @property
    def account_status(self) -> AccountStatus:
        """Che cosa si sa dell'account del profilo aperto."""
        return self._status

    @property
    def operation(self) -> Operation | None:
        """Il lavoro in corso, se c'è."""
        return self._operation

    @property
    def is_syncing(self) -> bool:
        """Vero mentre una sincronizzazione è in corso (il profilo non si può cambiare)."""
        return self._operation is Operation.SYNC

    @property
    def progress(self) -> SyncProgress | None:
        """L'ultimo avanzamento della sincronizzazione in corso."""
        return self._progress

    @property
    def commands(self) -> SyncCommands:
        """Quali comandi del menu Garmin sono disponibili, e perché no."""
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
        """Vero se nessun thread dell'account è più in esecuzione."""
        return self._thread is None or not self._thread.is_alive()

    # --- Profilo ----------------------------------------------------------------------------

    def profile_opened(self, profile_id: ProfileId, folder: Path) -> None:
        """Un profilo si è aperto: si controlla il suo account e, se è collegato, si sincronizza.

        Il controllo parte appena nessun'altra operazione è in corso.
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
            self._auto_pending = True
        self.account_changed.emit(self._status)
        self.state_changed.emit()
        self._check_if_needed()

    # --- Comandi ----------------------------------------------------------------------------

    def connect_account(self, credentials: Credentials) -> bool:
        """Accede a Garmin con email e password (e codice MFA se serve) e salva i token.

        La password non si conserva: vive solo nel thread del login finché Garmin risponde.
        Falso (nulla accade) se il comando non è disponibile.
        """
        profile_id = self._profile_id
        if profile_id is None or not self.commands.connect.enabled:
            return False
        mailbox = MfaMailbox(self._services.mfa_wait_s)
        self._mailbox = mailbox

        def work(serial: int, cancel: threading.Event) -> None:
            result = self._worker.log_in(
                profile_id, credentials, lambda: self._ask_mfa(serial, mailbox), cancel
            )
            _post(self._logged_in, serial, result)

        self._launch(Operation.LOGIN, profile_id, work)
        return True

    def answer_mfa(self, code: str | None) -> None:
        """La risposta alla richiesta del codice MFA (`None`: l'utente annulla)."""
        if self._mailbox is not None:
            self._mailbox.answer(code)

    def cancel_connect(self) -> None:
        """Annulla il collegamento in corso: l'attesa del codice finisce e nulla si salva."""
        if self._operation is Operation.LOGIN:
            self._cancel.set()
            if self._mailbox is not None:
                self._mailbox.cancel()

    def disconnect_account(self) -> bool:
        """Cancella i token del profilo. Le attività già scaricate restano."""
        profile_id = self._profile_id
        if profile_id is None or not self.commands.disconnect.enabled:
            return False

        def work(serial: int, _cancel: threading.Event) -> None:
            _post(self._logged_out, serial, self._worker.log_out(profile_id))

        self._launch(Operation.LOGOUT, profile_id, work)
        return True

    def start_sync(self, mode: SyncMode = SyncMode.SYNC, *, automatic: bool = False) -> bool:
        """Avvia un giro di sincronizzazione. Falso (nulla accade) se il comando non è
        disponibile."""
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
        """Chiede di fermare la sincronizzazione in corso (entro una chiamata)."""
        if self._operation is Operation.SYNC:
            self._cancel.set()

    def begin_shutdown(self) -> None:
        """Primo tempo della chiusura: ferma ciò che gira. Non attende: chi chiude guarda
        `has_stopped`. Da qui nessuna operazione parte più; si può ripetere."""
        self._closing = True
        self._cancel.set()
        if self._mailbox is not None:
            self._mailbox.cancel()
        self.state_changed.emit()

    # --- Avvio dei thread -------------------------------------------------------------------

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
        )
        self._thread = thread
        self.state_changed.emit()
        thread.start()

    @staticmethod
    def _guarded(
        work: Callable[[int, threading.Event], None], serial: int, cancel: threading.Event
    ) -> None:
        """Il corpo del thread: un guasto imprevisto si registra (solo il tipo) e non lo abbatte."""
        try:
            work(serial, cancel)
        except Exception as error:  # confine del thread; i lavori restituiscono già i loro esiti
            _LOG.error("Garmin: the worker failed: %s", exception_chain(error))

    def _ask_mfa(self, serial: int, mailbox: MfaMailbox) -> str | None:
        """Dal thread del login: chiede il codice alla UI e lo aspetta."""
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

    # --- Esiti (nel thread della UI) --------------------------------------------------------

    def _finish(self, serial: int) -> bool:
        """Chiude l'operazione `serial`. Falso se l'esito non è di quella in corso (si ignora)."""
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
            self.start_sync(SyncMode.SYNC)  # il primo giro: tutto lo storico
        self._check_if_needed()

    @Slot(int, bool)
    def _on_logged_out(self, serial: int, deleted: bool) -> None:
        current = self._is_current_profile()
        if not self._finish(serial):
            return
        if current:
            self._set_status(AccountStatus.NOT_LINKED if deleted else AccountStatus.UNKNOWN)
            if not deleted:
                self._status_profile = None  # lo stato dei token è da rileggere
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
        if serial == self._serial and self._operation is Operation.LOGIN and not self._closing:
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
        """Notifica di sistema con il numero di attività nuove, se ce ne sono. Non blocca e non
        fa mai fallire la sincronizzazione."""
        count = new_activity_count(result)
        if count <= 0:
            return
        title, message = notification_texts(count)
        try:
            self._services.notifier.notify(title, message)
        except Exception as error:  # il contratto dice che non solleva; qui si fa rispettare
            _LOG.warning("Garmin: the notification failed: %s", exception_chain(error))
