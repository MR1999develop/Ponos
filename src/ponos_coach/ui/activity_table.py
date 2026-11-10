# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Activities table: model, per-column sorting and the page with its empty state.

The model gives texts for the view and numeric keys for sorting; numbers sit on the right,
units are in the header, and the cell is empty where the datum is missing (for example the distance
of an indoor session). The filters are the shared ones (`SharedFilterState`): the data thread
filters once and the page only shows the rows whose identity passed (`set_visible_identities`),
so the table and the map always list the same activities.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from enum import IntEnum
from zoneinfo import ZoneInfo

from PySide6.QtCore import (
    QAbstractTableModel,
    QDate,
    QDateTime,
    QLocale,
    QModelIndex,
    QObject,
    QPersistentModelIndex,
    QSortFilterProxyModel,
    Qt,
    QTime,
    Signal,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QHeaderView,
    QLabel,
    QStackedWidget,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from ponos_coach.activities.domain import ActivityIdentity, identity_key
from ponos_coach.load.use_cases import ProfileActivity
from ponos_coach.ui.activity_text import (
    ascent_m_text,
    distance_km_text,
    duration_text,
    load_source_text,
    sport_text,
)
from ponos_coach.ui.state_page import StatePage

SORT_ROLE = Qt.ItemDataRole.UserRole
_MISSING_SORT_KEY = -1.0  # an empty cell comes before every value, in ascending order

type _Key = float | str

# Rows that the column width looks at: the texts all have the same shape, so a
# sample is enough (300 full rows cost about 80 ms on the UI thread).
_WIDTH_SAMPLE_ROWS = 30

_ALIGN_RIGHT = int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
_ALIGN_LEFT = int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)


class Column(IntEnum):
    """Table columns, in display order."""

    START = 0
    SPORT = 1
    MOVING_TIME = 2
    TOTAL_TIME = 3
    DISTANCE = 4
    ASCENT = 5
    LOAD = 6
    AVG_HR = 7
    MAX_HR = 8
    LOAD_SOURCE = 9


_NUMERIC_COLUMNS = frozenset(
    {
        Column.MOVING_TIME,
        Column.TOTAL_TIME,
        Column.DISTANCE,
        Column.ASCENT,
        Column.LOAD,
        Column.AVG_HR,
        Column.MAX_HR,
    }
)


def _local_qdatetime(activity: ProfileActivity, zone: ZoneInfo) -> QDateTime:
    local = activity.start.astimezone(zone)
    return QDateTime(
        QDate(local.year, local.month, local.day),
        QTime(local.hour, local.minute, local.second),
    )


