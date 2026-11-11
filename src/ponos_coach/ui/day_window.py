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
        self._page.content_shown.connect(self.fit_content)

    def fit_content(self) -> None:
        """Grow the window to show the whole day without scroll bars (user's request of
        2026-11-10), but never past the screen it is on: there the page scrolls."""
        layout = self._page.layout()
        if layout is not None:
            layout.activate()
        extra = self._page.content_overflow()
        if extra.width() == 0 and extra.height() == 0:
            return
        room = self.screen().availableGeometry()
        frame = self.frameGeometry().size() - self.size()  # title bar and borders
        width = min(self.width() + extra.width(), room.width() - frame.width())
        height = min(self.height() + extra.height(), room.height() - frame.height())
        self.resize(width, height)
        top_left = self.frameGeometry().topLeft()
        if (
            self.frameGeometry().bottom() > room.bottom()
            or self.frameGeometry().right() > room.right()
        ):
            self.move(
                min(top_left.x(), room.right() - self.frameGeometry().width() + 1),
                min(top_left.y(), room.bottom() - self.frameGeometry().height() + 1),
            )

    @property
    def day(self) -> date:
        """The day of the window."""
        return self._day

    @property
    def page(self) -> DayPage:
        """The page of the day."""
        return self._page
