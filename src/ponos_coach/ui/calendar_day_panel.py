# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""The panel under the calendar grid: the selected day in full, and the commands on its goals.

Humble view: the text comes ready from `calendar_texts.day_detail_text`. A day after today shows the
planned session with its blocks, heart-rate zones and pace; a past day shows what was planned then
next to what was done. The goals of the day can be changed or removed here; adding a goal and
sending the session to Garmin are commands of the menu of the day. Nothing is asked of the user
before an undoable change.
"""

from __future__ import annotations

from datetime import date

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ponos_coach.ui.calendar_model import CalendarDay
from ponos_coach.ui.calendar_texts import day_detail_text
from ponos_coach.ui.plan_texts import goal_label
from ponos_coach.ui.tokens import SPACE_1, SPACE_2, SPACE_3

_PANEL_MIN_HEIGHT = 72  # about four lines of text; the user can drag it taller


class DayDetailPanel(QWidget):
    """The selected day's text and the buttons of its goals."""

    change_goal_requested = Signal(str)  # goal id
    remove_goal_requested = Signal(str)  # goal id

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(_PANEL_MIN_HEIGHT)
        self._day: date | None = None
        self._detail = QLabel(self)
        self._detail.setObjectName("calendarDayDetail")
        self._detail.setAccessibleName(self.tr("Selected day"))
        self._detail.setTextFormat(Qt.TextFormat.PlainText)
        self._detail.setWordWrap(True)
        self._detail.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self._detail.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByKeyboard)
        scroll = QScrollArea(self)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setWidget(self._detail)
        self._goal_rows = QVBoxLayout()
        self._goal_rows.setSpacing(SPACE_2)
        self._row_widgets: list[QWidget] = []
        column = QVBoxLayout()
        column.setSpacing(SPACE_2)
        column.addLayout(self._goal_rows)
        column.addStretch(1)
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(SPACE_3)
        outer.addWidget(scroll, stretch=1)
        outer.addLayout(column)
        self.show_day(None)

    # --- State for the tests ---------------------------------------------------------------

    @property
    def detail_text(self) -> str:
        """The text shown."""
        return self._detail.text()

    def goal_buttons(self) -> list[tuple[QPushButton, QPushButton]]:
        """For each goal of the day, its "Change..." and "Remove" buttons."""
        pairs = []
        for row in self._row_widgets:
            buttons = row.findChildren(QPushButton)
            pairs.append((buttons[0], buttons[1]))
        return pairs

    # --- Showing a day -----------------------------------------------------------------------

    def show_day(self, cell: CalendarDay | None) -> None:
        """Show a day (`None`: nothing selected, say how to start)."""
        self._clear_rows()
        if cell is None or not cell.in_month:
            self._day = None
            self._detail.setText(
                self.tr("Select a day to see what is planned, what was done and its goals.")
            )
            return
        self._day = cell.day
        self._detail.setText(day_detail_text(cell))
        for item in cell.goals:
            self._add_goal_row(item.goal.goal_id.value, goal_label(item.goal))

    def _add_goal_row(self, goal_id: str, label: str) -> None:
        row = QWidget(self)
        layout = QVBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACE_1)
        name = QLabel(label, row)
        name.setTextFormat(Qt.TextFormat.PlainText)
        name.setWordWrap(True)
        buttons = QHBoxLayout()
        buttons.setSpacing(SPACE_2)
        change = QPushButton(self.tr("Change..."), row)
        change.setToolTip(label)
        change.setAccessibleDescription(label)
        change.clicked.connect(lambda: self.change_goal_requested.emit(goal_id))
        remove = QPushButton(self.tr("Remove"), row)
        remove.setToolTip(label)
        remove.setAccessibleDescription(label)
        remove.clicked.connect(lambda: self.remove_goal_requested.emit(goal_id))
        buttons.addWidget(change)
        buttons.addWidget(remove)
        layout.addWidget(name)
        layout.addLayout(buttons)
        self._goal_rows.addWidget(row)
        self._row_widgets.append(row)

    def _clear_rows(self) -> None:
        for row in self._row_widgets:
            self._goal_rows.removeWidget(row)
            row.hide()
            row.deleteLater()
        self._row_widgets = []
