# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""The threshold test protocol the user chose last, remembered between sessions."""

from __future__ import annotations

from typing import Final

from PySide6.QtCore import QSettings

from ponos_coach.assessment.domain import DEFAULT_PROTOCOL, ThresholdProtocol

_KEY: Final = "threshold/protocol"


class ProtocolPreference:
    """Reads and writes the last protocol chosen in the app's settings."""

    def __init__(self, settings: QSettings) -> None:
        self._settings = settings

    def load(self) -> ThresholdProtocol:
        """The last choice; a missing or unknown value gives the default, never an error."""
        raw = self._settings.value(_KEY)
        for protocol in ThresholdProtocol:
            if raw == protocol.value:
                return protocol
        return DEFAULT_PROTOCOL

    def save(self, protocol: ThresholdProtocol) -> None:
        """Remember the choice."""
        self._settings.setValue(_KEY, protocol.value)
