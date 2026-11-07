# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Page "Calendar": a month grid with the past days' activities, load and nutrition goal.

Humble view on `calendar_model`: the grid is a read-only table model (one row per week, Monday
first), navigation is by buttons and keys (Page Up and Page Down change month; arrows move between
days; Enter or double click opens a past day). Days after today cannot be selected. The goal period
of a day is a faint colour, always with its name in the cell and in the legend.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

from PySide6.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    QObject,
    QPersistentModelIndex,
    Qt,
    Signal,
)
from PySide6.QtGui import QBrush, QColor, QFont, QKeyEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from ponos_coach.load.use_cases import ProfileActivity
from ponos_coach.nutrition.domain import GoalPeriod
from ponos_coach.ui.calendar_model import (
    DAYS_PER_WEEK,
    CalendarDay,
    GoalKind,
    MonthView,
    build_month,
    first_of_month,
    shift_month,
)
from ponos_coach.ui.calendar_texts import (
    cell_description,
    cell_text,
    goal_kind_text,
    month_title,
    weekday_names,
)
from ponos_coach.ui.tokens import SPACE_2, SPACE_3, SPACE_4, Theme, goal_tints_for

_ALIGN_TOP_LEFT = int(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
_LEGEND_SWATCH = 16
_MIN_CELL_HEIGHT = 64


class MonthModel(QAbstractTableModel):
    """The cells of a month: texts, descriptions and goal backgrounds.

    Only the past days of the month are enabled."""

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._month: MonthView | None = None
        self._tints: tuple[QColor, ...] = ()

    def set_month(self, month: MonthView, theme: Theme) -> None:
        """Replace the month shown; `theme` chooses the goal tints."""
        self.beginResetModel()
        self._month = month
        self._tints = tuple(QColor(tint) for tint in goal_tints_for(theme))
        self.endResetModel()

    def day_at(self, row: int, column: int) -> CalendarDay | None:
        """The cell at a position, or `None` if there is none."""
        if self._month is None or not 0 <= row < len(self._month.weeks):
            return None
        week = self._month.weeks[row]
        return week[column] if 0 <= column < len(week) else None

    def index_of(self, day: date) -> QModelIndex:
        """The index of a day of the month, or an invalid one."""
        position = None if self._month is None else self._month.cell_of(day)
        return QModelIndex() if position is None else self.index(*position)

    def rowCount(  # noqa: N802 - Qt
        self, parent: QModelIndex | QPersistentModelIndex | None = None
    ) -> int:
        """Number of weeks."""
        if parent is not None and parent.isValid():
            return 0
        return 0 if self._month is None else len(self._month.weeks)

    def columnCount(  # noqa: N802 - Qt
        self, parent: QModelIndex | QPersistentModelIndex | None = None
    ) -> int:
        """Seven days."""
        if parent is not None and parent.isValid():
            return 0
        return DAYS_PER_WEEK

    def headerData(  # noqa: N802 - Qt
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> object:
        """The weekday names."""
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return weekday_names()[section] if 0 <= section < DAYS_PER_WEEK else None
        return None

    def flags(self, index: QModelIndex | QPersistentModelIndex) -> Qt.ItemFlag:
        """Only the past days of the month can be selected and opened."""
        cell = self.day_at(index.row(), index.column()) if index.isValid() else None
        if cell is None or not cell.can_open:
            return Qt.ItemFlag.NoItemFlags
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable

    def data(
        self,
        index: QModelIndex | QPersistentModelIndex,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> object:
        """Text, description, background and font of a cell."""
        cell = self.day_at(index.row(), index.column()) if index.isValid() else None
        if cell is None:
            return None
        if role == Qt.ItemDataRole.DisplayRole:
            return cell_text(cell)
        if role in (Qt.ItemDataRole.ToolTipRole, Qt.ItemDataRole.AccessibleDescriptionRole):
            return cell_description(cell)
        return self._style(cell, role)

    def _style(self, cell: CalendarDay, role: int) -> object:
        """Alignment, goal tint and bold today."""
        if role == Qt.ItemDataRole.TextAlignmentRole:
            return _ALIGN_TOP_LEFT
        if role == Qt.ItemDataRole.BackgroundRole and cell.goal is not None:
            return QBrush(self._tints[list(GoalKind).index(cell.goal)])
        if role == Qt.ItemDataRole.FontRole and cell.is_today:
            font = QFont()
            font.setBold(True)
            return font
        return None


class MonthTable(QTableView):
    """The grid; Page Up and Page Down ask for the previous and the next month."""

    month_step_requested = Signal(int)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802 - Qt
        """Page Up and Page Down change month; every other key is the table's."""
        if event.key() == Qt.Key.Key_PageUp:
            self.month_step_requested.emit(-1)
        elif event.key() == Qt.Key.Key_PageDown:
            self.month_step_requested.emit(1)
        else:
            super().keyPressEvent(event)


class CalendarPage(QWidget):
    """Month navigation, the grid and the legend of the goal colours."""

    day_requested = Signal(object)  # date: Enter or double click on a past day
    month_changed = Signal(object)  # date: the first day of the month now shown

    def __init__(self, today: date, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._today = today
        self._month_first = first_of_month(today)
        self._activities: tuple[ProfileActivity, ...] = ()
        self._periods: tuple[GoalPeriod, ...] = ()
        self._theme = Theme.LIGHT
        self._model = MonthModel(self)
        self._build()
        self._refresh()

    # --- Construction -------------------------------------------------------------------

    def _build(self) -> None:
        top = self._build_navigation()
        self._build_table()
        legend_row = self._build_legend()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACE_4, SPACE_4, SPACE_4, SPACE_4)
        layout.setSpacing(SPACE_3)
        layout.addLayout(top)
        layout.addWidget(self._table, stretch=1)
        layout.addWidget(legend_row)

    def _build_navigation(self) -> QHBoxLayout:
        self._previous = QPushButton(self.tr("Previous month"), self)
        self._previous.setObjectName("calendarPrevious")
        self._previous.clicked.connect(self.previous_month)
        self._next = QPushButton(self.tr("Next month"), self)
        self._next.setObjectName("calendarNext")
        self._next.clicked.connect(self.next_month)
        self._current = QPushButton(self.tr("Today"), self)
        self._current.setObjectName("calendarToday")
        self._current.clicked.connect(self.go_today)
        self._title = QLabel(self)
        self._title.setObjectName("calendarTitle")
        self._title.setTextFormat(Qt.TextFormat.PlainText)
        font = self._title.font()
        font.setBold(True)
        self._title.setFont(font)
        self._title.setMinimumWidth(self._title.fontMetrics().horizontalAdvance("September 2026"))
        self._title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        top = QHBoxLayout()
        for widget in (self._previous, self._title, self._next):
            top.addWidget(widget)
        top.addSpacing(SPACE_4)
        top.addWidget(self._current)
        top.addStretch(1)
        return top

    def _build_table(self) -> None:
        self._table = MonthTable(self)
        self._table.setObjectName("calendarTable")
        self._table.setAccessibleName(self.tr("Calendar"))
        self._table.setModel(self._model)
        self._table.setShowGrid(False)
        self._table.setWordWrap(True)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.verticalHeader().setVisible(False)
        self._table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._table.verticalHeader().setMinimumSectionSize(_MIN_CELL_HEIGHT)
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._table.horizontalHeader().setHighlightSections(False)
        self._table.activated.connect(self._on_activated)
        self._table.month_step_requested.connect(self._step)

    def _build_legend(self) -> QWidget:
        self._legend = QHBoxLayout()
        self._legend.setSpacing(SPACE_2)
        legend_row = QWidget(self)
        legend_row.setLayout(self._legend)
        self._legend_swatches: dict[GoalKind, QFrame] = {}
        self._legend.addWidget(QLabel(self.tr("Nutrition goal:"), self))
        for kind in GoalKind:
            swatch = QFrame(self)
            swatch.setFixedSize(_LEGEND_SWATCH, _LEGEND_SWATCH)
            swatch.setFrameShape(QFrame.Shape.Box)
            swatch.setAccessibleName(goal_kind_text(kind))
            self._legend_swatches[kind] = swatch
            self._legend.addWidget(swatch)
            self._legend.addWidget(QLabel(goal_kind_text(kind), self))
        self._legend.addSpacing(SPACE_4)
        self._legend.addWidget(
            QLabel(self.tr("No colour: no goal period, maintenance assumed."), self)
        )
        self._legend.addStretch(1)

        return legend_row

    # --- Public commands and state ------------------------------------------------------

    @property
    def table(self) -> QTableView:
        """The month grid."""
        return self._table

    @property
    def model(self) -> MonthModel:
        """The grid's model."""
        return self._model

    @property
    def current_month(self) -> date:
        """The first day of the month shown."""
        return self._month_first

    @property
    def title_text(self) -> str:
        """The month and year shown in the title."""
        return self._title.text()

    @property
    def previous_button(self) -> QPushButton:
        """The "Previous month" button."""
        return self._previous

    @property
    def next_button(self) -> QPushButton:
        """The "Next month" button."""
        return self._next

    @property
    def today_button(self) -> QPushButton:
        """The "Today" button."""
        return self._current

    def show_data(
        self,
        today: date,
        activities: Sequence[ProfileActivity],
        theme: Theme,
    ) -> None:
        """Show the profile's activities. The month stays unless `today` moved before it."""
        if today != self._today and self._month_first > first_of_month(today):
            self._month_first = first_of_month(today)
        self._today = today
        self._activities = tuple(activities)
        self._theme = theme
        self._refresh()

    def set_periods(self, periods: Sequence[GoalPeriod]) -> None:
        """Show the goal periods (after the settings changed)."""
        self._periods = tuple(periods)
        self._refresh()

    def apply_theme(self, theme: Theme) -> None:
        """Change the tints to the theme's."""
        self._theme = theme
        self._refresh()

    def previous_month(self) -> None:
        """Show the month before."""
        self._step(-1)

    def next_month(self) -> None:
        """Show the month after (never later than the month of today)."""
        self._step(1)

    def go_today(self) -> None:
        """Show the month of today and select today."""
        self._set_month(first_of_month(self._today))
        self.select_day(self._today)

    def select_day(self, day: date) -> None:
        """Select a day of the shown month if it can be opened."""
        index = self._model.index_of(day)
        if index.isValid() and self._model.flags(index) & Qt.ItemFlag.ItemIsEnabled:
            self._table.setCurrentIndex(index)

    def selected_day(self) -> date | None:
        """The day of the selected cell, if any."""
        cell = self._cell_at(self._table.currentIndex())
        return None if cell is None else cell.day

    def request_selected_day(self) -> None:
        """Open the selected day (no effect without a selection)."""
        day = self.selected_day()
        if day is not None:
            self.day_requested.emit(day)

    # --- Internals -----------------------------------------------------------------------

    def _step(self, months: int) -> None:
        target = shift_month(self._month_first, months)
        if target <= first_of_month(self._today):
            self._set_month(target)

    def _set_month(self, first_day: date) -> None:
        if first_day == self._month_first:
            return
        self._month_first = first_day
        self._refresh()
        self.month_changed.emit(first_day)

    def _cell_at(self, index: QModelIndex) -> CalendarDay | None:
        if not index.isValid() or not self._model.flags(index) & Qt.ItemFlag.ItemIsEnabled:
            return None
        return self._model.day_at(index.row(), index.column())

    def _on_activated(self, index: QModelIndex) -> None:
        cell = self._cell_at(index)
        if cell is not None:
            self.day_requested.emit(cell.day)

    def _refresh(self) -> None:
        month = build_month(self._month_first, self._today, self._activities, self._periods)
        self._model.set_month(month, self._theme)
        self._title.setText(month_title(self._month_first))
        at_present = self._month_first >= first_of_month(self._today)
        self._next.setEnabled(not at_present)
        self._next.setToolTip(
            self.tr("The calendar shows the past only, for now.") if at_present else ""
        )
        tints = goal_tints_for(self._theme)
        for kind, swatch in self._legend_swatches.items():
            swatch.setStyleSheet(f"background-color: {tints[list(GoalKind).index(kind)]};")
