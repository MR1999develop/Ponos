# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Window of one day: a `DayPage` in a window of its own.

Every day opened from the calendar has its own window, not modal, so that several days can be read
side by side. The window only hosts the page and says which day it is for; what the page shows is
decided by `NutritionUi`.
"""

from __future__ import annotations

from datetime import date

from PySide6.QtWidgets import QDialog, QVBoxLayout, QWidget

from ponos_coach.ui.day_page import DayPage
from ponos_coach.ui.day_presentation import day_title

_DEFAULT_SIZE = (780, 680)


class DayWindow(QDialog):
    """A window with the page of one day; closing it ends that day's calculation."""

    def __init__(self, day: date, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._day = day
        self.setModal(False)
        self.setObjectName("dayWindow")
        self.setWindowTitle(day_title(day))
        self.resize(*_DEFAULT_SIZE)
        self._page = DayPage(self)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._page)

    @property
    def day(self) -> date:
        """The day of the window."""
        return self._day

    @property
    def page(self) -> DayPage:
        """The page of the day."""
        return self._page