class ActivityTableModel(QAbstractTableModel):
    """A profile's activities, one row each, read-only."""

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._activities: tuple[ProfileActivity, ...] = ()
        self._rows: dict[str, int] = {}
        self._zone = ZoneInfo("UTC")

    def row_of(self, identity: ActivityIdentity) -> int | None:
        """The model row of an activity (not of the proxy), if the table has it."""
        return self._rows.get(identity_key(identity))

    def set_activities(self, activities: Sequence[ProfileActivity], zone: ZoneInfo) -> None:
        """Replace the rows; `zone` is the profile's time zone, for local date and time."""
        self.beginResetModel()
        self._activities = tuple(activities)
        self._rows = {identity_key(a.identity): i for i, a in enumerate(self._activities)}
        self._zone = zone
        self.endResetModel()

    def rowCount(  # noqa: N802 - Qt
        self, parent: QModelIndex | QPersistentModelIndex | None = None
    ) -> int:
        """Number of activities (no child rows)."""
        if parent is not None and parent.isValid():
            return 0
        return len(self._activities)

    def columnCount(  # noqa: N802 - Qt
        self, parent: QModelIndex | QPersistentModelIndex | None = None
    ) -> int:
        """Number of columns."""
        if parent is not None and parent.isValid():
            return 0
        return len(Column)

    def headerData(  # noqa: N802 - Qt
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> object:
        """Headers, with the unit of measure where needed."""
        if orientation != Qt.Orientation.Horizontal:
            return None
        if role == Qt.ItemDataRole.DisplayRole and 0 <= section < len(Column):
            return self._header_text(Column(section))
        if role == Qt.ItemDataRole.TextAlignmentRole and 0 <= section < len(Column):
            return _ALIGN_RIGHT if Column(section) in _NUMERIC_COLUMNS else _ALIGN_LEFT
        return None

    def data(
        self,
        index: QModelIndex | QPersistentModelIndex,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> object:
        """Text, alignment and sort key of a cell."""
        if not index.isValid() or not 0 <= index.row() < len(self._activities):
            return None
        column = Column(index.column())
        activity = self._activities[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            return self._text(activity, column)
        if role == Qt.ItemDataRole.TextAlignmentRole:
            return _ALIGN_RIGHT if column in _NUMERIC_COLUMNS else _ALIGN_LEFT
        if role == SORT_ROLE:
            return self._sort_key(activity, column)
        return None

    def _header_text(self, column: Column) -> str:
        headers = {
            Column.START: self.tr("Date and time"),
            Column.SPORT: self.tr("Sport"),
            Column.MOVING_TIME: self.tr("Moving time (h:mm:ss)"),
            Column.TOTAL_TIME: self.tr("Total time (h:mm:ss)"),
            Column.DISTANCE: self.tr("Distance (km)"),
            Column.ASCENT: self.tr("Ascent (m)"),
            Column.LOAD: self.tr("Load (TRIMPeq)"),
            Column.AVG_HR: self.tr("Avg HR (bpm)"),
            Column.MAX_HR: self.tr("Max HR (bpm)"),
            Column.LOAD_SOURCE: self.tr("Load from"),
        }
        return headers[column]

    def activity_at(self, row: int) -> ProfileActivity | None:
        """The activity of a model row (not of the proxy), or `None` if out of range."""
        return self._activities[row] if 0 <= row < len(self._activities) else None

    def _text(self, activity: ProfileActivity, column: Column) -> str:
        return _TEXT_OF[column](activity, self._zone)

    def _sort_key(self, activity: ProfileActivity, column: Column) -> _Key:
        return _SORT_KEY_OF[column](activity)


def _start_text(activity: ProfileActivity, zone: ZoneInfo) -> str:
    return QLocale().toString(_local_qdatetime(activity, zone), QLocale.FormatType.ShortFormat)


def _missing_as_key(value: float | None) -> float:
    return _MISSING_SORT_KEY if value is None else value


def _bpm_text(bpm: int | None) -> str:
    """Whole beats per minute in the locale's format; empty if the activity has no HR."""
    return "" if bpm is None else QLocale().toString(bpm)


# One function per column: displayed text and sort key.
_TEXT_OF: dict[Column, Callable[[ProfileActivity, ZoneInfo], str]] = {
    Column.START: _start_text,
    Column.SPORT: lambda a, _z: sport_text(a.sport),
    Column.MOVING_TIME: lambda a, _z: duration_text(a.timer_s),
    Column.TOTAL_TIME: lambda a, _z: duration_text(a.elapsed_s),
    Column.DISTANCE: lambda a, _z: distance_km_text(a.summary.distance_m),
    Column.ASCENT: lambda a, _z: ascent_m_text(a.summary.ascent_m),
    Column.LOAD: lambda a, _z: QLocale().toString(round(a.load.trimp_eq)),
    Column.AVG_HR: lambda a, _z: _bpm_text(a.load.avg_bpm),
    Column.MAX_HR: lambda a, _z: _bpm_text(a.load.max_bpm),
    Column.LOAD_SOURCE: lambda a, _z: load_source_text(a.load.source),
}

_SORT_KEY_OF: dict[Column, Callable[[ProfileActivity], _Key]] = {
    Column.START: lambda a: a.start.timestamp(),
    Column.SPORT: lambda a: sport_text(a.sport).casefold(),
    Column.MOVING_TIME: lambda a: a.timer_s,
    Column.TOTAL_TIME: lambda a: a.elapsed_s,
    Column.DISTANCE: lambda a: _missing_as_key(a.summary.distance_m),
    Column.ASCENT: lambda a: _missing_as_key(a.summary.ascent_m),
    Column.LOAD: lambda a: a.load.trimp_eq,
    Column.AVG_HR: lambda a: _missing_as_key(a.load.avg_bpm),
    Column.MAX_HR: lambda a: _missing_as_key(a.load.max_bpm),
    Column.LOAD_SOURCE: lambda a: load_source_text(a.load.source).casefold(),
}


class ActivityFilterProxy(QSortFilterProxyModel):
    """Sorts by the model's keys and, if told, shows only the activities that passed the filter."""

    def __init__(self, model: ActivityTableModel, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._model = model
        self._allowed: frozenset[str] | None = None

    def set_allowed(self, keys: frozenset[str] | None) -> None:
        """Show only these identity keys (`None` shows every row)."""
        if keys != self._allowed:
            self.beginFilterChange()
            self._allowed = keys
            self.endFilterChange(QSortFilterProxyModel.Direction.Rows)

    def filterAcceptsRow(  # noqa: N802 - Qt
        self, source_row: int, source_parent: QModelIndex | QPersistentModelIndex
    ) -> bool:
        """Whether the row's activity passed the filter."""
        del source_parent
        if self._allowed is None:
            return True
        activity = self._model.activity_at(source_row)
        return activity is not None and identity_key(activity.identity) in self._allowed


def sorted_table_model(
    model: ActivityTableModel, parent: QObject | None = None
) -> ActivityFilterProxy:
    """Proxy that sorts by column using the model's keys (most recent first)."""
    proxy = ActivityFilterProxy(model, parent)
    proxy.setSourceModel(model)
    proxy.setSortRole(SORT_ROLE)
    proxy.setDynamicSortFilter(True)
    proxy.sort(Column.START, Qt.SortOrder.DescendingOrder)
    return proxy


class ActivitiesPage(QWidget):
    """Page "Activities": the table, or the empty state with the first step."""

    import_requested = Signal()
    detail_requested = Signal(object)  # ActivityIdentity, on double click or Enter
    selection_changed = Signal()
    area_only_toggled = Signal(bool)  # "Only the visible map area" was checked or unchecked

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._model = ActivityTableModel(self)
        self._proxy = sorted_table_model(self._model, self)
        self._table = QTableView(self)
        self._table.setObjectName("activitiesTable")
        self._table.setAccessibleName(self.tr("Activities"))
        self._table.setModel(self._proxy)
        self._table.setSortingEnabled(True)
        self._table.sortByColumn(Column.START, Qt.SortOrder.DescendingOrder)
        self._table.activated.connect(self._on_activated)
        self._table.selectionModel().selectionChanged.connect(
            lambda *_: self.selection_changed.emit()
        )
        self._table.setShowGrid(False)  # no vertical or horizontal lines
        self._table.setAlternatingRowColors(True)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.verticalHeader().setVisible(False)
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.horizontalHeader().setResizeContentsPrecision(_WIDTH_SAMPLE_ROWS)
        self._syncing = False
        self._count = QLabel(self)
        self._count.setObjectName("activitiesCount")
        self._count.setTextFormat(Qt.TextFormat.PlainText)
        self._area_only = QCheckBox(self.tr("Only the visible map area"), self)
        self._area_only.setObjectName("activitiesAreaOnly")
        self._area_only.setToolTip(
            self.tr("List only the activities that start in the area the map shows.")
        )
        self._area_only.toggled.connect(self.area_only_toggled)
        self._empty = StatePage(self)
        self._empty.set_content(
            self.tr("No activities yet"),
            self.tr("To get started, import FIT files."),
            self.tr("Import files..."),
        )
        self._empty.action_requested.connect(self.import_requested)
        self._stack = QStackedWidget(self)
        self._stack.addWidget(self._empty)
        self._stack.addWidget(self._table)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._count)
        layout.addWidget(self._area_only)
        layout.addWidget(self._stack)
        self._stack.setCurrentWidget(self._empty)
        self._count.hide()
        self._area_only.hide()

    @property
    def area_only_check(self) -> QCheckBox:
        """The check box "Only the visible map area" (shown while there are activities)."""
        return self._area_only

    @property
    def table(self) -> QTableView:
        """The table view."""
        return self._table

    @property
    def model(self) -> ActivityTableModel:
        """The model (rows in domain order)."""
        return self._model

    @property
    def proxy(self) -> ActivityFilterProxy:
        """The sorting and filtering proxy."""
        return self._proxy

    @property
    def count_text(self) -> str:
        """How many activities the table shows, out of how many (empty without activities)."""
        return self._count.text()

    def set_visible_identities(self, keys: frozenset[str] | None) -> None:
        """Show only the activities that passed the shared filter (`None`: no filter)."""
        self._proxy.set_allowed(keys)
        self._update_count()

    def select_identity(self, identity: ActivityIdentity | None) -> None:
        """Select the row of an activity (or none), without telling who listens to the user."""
        self._syncing = True
        try:
            selection = self._table.selectionModel()
            row = None if identity is None else self._model.row_of(identity)
            if row is None:
                selection.clearSelection()
                return
            index = self._proxy.mapFromSource(self._model.index(row, 0))
            if index.isValid():
                self._table.selectRow(index.row())
                self._table.scrollTo(index)
            else:
                selection.clearSelection()
        finally:
            self._syncing = False

    @property
    def is_syncing(self) -> bool:
        """True while a selection is being set by the program, not by the user."""
        return self._syncing

    def _update_count(self) -> None:
        total = self._model.rowCount()
        shown = self._proxy.rowCount()
        if total == 0:
            self._count.setText("")
            self._count.hide()
            self._area_only.hide()
            return
        # {shown}: rows shown after the filters; {total}: all the activities of the profile.
        self._count.setText(
            self.tr("{shown} of {total} activities").format(
                shown=QLocale().toString(shown), total=QLocale().toString(total)
            )
        )
        self._count.show()
        self._area_only.show()

    @property
    def empty_page(self) -> StatePage:
        """The empty state."""
        return self._empty

    def selected_activity(self) -> ProfileActivity | None:
        """The activity of the selected row (the current one, if none is selected)."""
        rows = self._table.selectionModel().selectedRows()
        index = rows[0] if rows else self._table.currentIndex()
        if not index.isValid():
            return None
        return self._model.activity_at(self._proxy.mapToSource(index).row())

    def request_detail(self) -> None:
        """Request the detail of the selected activity (no effect without a selection)."""
        activity = self.selected_activity()
        if activity is not None:
            self.detail_requested.emit(activity.identity)

    def _on_activated(self, index: QModelIndex) -> None:
        activity = self._model.activity_at(self._proxy.mapToSource(index).row())
        if activity is not None:
            self.detail_requested.emit(activity.identity)

    def show_activities(self, activities: Sequence[ProfileActivity], zone: ZoneInfo) -> None:
        """Show the activities, or the empty state if there are none."""
        self._model.set_activities(activities, zone)
        self._update_count()
        self._stack.setCurrentWidget(self._table if activities else self._empty)
        if activities:
            self._table.resizeColumnsToContents()
