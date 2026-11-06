# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Vista dell'account Garmin nella finestra principale: menu, barra di stato e dialoghi.

Vista umile: le decisioni (quali comandi sono disponibili, che cosa dice il riepilogo, se serve
una finestra) stanno in `sync_model` e `sync_text`; qui ci sono solo widget e comandi.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QObject, Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QLabel,
    QMainWindow,
    QMenuBar,
    QMessageBox,
    QProgressBar,
    QPushButton,
)

from ponos_coach.activities.sync import SyncMode, SyncPhase, SyncProgress
from ponos_coach.ui.garmin_dialogs import GarminLoginDialog, MfaCodeDialog
from ponos_coach.ui.sync_controller import SyncController
from ponos_coach.ui.sync_model import (
    Availability,
    LoginEnd,
    LoginFailure,
    LoginResult,
    SyncResult,
    needs_attention,
    needs_reconnect,
)
from ponos_coach.ui.sync_text import (
    disabled_reason_text,
    headline_text,
    login_failure_text,
    progress_text,
    summary_message,
)
from ponos_coach.ui.sync_worker import Credentials
from ponos_coach.ui.tokens import SPACE_2, SPACE_4, STATUS_PROGRESS_WIDTH, Theme

_STATUS_MESSAGE_MS = 8000


