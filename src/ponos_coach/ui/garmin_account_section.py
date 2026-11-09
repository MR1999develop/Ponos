# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""The content of Settings > Garmin account: whether an account is connected, the buttons
to connect and disconnect it and the preference to synchronise when a profile opens.

Humble view: the commands themselves (their availability, the reason when they are off, what they
do) are the `QAction`s of `GarminUi`; this widget shows them and passes the clicks on.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QCheckBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ponos_coach.ui.tokens import SPACE_2


class GarminAccountSection(QGroupBox):
    """Account status and preferences, driven by the actions of the Garmin commands."""

    def __init__(
        self,
        connect: QAction,
        disconnect: QAction,
        auto_sync: QAction,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setFlat(True)  # the page heading already says "Garmin account"
        self.setObjectName("garminAccountSection")
        self._connect = connect
        self._disconnect = disconnect
        self._auto_sync = auto_sync
        self._status = QLabel(self)
        self._status.setObjectName("garminAccountStatus")
        self._status.setTextFormat(Qt.TextFormat.PlainText)
        self._status.setWordWrap(True)
        self._connect_button = self._button(connect, "garminConnect")
        self._disconnect_button = self._button(disconnect, "garminDisconnect")
        self._auto_check = QCheckBox(auto_sync.text(), self)
        self._auto_check.setObjectName("garminAutoSync")
        self._auto_check.clicked.connect(self._on_auto_clicked)
        row = QHBoxLayout()
        row.addWidget(self._connect_button)
        row.addWidget(self._disconnect_button)
        row.addStretch(1)
        column = QVBoxLayout(self)
        column.setSpacing(SPACE_2)
        column.addWidget(self._status)
        column.addLayout(row)
        column.addWidget(self._auto_check)
        self.refresh()

    def _button(self, action: QAction, name: str) -> QPushButton:
        button = QPushButton(action.text(), self)
        button.setObjectName(name)
        button.clicked.connect(action.trigger)
        return button

    def _on_auto_clicked(self, checked: bool) -> None:
        # the action holds the preference: triggering it toggles it and says the new state
        if self._auto_sync.isChecked() != checked:
            self._auto_sync.trigger()

    # --- Test accessors ---------------------------------------------------------------------

    @property
    def connect_button(self) -> QPushButton:
        """The "Connect Garmin account..." button."""
        return self._connect_button

    @property
    def disconnect_button(self) -> QPushButton:
        """The "Disconnect Garmin account" button."""
        return self._disconnect_button

    @property
    def auto_sync_check(self) -> QCheckBox:
        """The "Synchronise when a profile opens" check box."""
        return self._auto_check

    @property
    def status_text(self) -> str:
        """What the section says about the account."""
        return self._status.text()

    # --- Showing ------------------------------------------------------------------------------

    def set_status(self, text: str) -> None:
        """Say what is known about the account."""
        self._status.setText(text)

    def refresh(self) -> None:
        """Show the state of the three commands: enabled or not (with the reason) and checked."""
        for button, action in (
            (self._connect_button, self._connect),
            (self._disconnect_button, self._disconnect),
        ):
            button.setEnabled(action.isEnabled())
            button.setToolTip(action.toolTip())
        self._auto_check.setEnabled(self._auto_sync.isEnabled())
        self._auto_check.setToolTip(self._auto_sync.toolTip())
        self._auto_check.setChecked(self._auto_sync.isChecked())
