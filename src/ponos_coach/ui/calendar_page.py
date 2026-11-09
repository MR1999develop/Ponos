# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Page "Calendar": a month grid with what was done, what is planned, goals, and the day in full.

Humble view on `calendar_model`: the grid is a read-only table model (one row per week, Monday
first), navigation is by buttons and keys (Page Up and Page Down change month; arrows move between
days; Enter or double click opens a day up to today). Every day of the month can be selected, also
the days ahead, up to a year from today: the panel under the grid shows the plan of the selected
day. The nutrition goal period of a day is a faint colour and the training phase a stripe on top of
the cell; both always have their name in the cell and in the legend (never the colour alone).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

from PySide6.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    QObject,
    QPersistentModelIndex,
    QPoint,
    Qt,
    Signal,
)
from PySide6.QtGui import QBrush, QColor, QContextMenuEvent, QFont, QKeyEvent, QPainter
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QSplitter,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from ponos_coach.load.use_cases import ProfileActivity
from ponos_coach.nutrition.domain import GoalPeriod
from ponos_coach.planning.domain import Phase
from ponos_coach.ui.calendar_day_panel import DayDetailPanel
from ponos_coach.ui.calendar_model import (
    DAYS_PER_WEEK,
    CalendarDay,
    GoalKind,
    MonthView,
    PlanLayer,
    build_month,
    first_of_month,
    grid_range,
    last_month_shown,
    shift_month,
)
from ponos_coach.ui.calendar_texts import (
    cell_description,
    cell_text,
    goal_kind_text,
    month_title,
    weekday_names,
)
from ponos_coach.ui.injury_presentation import ProblemSpan
from ponos_coach.ui.plan_texts import phase_text
from ponos_coach.ui.tokens import SPACE_2, SPACE_3, SPACE_4, Theme, goal_tints_for, tokens_for

