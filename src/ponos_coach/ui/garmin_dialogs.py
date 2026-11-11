# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Garmin account dialogs: email and password, and verification code (MFA).

Humble views: they collect the text and hand it over with a signal. The password and the code are
not
kept: the field is cleared as soon as the text has been read, and `Credentials` does not show them
even in `repr`. The real login runs on a thread (`SyncController`); the dialog stays open
and busy until Garmin answers, so an error appears where the user is looking.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ponos_coach.ui.sync_worker import Credentials
from ponos_coach.ui.tokens import DIALOG_MIN_WIDTH, SPACE_2, SPACE_4, Theme, tokens_for

_SECRET_HINTS = (
    Qt.InputMethodHint.ImhHiddenText
    | Qt.InputMethodHint.ImhNoPredictiveText
    | Qt.InputMethodHint.ImhNoAutoUppercase
    | Qt.InputMethodHint.ImhSensitiveData
)


class GarminLoginDialog(QDialog):
    """Garmin account email and password; "Connect" is available only with both."""

    submitted = Signal(object)  # Credentials
    cancel_requested = Signal()  # closed while the login is in progress

    def __init__(self, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._error_color = tokens_for(theme).error
        self._busy = False
        self.setWindowTitle(self.tr("Connect Garmin account"))
        self.setMinimumWidth(DIALOG_MIN_WIDTH)
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self._build()
        self._update_buttons()

    def _build(self) -> None:
        self._intro = QLabel(
            self.tr(
                "Your password goes only to Garmin and is never saved. Ponos Coach keeps "
                "just the sign-in tokens, protected by your system."
            ),
            self,
        )
        self._intro.setWordWrap(True)
        self._intro.setTextFormat(Qt.TextFormat.PlainText)
        self._email = QLineEdit(self)
        self._email.setObjectName("garminEmail")
        self._email.setInputMethodHints(
            Qt.InputMethodHint.ImhEmailCharactersOnly | Qt.InputMethodHint.ImhNoAutoUppercase
        )
        self._password = QLineEdit(self)
        self._password.setObjectName("garminPassword")
        self._password.setEchoMode(QLineEdit.EchoMode.Password)
        self._password.setInputMethodHints(_SECRET_HINTS)
        for edit in (self._email, self._password):
            edit.textChanged.connect(self._update_buttons)
            edit.returnPressed.connect(self._submit)
        self._message = QLabel(self)
        self._message.setObjectName("garminLoginMessage")
        self._message.setWordWrap(True)
        self._message.setTextFormat(Qt.TextFormat.PlainText)
        self._message.setStyleSheet(f"color: {self._error_color};")
        self._message.setVisible(False)
        self._progress = QProgressBar(self)
        self._progress.setRange(0, 0)
        self._progress.setTextVisible(False)
        self._progress.setVisible(False)
        self._progress_label = QLabel(self.tr("Signing in to Garmin..."), self)
        self._progress_label.setVisible(False)

        form = QFormLayout()
        form.setVerticalSpacing(SPACE_2)
        form.addRow(self.tr("Email"), self._email)
        form.addRow(self.tr("Password"), self._password)
        self._buttons = QDialogButtonBox(self)
        self._connect = self._buttons.addButton(
            self.tr("Connect"), QDialogButtonBox.ButtonRole.AcceptRole
        )
        self._connect.setDefault(True)
        self._cancel = self._buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        self._connect.clicked.connect(self._submit)
        self._cancel.clicked.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.setSpacing(SPACE_4)
        layout.addWidget(self._intro)
        layout.addLayout(form)
        layout.addWidget(self._message)
        layout.addWidget(self._progress_label)
        layout.addWidget(self._progress)
        layout.addWidget(self._buttons)

    # --- Accessors for tests and for the view -------------------------------------------------

    @property
    def email_edit(self) -> QLineEdit:
        """The email field."""
        return self._email

    @property
    def password_edit(self) -> QLineEdit:
        """The password field (hidden)."""
        return self._password

    @property
    def connect_button(self) -> QPushButton:
        """The "Connect" button."""
        return self._connect

    @property
    def message_text(self) -> str:
        """The error shown (empty if there is none)."""
        return "" if self._message.isHidden() else self._message.text()

    @property
    def is_busy(self) -> bool:
        """True while Garmin answers."""
        return self._busy

    # --- Comportamento ------------------------------------------------------------------------

    def _update_buttons(self) -> None:
        ready = bool(self._email.text().strip()) and bool(self._password.text()) and not self._busy
        self._connect.setEnabled(ready)
        self._connect.setToolTip(
            "" if ready or self._busy else self.tr("Enter your email and password.")
        )

    def _submit(self) -> None:
        if not self._connect.isEnabled():
            return
        credentials = Credentials(self._email.text().strip(), self._password.text())
        self._password.clear()  # the password does not stay in the dialog
        self.set_busy(True)
        self.submitted.emit(credentials)

    def set_busy(self, busy: bool) -> None:
        """Busy: the fields and "Connect" are disabled, progress appears."""
        self._busy = busy
        for widget in (self._email, self._password):
            widget.setEnabled(not busy)
        self._progress.setVisible(busy)
        self._progress_label.setVisible(busy)
        if busy:
            self._message.setVisible(False)
        self._update_buttons()

    def show_failure(self, text: str) -> None:
        """An error next to the fields; one can retry right away."""
        self.set_busy(False)
        self._message.setText(text)
        self._message.setVisible(True)
        self._password.setFocus()

    def reject(self) -> None:
        """Close; if the login is in progress, ask to cancel it."""
        self._password.clear()
        if self._busy:
            self.cancel_requested.emit()
        super().reject()


class MfaCodeDialog(QDialog):
    """The verification code Garmin sent; "Verify" or Cancel answer the login."""

    answered = Signal(object)  # str | None

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._done = False
        self.setWindowTitle(self.tr("Garmin verification code"))
        self.setMinimumWidth(DIALOG_MIN_WIDTH)
        self.setWindowModality(Qt.WindowModality.WindowModal)
        intro = QLabel(
            self.tr("Garmin sent you a verification code. Enter it to finish connecting."), self
        )
        intro.setWordWrap(True)
        intro.setTextFormat(Qt.TextFormat.PlainText)
        self._code = QLineEdit(self)
        self._code.setObjectName("garminMfaCode")
        self._code.setInputMethodHints(_SECRET_HINTS)
        self._code.textChanged.connect(self._update_buttons)
        self._code.returnPressed.connect(self.accept)
        form = QFormLayout()
        form.addRow(self.tr("Verification code"), self._code)
        buttons = QDialogButtonBox(self)
        self._verify = buttons.addButton(self.tr("Verify"), QDialogButtonBox.ButtonRole.AcceptRole)
        self._verify.setDefault(True)
        self._verify.clicked.connect(self.accept)
        buttons.addButton(QDialogButtonBox.StandardButton.Cancel).clicked.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.setSpacing(SPACE_4)
        layout.addWidget(intro)
        layout.addLayout(form)
        layout.addWidget(buttons)
        self._update_buttons()

    @property
    def code_edit(self) -> QLineEdit:
        """The code field."""
        return self._code

    @property
    def verify_button(self) -> QPushButton:
        """The "Verify" button."""
        return self._verify

    def _update_buttons(self) -> None:
        ready = bool(self._code.text().strip())
        self._verify.setEnabled(ready)
        self._verify.setToolTip("" if ready else self.tr("Enter the code from Garmin."))

    def _answer(self, code: str | None) -> None:
        if not self._done:
            self._done = True
            self._code.clear()  # the code does not stay in the dialog
            self.answered.emit(code)

    def accept(self) -> None:
        """Deliver the code (if there is one) and close."""
        code = self._code.text().strip()
        if not code:
            return
        self._answer(code)
        super().accept()

    def reject(self) -> None:
        """Cancel: the login ends without saving anything."""
        self._answer(None)
        super().reject()