class GarminUi(QObject):
    """Il menu "Garmin", l'avanzamento nella barra di stato e i dialoghi dell'account."""

    def __init__(
        self,
        window: QMainWindow,
        controller: SyncController,
        theme: Callable[[], Theme],
        remember_box: Callable[[QMessageBox], None],
    ) -> None:
        super().__init__(window)
        self._window = window
        self._controller = controller
        self._theme = theme
        self._remember_box = remember_box
        self._login: GarminLoginDialog | None = None
        self._mfa: MfaCodeDialog | None = None
        self._build_actions()
        self._build_status_widgets()
        controller.state_changed.connect(self.refresh)
        controller.account_changed.connect(self.refresh)
        controller.progress_changed.connect(self._on_progress)
        controller.mfa_requested.connect(self._ask_mfa_code)
        controller.login_finished.connect(self._on_login_finished)
        controller.disconnect_finished.connect(self._on_disconnect_finished)
        controller.sync_finished.connect(self._on_sync_finished)
        self.refresh()

    # --- Costruzione -----

    def _action(self, text: str, shortcut: str, slot: Callable[[], object]) -> QAction:
        action = QAction(text, self._window)
        action.setShortcut(QKeySequence(shortcut))
        action.triggered.connect(slot)
        return action

    def _build_actions(self) -> None:
        self._connect_action = self._action(
            self.tr("Connect Garmin account..."), "Ctrl+G", self.connect_account
        )
        self._disconnect_action = self._action(
            self.tr("Disconnect Garmin account"), "Ctrl+Shift+G", self.confirm_disconnect
        )
        self._sync_action = self._action(
            self.tr("Synchronise"), "F5", lambda: self._controller.start_sync(SyncMode.SYNC)
        )
        self._verify_action = self._action(
            self.tr("Full check"),
            "Ctrl+F5",
            lambda: self._controller.start_sync(SyncMode.VERIFY),
        )
        self._stop_action = self._action(
            self.tr("Stop synchronisation"), "Shift+F5", self._controller.cancel_sync
        )
        self._actions: tuple[tuple[QAction, Callable[[], Availability]], ...] = (
            (self._connect_action, lambda: self._controller.commands.connect),
            (self._disconnect_action, lambda: self._controller.commands.disconnect),
            (self._sync_action, lambda: self._controller.commands.sync),
            (self._verify_action, lambda: self._controller.commands.verify),
            (self._stop_action, lambda: self._controller.commands.stop),
        )

    def build_menu(self, menu_bar: QMenuBar) -> None:
        """Aggiunge il menu "Garmin" alla barra dei menu."""
        menu = menu_bar.addMenu(self.tr("Garmin"))
        menu.addAction(self._connect_action)
        menu.addAction(self._disconnect_action)
        menu.addSeparator()
        menu.addAction(self._sync_action)
        menu.addAction(self._verify_action)
        menu.addAction(self._stop_action)
        self._verify_action.setStatusTip(
            self.tr("List the whole Garmin history to find activities uploaded late.")
        )

    def _build_status_widgets(self) -> None:
        window = self._window
        self._label = QLabel(window)
        self._label.setObjectName("syncStatus")
        self._label.setTextFormat(Qt.TextFormat.PlainText)
        self._label.setContentsMargins(SPACE_2, 0, SPACE_4, 0)
        self._bar = QProgressBar(window)
        self._bar.setTextVisible(False)
        self._bar.setFixedWidth(STATUS_PROGRESS_WIDTH)
        self._bar.setAccessibleName(self.tr("Synchronisation progress"))
        self._stop_button = QPushButton(self.tr("Stop sync"), window)
        self._stop_button.setObjectName("syncStop")
        self._stop_button.clicked.connect(self._controller.cancel_sync)
        self._widgets = (self._label, self._bar, self._stop_button)
        status_bar = window.statusBar()
        for widget in self._widgets:
            status_bar.addPermanentWidget(widget)
            widget.setVisible(False)

    # --- Accessori per i test -----

    @property
    def connect_action(self) -> QAction:
        """Azione "Collega account Garmin..."."""
        return self._connect_action

    @property
    def disconnect_action(self) -> QAction:
        """Azione "Scollega account Garmin"."""
        return self._disconnect_action

    @property
    def sync_action(self) -> QAction:
        """Azione "Sincronizza" (F5)."""
        return self._sync_action

    @property
    def verify_action(self) -> QAction:
        """Azione "Verifica completa"."""
        return self._verify_action

    @property
    def stop_action(self) -> QAction:
        """Azione "Interrompi la sincronizzazione"."""
        return self._stop_action

    @property
    def stop_button(self) -> QPushButton:
        """Il bottone "Interrompi" della barra di stato."""
        return self._stop_button

    @property
    def status_text(self) -> str:
        """Il testo dell'avanzamento nella barra di stato (vuoto se non si sincronizza)."""
        return "" if self._label.isHidden() else self._label.text()

    @property
    def login_dialog(self) -> GarminLoginDialog | None:
        """Il dialogo di accesso aperto, se c'è."""
        return self._login

    @property
    def mfa_dialog(self) -> MfaCodeDialog | None:
        """Il dialogo del codice MFA aperto, se c'è."""
        return self._mfa

    # --- Stato dei comandi -----

    def refresh(self) -> None:
        """Allinea comandi e barra di stato a ciò che dice il controller."""
        for action, availability in self._actions:
            current = availability()
            action.setEnabled(current.enabled)
            action.setToolTip(
                "" if current.reason is None else disabled_reason_text(current.reason)
            )
        self._stop_button.setEnabled(self._controller.commands.stop.enabled)
        syncing = self._controller.is_syncing
        for widget in self._widgets:
            widget.setVisible(syncing)
        if syncing and self._controller.progress is None:
            self._show_progress(None)

    def _on_progress(self, progress: object) -> None:
        if isinstance(progress, SyncProgress):
            self._show_progress(progress)

    def _show_progress(self, progress: SyncProgress | None) -> None:
        if progress is None:
            self._label.setText(self.tr("Starting synchronisation..."))
            self._bar.setRange(0, 0)
            return
        self._label.setText(progress_text(progress))
        if progress.total:
            self._bar.setRange(0, progress.total)
            self._bar.setValue(min(progress.done, progress.total))
        elif progress.phase is SyncPhase.LISTING:
            self._bar.setRange(0, 0)  # lunghezza ignota

    # --- Collegamento -----

    def connect_account(self) -> None:
        """Apre il dialogo di accesso (nessun effetto se il comando non è disponibile)."""
        if self._login is not None or not self._controller.commands.connect.enabled:
            return
        dialog = GarminLoginDialog(self._theme(), self._window)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.submitted.connect(self._submit_credentials)
        dialog.cancel_requested.connect(self._controller.cancel_connect)
        dialog.finished.connect(self._on_login_dialog_closed)
        self._login = dialog
        dialog.open()

    def _submit_credentials(self, credentials: object) -> None:
        if not isinstance(credentials, Credentials):
            return
        if not self._controller.connect_account(credentials) and self._login is not None:
            self._login.show_failure(login_failure_text(LoginFailure.OTHER))

    def _on_login_dialog_closed(self) -> None:
        self._login = None

    def _ask_mfa_code(self) -> None:
        dialog = MfaCodeDialog(self._login or self._window)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.answered.connect(self._answer_mfa)
        dialog.finished.connect(self._on_mfa_dialog_closed)
        self._mfa = dialog
        dialog.open()

    def _answer_mfa(self, code: object) -> None:
        self._controller.answer_mfa(code if isinstance(code, str) else None)

    def _on_mfa_dialog_closed(self) -> None:
        self._mfa = None

    def _on_login_finished(self, result: LoginResult) -> None:
        if self._mfa is not None:
            self._mfa.reject()
        dialog = self._login
        if isinstance(result, LoginFailure):
            if dialog is not None:
                dialog.show_failure(login_failure_text(result))
            return
        if dialog is not None:
            dialog.set_busy(False)
            if result is LoginEnd.CONNECTED:
                dialog.accept()
            else:
                dialog.reject()
        if result is LoginEnd.CONNECTED:
            self._window.statusBar().showMessage(
                self.tr("Garmin account connected"), _STATUS_MESSAGE_MS
            )

    # --- Scollegamento -----

    def confirm_disconnect(self) -> None:
        """Chiede conferma (con il verbo sul bottone) e scollega l'account."""
        if not self._controller.commands.disconnect.enabled:
            return
        box = QMessageBox(self._window)
        box.setObjectName("disconnectConfirmation")
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle(self.tr("Disconnect Garmin account"))
        box.setTextFormat(Qt.TextFormat.PlainText)
        box.setText(self.tr("Disconnect the Garmin account from this profile?"))
        box.setInformativeText(
            self.tr(
                "The sign-in is deleted from this computer. Activities already downloaded "
                "stay in the profile. You can connect again at any time."
            )
        )
        disconnect = box.addButton(self.tr("Disconnect"), QMessageBox.ButtonRole.DestructiveRole)
        cancel = box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(cancel)
        box.setEscapeButton(cancel)
        box.buttonClicked.connect(lambda clicked: self._on_disconnect_answer(clicked, disconnect))
        self._remember_box(box)
        box.open()

    def _on_disconnect_answer(self, clicked: object, disconnect: object) -> None:
        if clicked is disconnect:
            self._controller.disconnect_account()

    def _on_disconnect_finished(self, deleted: bool) -> None:
        if deleted:
            self._window.statusBar().showMessage(
                self.tr("Garmin account disconnected"), _STATUS_MESSAGE_MS
            )
            return
        box = QMessageBox(self._window)
        box.setObjectName("disconnectFailed")
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle(self.tr("The account was not disconnected"))
        box.setTextFormat(Qt.TextFormat.PlainText)
        box.setText(self.tr("The account was not disconnected"))
        box.setInformativeText(
            self.tr(
                "The sign-in could not be deleted from the system keychain.\n\n"
                "Check that the keychain is unlocked, then try again."
            )
        )
        box.setStandardButtons(QMessageBox.StandardButton.Ok)
        self._remember_box(box)
        box.open()

    # --- Riepilogo -----

    def _on_sync_finished(self, result: SyncResult) -> None:
        self._window.statusBar().showMessage(headline_text(result), _STATUS_MESSAGE_MS)
        if needs_attention(result):
            self._show_summary(result)

    def _show_summary(self, result: SyncResult) -> None:
        message = summary_message(result)
        box = QMessageBox(self._window)
        box.setObjectName("syncSummary")
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle(self.tr("Garmin synchronisation"))
        box.setTextFormat(Qt.TextFormat.PlainText)
        box.setText(message.title)
        box.setInformativeText(f"{message.what}\n\n{message.remedy}")
        reconnect: QPushButton | None = None
        if needs_reconnect(result):
            reconnect = box.addButton(
                self.tr("Connect account..."), QMessageBox.ButtonRole.AcceptRole
            )
        close = box.addButton(QMessageBox.StandardButton.Close)
        box.setDefaultButton(close if reconnect is None else reconnect)
        box.setEscapeButton(close)
        if reconnect is not None:
            reconnect.clicked.connect(self.connect_account)
        self._remember_box(box)
        box.open()