_ALIGN_TOP_LEFT = int(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
_LEGEND_SWATCH = 16
_MIN_CELL_HEIGHT = 64
_STRIPE_HEIGHT = 4
STRIPE_ROLE = Qt.ItemDataRole.UserRole  # the colour of the phase stripe of a cell, or None

# The colour slot of each phase in the theme's categories (never red or orange). Taper and mini
# taper share a slot: their names tell them apart.
_PHASE_SLOT = {
    Phase.MAINTENANCE: 6,
    Phase.BASE: 0,
    Phase.BUILD: 2,
    Phase.PEAK: 3,
    Phase.RECOVERY: 4,
    Phase.MINI_TAPER: 1,
    Phase.TAPER: 1,
    Phase.RACE: 5,
}


def phase_color(phase: Phase, theme: Theme) -> str:
    """The colour of a phase's stripe and legend swatch in a theme."""
    return tokens_for(theme).categories[_PHASE_SLOT[phase]]


class MonthModel(QAbstractTableModel):
    """The cells of a month: texts, descriptions, goal backgrounds and phase stripes.

    Only the days of the month are enabled."""

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._month: MonthView | None = None
        self._tints: tuple[QColor, ...] = ()
        self._theme = Theme.LIGHT

    def set_month(self, month: MonthView, theme: Theme) -> None:
        """Replace the month shown; `theme` chooses the goal tints and the phase stripes."""
        self.beginResetModel()
        self._month = month
        self._theme = theme
        self._tints = tuple(QColor(tint) for tint in goal_tints_for(theme))
        self.endResetModel()

    @property
    def month(self) -> MonthView | None:
        """The month shown."""
        return self._month

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
        """The days of the month can be selected (those after today too: they hold the plan)."""
        cell = self.day_at(index.row(), index.column()) if index.isValid() else None
        if cell is None or not cell.can_select:
            return Qt.ItemFlag.NoItemFlags
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable

    def data(
        self,
        index: QModelIndex | QPersistentModelIndex,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> object:
        """Text, description, background, stripe and font of a cell."""
        cell = self.day_at(index.row(), index.column()) if index.isValid() else None
        if cell is None:
            return None
        if role == Qt.ItemDataRole.DisplayRole:
            return cell_text(cell)
        if role in (Qt.ItemDataRole.ToolTipRole, Qt.ItemDataRole.AccessibleDescriptionRole):
            return cell_description(cell)
        if role == STRIPE_ROLE:
            return None if cell.plan is None else phase_color(cell.plan.phase, self._theme)
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


class StripeDelegate(QStyledItemDelegate):
    """Draws the cell as usual and, over it, a thin stripe in the colour of the day's phase."""

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        index: QModelIndex | QPersistentModelIndex,
    ) -> None:
        """The cell, then the stripe along its top edge."""
        super().paint(painter, option, index)
        color = index.data(STRIPE_ROLE)
        if isinstance(color, str):
            painter.save()
            painter.fillRect(
                option.rect.x(), option.rect.y(), option.rect.width(), _STRIPE_HEIGHT, QColor(color)
            )
            painter.restore()


class MonthTable(QTableView):
    """The grid; Page Up and Page Down ask for the previous and the next month."""

    month_step_requested = Signal(int)
    context_menu_requested = Signal(object, bool)  # QPoint (in the viewport), from the keyboard

    def contextMenuEvent(self, event: QContextMenuEvent) -> None:  # noqa: N802 - Qt
        """The right button or the Menu key (Shift+F10): ask for the menu of the day."""
        from_keyboard = event.reason() == QContextMenuEvent.Reason.Keyboard
        self.context_menu_requested.emit(event.pos(), from_keyboard)
        event.accept()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802 - Qt
        """Page Up and Page Down change month; every other key is the table's."""
        if event.key() == Qt.Key.Key_PageUp:
            self.month_step_requested.emit(-1)
        elif event.key() == Qt.Key.Key_PageDown:
            self.month_step_requested.emit(1)
        else:
            super().keyPressEvent(event)


class CalendarPage(QWidget):
    """Month navigation, the grid, the legends and the panel of the selected day."""

    day_requested = Signal(object)  # date: Enter or double click on a day up to today
    month_changed = Signal(object)  # date: the first day of the month now shown
    selection_changed = Signal(object)  # date | None: the selected day
    add_race_requested = Signal(object)  # date: from the panel of a day
    add_form_goal_requested = Signal(object)  # date
    change_goal_requested = Signal(str)  # goal id
    remove_goal_requested = Signal(str)  # goal id
    day_menu_requested = Signal(object, object)  # CalendarDay, QPoint (global): a day's menu

    def __init__(self, today: date, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._today = today
        self._month_first = first_of_month(today)
        self._activities: tuple[ProfileActivity, ...] = ()
        self._periods: tuple[GoalPeriod, ...] = ()
        self._layer = PlanLayer()
        self._problems: tuple[ProblemSpan, ...] = ()
        self._theme = Theme.LIGHT
        self._refreshing = False
        self._model = MonthModel(self)
        self._build()
        self._refresh()

    # --- Construction -------------------------------------------------------------------

    def _build(self) -> None:
        top = self._build_navigation()
        self._build_table()
        legends = self._build_legends()
        self._panel = DayDetailPanel(self)
        self._panel.setObjectName("calendarDayPanel")
        self._panel.add_race_requested.connect(self.add_race_requested)
        self._panel.add_form_goal_requested.connect(self.add_form_goal_requested)
        self._panel.change_goal_requested.connect(self.change_goal_requested)
        self._panel.remove_goal_requested.connect(self.remove_goal_requested)
        splitter = QSplitter(Qt.Orientation.Vertical, self)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self._table)
        splitter.addWidget(self._panel)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACE_4, SPACE_4, SPACE_4, SPACE_4)
        layout.setSpacing(SPACE_3)
        layout.addLayout(top)
        layout.addWidget(splitter, stretch=1)
        layout.addWidget(legends)

    def _build_navigation(self) -> QHBoxLayout:
        self._previous = QPushButton(self.tr("Previous month"), self)
        self._previous.setObjectName("calendarPrevious")
        self._previous.clicked.connect(self.previous_month)
        self._next = QPushButton(self.tr("Next month"), self)
        self._next.setObjectName("calendarNext")
        self._next.clicked.connect(self.next_month)
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
        top.addStretch(1)
        return top

    def _build_table(self) -> None:
        self._table = MonthTable(self)
        self._table.setObjectName("calendarTable")
        self._table.setAccessibleName(self.tr("Calendar"))
        self._table.setModel(self._model)
        self._table.setItemDelegate(StripeDelegate(self._table))
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
        self._table.context_menu_requested.connect(self._on_context_menu)
        self._table.selectionModel().currentChanged.connect(self._on_current_changed)

    def _build_legends(self) -> QWidget:
        legends = QWidget(self)
        column = QVBoxLayout(legends)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(SPACE_2)
        column.addWidget(self._build_goal_legend())
        column.addWidget(self._build_phase_legend())
        return legends

    def _build_goal_legend(self) -> QWidget:
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

    def _build_phase_legend(self) -> QWidget:
        """The training phases of the month shown, each with its stripe colour and its name."""
        self._phase_legend = QHBoxLayout()
        self._phase_legend.setSpacing(SPACE_2)
        self._phase_legend_row = QWidget(self)
        self._phase_legend_row.setObjectName("calendarPhaseLegend")
        self._phase_legend_row.setLayout(self._phase_legend)
        self._phase_legend_row.hide()
        return self._phase_legend_row

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
    def panel(self) -> DayDetailPanel:
        """The panel of the selected day."""
        return self._panel

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
    def phase_legend_texts(self) -> tuple[str, ...]:
        """The names in the phase legend (the phases of the month shown)."""
        return tuple(
            label.text()
            for label in self._phase_legend_row.findChildren(QLabel)
            if label.objectName() == "phaseLegendName"
        )

    @property
    def visible_range(self) -> tuple[date, date]:
        """The first and the last day of the grid shown."""
        return grid_range(self._month_first)

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

    def set_plan_layer(self, layer: PlanLayer) -> None:
        """Show the plan: planned days, how the past ones went, the goals."""
        self._layer = layer
        self._refresh()

    def set_problems(self, problems: Sequence[ProblemSpan]) -> None:
        """Show the days with an injury or an illness (in words, in the cell and the panel)."""
        self._problems = tuple(problems)
        self._refresh()

    def apply_theme(self, theme: Theme) -> None:
        """Change the tints and the stripes to the theme's."""
        self._theme = theme
        self._refresh()

    def previous_month(self) -> None:
        """Show the month before."""
        self._step(-1)

    def next_month(self) -> None:
        """Show the month after (never later than a year from today)."""
        self._step(1)

    def go_today(self) -> None:
        """Show the month of today and select today."""
        self._set_month(first_of_month(self._today))
        self.select_day(self._today)

    def select_day(self, day: date) -> None:
        """Select a day of the shown month."""
        index = self._model.index_of(day)
        if index.isValid() and self._model.flags(index) & Qt.ItemFlag.ItemIsEnabled:
            self._table.setCurrentIndex(index)

    def go_to(self, day: date) -> None:
        """Show the month of a day (if the calendar reaches it) and select the day."""
        target = first_of_month(day)
        if target <= last_month_shown(self._today):
            self._set_month(target)
            self.select_day(day)

    def selected_day(self) -> date | None:
        """The day of the selected cell, if any."""
        cell = self._cell_at(self._table.currentIndex())
        return None if cell is None else cell.day

    def selected_cell(self) -> CalendarDay | None:
        """The selected cell, if any."""
        return self._cell_at(self._table.currentIndex())

    def request_selected_day(self) -> None:
        """Open the selected day, if it is up to today (no effect otherwise)."""
        cell = self.selected_cell()
        if cell is not None and cell.can_open:
            self.day_requested.emit(cell.day)

    # --- Internals -----------------------------------------------------------------------

    def _step(self, months: int) -> None:
        target = shift_month(self._month_first, months)
        if target <= last_month_shown(self._today):
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
        if cell is not None and cell.can_open:
            self.day_requested.emit(cell.day)

    def _on_context_menu(self, position: object, from_keyboard: bool) -> None:
        """Select the day under the pointer (or keep the selected one, for the keyboard) and say
        where its menu goes."""
        if not isinstance(position, QPoint):
            return
        viewport = self._table.viewport()
        if from_keyboard:
            current = self._table.currentIndex()
            where = viewport.mapToGlobal(self._table.visualRect(current).center())
        else:
            current = self._table.indexAt(position)
            if self._cell_at(current) is not None:
                self._table.setCurrentIndex(current)
            where = viewport.mapToGlobal(position)
        cell = self._cell_at(current)
        if cell is not None:
            self.day_menu_requested.emit(cell, where)

    def _on_current_changed(self, _current: QModelIndex, _previous: QModelIndex) -> None:
        if not self._refreshing:
            self._sync_panel()

    def _sync_panel(self) -> None:
        """Show the selected day in the panel and tell whoever follows the selection."""
        cell = self.selected_cell()
        self._panel.show_day(cell, self._today)
        self.selection_changed.emit(None if cell is None else cell.day)

    def _refresh(self) -> None:
        selected = self.selected_day()
        month = build_month(
            self._month_first,
            self._today,
            self._activities,
            self._periods,
            self._layer,
            self._problems,
        )
        self._refreshing = True  # the model reset drops the current cell: say it once, at the end
        try:
            self._model.set_month(month, self._theme)
            if selected is not None:
                self.select_day(selected)
        finally:
            self._refreshing = False
        self._title.setText(month_title(self._month_first))
        at_limit = self._month_first >= last_month_shown(self._today)
        self._next.setEnabled(not at_limit)
        self._next.setToolTip(self.tr("The calendar shows up to a year ahead.") if at_limit else "")
        tints = goal_tints_for(self._theme)
        for kind, swatch in self._legend_swatches.items():
            swatch.setStyleSheet(f"background-color: {tints[list(GoalKind).index(kind)]};")
        self._refresh_phase_legend(month)
        self._sync_panel()

    def _refresh_phase_legend(self, month: MonthView) -> None:
        present = {cell.plan.phase for cell in month.days if cell.plan is not None}
        while self._phase_legend.count():
            item = self._phase_legend.takeAt(0)
            widget = None if item is None else item.widget()
            if widget is not None:
                widget.hide()
                widget.deleteLater()
        self._phase_legend_row.setVisible(bool(present))
        if not present:
            return
        self._phase_legend.addWidget(QLabel(self.tr("Training phase:"), self._phase_legend_row))
        for phase in Phase:
            if phase not in present:
                continue
            swatch = QFrame(self._phase_legend_row)
            swatch.setFixedSize(_LEGEND_SWATCH, _LEGEND_SWATCH)
            swatch.setFrameShape(QFrame.Shape.Box)
            swatch.setStyleSheet(f"background-color: {phase_color(phase, self._theme)};")
            swatch.setAccessibleName(phase_text(phase))
            name = QLabel(phase_text(phase), self._phase_legend_row)
            name.setObjectName("phaseLegendName")
            self._phase_legend.addWidget(swatch)
            self._phase_legend.addWidget(name)
        self._phase_legend.addStretch(1)
