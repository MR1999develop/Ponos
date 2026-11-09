# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""A window that hosts a page of the application.

The pages that used to be sections of the sidebar (the goals and plan status, the injury history)
are widgets that other parts of the window fill in; they now live in windows of their own, opened
from a menu command, until the settings window gathers them. Closing the window only hides it: the
page keeps what it shows and the next opening is immediate.
"""

from __future__ import annotations

from PySide6.QtWidgets import QDialog, QVBoxLayout, QWidget

_DEFAULT_SIZE = (780, 560)


class PageWindow(QDialog):
    """A non-modal window around one page widget."""

    def __init__(
        self, title: str, page: QWidget, parent: QWidget | None = None, *, name: str = ""
    ) -> None:
        super().__init__(parent)
        self.setModal(False)
        self.setWindowTitle(title)
        if name:
            self.setObjectName(name)
        self.resize(*_DEFAULT_SIZE)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(page)

    def bring_forward(self) -> None:
        """Show the window, in front of the others."""
        self.show()
        self.raise_()
        self.activateWindow()
