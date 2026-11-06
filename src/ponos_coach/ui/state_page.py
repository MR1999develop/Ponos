# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Pagina di stato: titolo, una riga di spiegazione e il bottone del primo passo."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QLabel, QPushButton, QVBoxLayout, QWidget

from ponos_coach.ui.tokens import SPACE_2, SPACE_4, SPACE_6, STATE_PAGE_MAX_WIDTH, TITLE_SCALE


class StatePage(QWidget):
    """Pagina di stato: titolo, una riga di spiegazione e il bottone del primo passo."""

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
        outer = QVBoxLayout(self)
        outer.addStretch(1)
        outer.addWidget(column, alignment=Qt.AlignmentFlag.AlignHCenter)
        outer.addStretch(2)

    def set_content(self, title: str, body: str, button_text: str) -> None:
        """Imposta i testi (sempre testo semplice, mai HTML)."""
        self._title.setText(title)
        self._body.setText(body)
        self._button.setText(button_text)

    @property
    def button(self) -> QPushButton:
        """Il bottone del primo passo."""
        return self._button

    @property
    def title_text(self) -> str:
        """Il titolo mostrato."""
        return self._title.text()

    @property
    def body_text(self) -> str:
        """La spiegazione mostrata."""
        return self._body.text()
