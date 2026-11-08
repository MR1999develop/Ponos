# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""The panel under the calendar grid: the selected day in full, and the commands on its goals.

Humble view: the text comes ready from `calendar_texts.day_detail_text`. A day after today shows the
planned session with its blocks, heart-rate zones and pace; a past day shows what was planned then
next to what was done. The goals of the day can be changed or removed here, and a goal can be added
on the day (only for a day after today: a goal needs a day the plan can still reach). Nothing is
asked of the user before an undoable change.
"""

from __future__ import annotations

from datetime import date

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction
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

_PANEL_MIN_HEIGHT = 140


class DayDetailPanel(QWidget):
    """The selected day's text and the buttons of its goals."""

    add_race_requested = Signal(object)  # date
    add_form_goal_requested = Signal(object)  # date
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
        self._add_race = QPushButton(self.tr("Add race here..."), self)
        self._add_race.setObjectName("dayAddRace")
        self._add_race.clicked.connect(self._on_add_race)
        self._add_form = QPushButton(self.tr("Add form goal here..."), self)
        self._add_form.setObjectName("dayAddFormGoal")
        self._add_form.clicked.connect(self._on_add_form)
        self._send_session = QPushButton(self.tr("Send this session to Garmin"), self)
        self._send_session.setObjectName("daySendSession")
        self._send_session.setEnabled(False)
        self._goal_rows = QVBoxLayout()
        self._goal_rows.setSpacing(SPACE_2)
        self._row_widgets: list[QWidget] = []
        column = QVBoxLayout()
        column.setSpacing(SPACE_2)
        column.addWidget(self._add_race)
        column.addWidget(self._add_form)
        column.addWidget(self._send_session)
        column.addLayout(self._goal_rows)
        column.addStretch(1)
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(SPACE_3)
        outer.addWidget(scroll, stretch=1)
        outer.addLayout(column)
        self.show_day(None, date.min)

    # --- State for the tests ---------------------------------------------------------------

    @property
    def detail_text(self) -> str:
        """The text shown."""
        return self._detail.text()

    @property
    def add_race_button(self) -> QPushButton:
        """The "Add race here..." button."""
        return self._add_race

    @property
    def add_form_goal_button(self) -> QPushButton:
        """The "Add form goal here..." button."""
        return self._add_form

    @property
    def send_session_button(self) -> QPushButton:
        """The "Send this session to Garmin" button."""
        return self._send_session

    def attach_send_action(self, action: QAction) -> None:
        """Make the "Send this session to Garmin" button follow a command: it does what the
        action does and is enabled, and explains why not, as the action is."""
        self._send_session.clicked.connect(action.trigger)
        action.changed.connect(lambda: self._follow(action))
        self._follow(action)

    def _follow(self, action: QAction) -> None:
        self._send_session.setEnabled(action.isEnabled())
        self._send_session.setToolTip(action.toolTip())

    def goal_buttons(self) -> list[tuple[QPushButton, QPushButton]]:
        """For each goal of the day, its "Change..." and "Remove" buttons."""
        pairs = []
        for row in self._row_widgets:
            buttons = row.findChildren(QPushButton)
            pairs.append((buttons[0], buttons[1]))
        return pairs

    # --- Showing a day -----------------------------------------------------------------------

    def show_day(self, cell: CalendarDay | None, today: date) -> None:
        """Show a day (`None`: nothing selected, say how to start)."""
        self._clear_rows()
        if cell is None or not cell.in_month:
            self._day = None
            self._detail.setText(
                self.tr("Select a day to see what is planned, what was done and its goals.")
            )
            for button in (self._add_race, self._add_form):
                button.setEnabled(False)
                button.setToolTip(self.tr("Select a day first."))
            return
        self._day = cell.day
        self._detail.setText(day_detail_text(cell))
        can_add = cell.day > today
        reason = "" if can_add else self.tr("A goal needs a day after today.")
        for button in (self._add_race, self._add_form):
            button.setEnabled(can_add)
            button.setToolTip(reason)
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

    def _on_add_race(self) -> None:
        if self._day is not None:
            self.add_race_requested.emit(self._day)

    def _on_add_form(self) -> None:
        if self._day is not None:
            self.add_form_goal_requested.emit(self._day)
