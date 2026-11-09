# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""The "Threshold test" section of Settings > Training plan: the protocol the test dialog starts
with.

The choice applies at once and is remembered between sessions (`ProtocolPreference`); the dialog of
a test still lets the user pick the other protocol for that test, and remembers that choice too.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QButtonGroup, QGroupBox, QLabel, QRadioButton, QVBoxLayout, QWidget

from ponos_coach.assessment.domain import ThresholdProtocol
from ponos_coach.ui.threshold_preferences import ProtocolPreference
from ponos_coach.ui.threshold_presentation import protocol_choice_text
from ponos_coach.ui.tokens import SPACE_2


class ThresholdProtocolSection(QGroupBox):
    """One radio button per protocol, checked on the one remembered."""

    def __init__(self, preference: ProtocolPreference, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setTitle(self.tr("Threshold test"))
        self.setObjectName("thresholdProtocolSection")
        self._preference = preference
        self._buttons: dict[ThresholdProtocol, QRadioButton] = {}
        intro = QLabel(
            self.tr("The test you start from an activity begins with this protocol."), self
        )
        intro.setWordWrap(True)
        intro.setTextFormat(Qt.TextFormat.PlainText)
        column = QVBoxLayout(self)
        column.setSpacing(SPACE_2)
        column.addWidget(intro)
        group = QButtonGroup(self)
        for protocol in ThresholdProtocol:
            button = QRadioButton(protocol_choice_text(protocol), self)
            button.setObjectName(f"settingsProtocol-{protocol.value}")
            button.toggled.connect(lambda on, p=protocol: self._on_toggled(p, on))
            group.addButton(button)
            column.addWidget(button)
            self._buttons[protocol] = button
        self.refresh()

    def button(self, protocol: ThresholdProtocol) -> QRadioButton:
        """The radio button of a protocol."""
        return self._buttons[protocol]

    def refresh(self) -> None:
        """Check the protocol remembered now (the test dialog may have changed it)."""
        chosen = self._preference.load()
        for protocol, button in self._buttons.items():
            button.blockSignals(True)
            button.setChecked(protocol is chosen)
            button.blockSignals(False)

    def _on_toggled(self, protocol: ThresholdProtocol, on: bool) -> None:
        if on:
            self._preference.save(protocol)
