# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""State page: title, one explanation line and the first-step button."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from ponos_coach.ui.tokens import SPACE_2, SPACE_4, SPACE_6, STATE_PAGE_MAX_WIDTH, TITLE_SCALE


class StatePage(QWidget):
    """State page: title, one explanation line and the first-step button."""

    action_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._title = QLabel(self)
        self._title.setTextFormat(Qt.TextFormat.PlainText)
        self._title.setWordWrap(True)
        font = self._title.font()
        font.setPointSizeF(font.pointSizeF() * TITLE_SCALE)
        font.setWeight(font.Weight.DemiBold)
        self._title.setFont(font)
        self._body = QLabel(self)
        self._body.setTextFormat(Qt.TextFormat.PlainText)
        self._body.setWordWrap(True)
        self._button = QPushButton(self)
        self._button.setDefault(True)
        self._button.clicked.connect(self.action_requested)
        column = QWidget(self)
        column.setMaximumWidth(STATE_PAGE_MAX_WIDTH)
        inner = QVBoxLayout(column)
        inner.setSpacing(SPACE_2)
        inner.setContentsMargins(SPACE_6, SPACE_6, SPACE_6, SPACE_6)
        inner.addWidget(self._title)
        inner.addWidget(self._body)
        inner.addSpacing(SPACE_4)
        inner.addWidget(self._button, alignment=Qt.AlignmentFlag.AlignLeft)
        # Centred with two spacers, not with the layout's alignment: with alignment Qt
        # computes the height from the column's ideal width and wrapped labels stay
        # shorter than their text (clipped lines).
        centered = QHBoxLayout()
        centered.addStretch(1)
        centered.addWidget(column)
        centered.addStretch(1)
        outer = QVBoxLayout(self)
        outer.addStretch(1)
        outer.addLayout(centered)
        outer.addStretch(2)

    def set_content(self, title: str, body: str, button_text: str) -> None:
        """Set the texts (always plain text, never HTML)."""
        self._title.setText(title)
        self._body.setText(body)
        self._button.setText(button_text)

    @property
    def button(self) -> QPushButton:
        """The first-step button."""
        return self._button

    @property
    def title_text(self) -> str:
        """The title shown."""
        return self._title.text()

    @property
    def body_text(self) -> str:
        """The explanation shown."""
        return self._body.text()
