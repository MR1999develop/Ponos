# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""The list at the right of the map: the filtered activities that start in the visible area.

Model for a `QTableView` behind a `QSortFilterProxyModel` (sorting by column, most recent first).
Numbers on the right, units in the headers, an empty cell where the datum is missing; a texts
function per column and a numeric key for sorting.
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
)

from ponos_coach.activities.domain import ActivityIdentity, ActivityRecord, identity_key
from ponos_coach.ui.activity_text import ascent_m_text, distance_km_text, duration_text, sport_text

SORT_ROLE = Qt.ItemDataRole.UserRole
_MISSING_KEY = -1.0

type _Key = float | str

_ALIGN_RIGHT = int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
_ALIGN_LEFT = int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)


class ListColumn(IntEnum):
    """Columns of the area list, in display order."""

    START = 0
    SPORT = 1
    DISTANCE = 2
    ASCENT = 3
    MOVING_TIME = 4


_NUMERIC = frozenset({ListColumn.DISTANCE, ListColumn.ASCENT, ListColumn.MOVING_TIME})


def start_text(record: ActivityRecord, zone: ZoneInfo) -> str:
    local = record.start.astimezone(zone)
    stamp = QDateTime(
        QDate(local.year, local.month, local.day), QTime(local.hour, local.minute, local.second)
    )
    return QLocale().toString(stamp, QLocale.FormatType.ShortFormat)


def _or_missing(value: float | None) -> float:
    return _MISSING_KEY if value is None else value


_TEXT_OF: dict[ListColumn, Callable[[ActivityRecord, ZoneInfo], str]] = {
    ListColumn.START: start_text,
    ListColumn.SPORT: lambda r, _z: sport_text(r.sport),
    ListColumn.DISTANCE: lambda r, _z: distance_km_text(r.summary.distance_m),
    ListColumn.ASCENT: lambda r, _z: ascent_m_text(r.summary.ascent_m),
    ListColumn.MOVING_TIME: lambda r, _z: duration_text(r.timer_s),
}
_KEY_OF: dict[ListColumn, Callable[[ActivityRecord], _Key]] = {
    ListColumn.START: lambda r: r.start.timestamp(),
    ListColumn.SPORT: lambda r: sport_text(r.sport).casefold(),
    ListColumn.DISTANCE: lambda r: _or_missing(r.summary.distance_m),
    ListColumn.ASCENT: lambda r: _or_missing(r.summary.ascent_m),
    ListColumn.MOVING_TIME: lambda r: r.timer_s,
}


class AreaListModel(QAbstractTableModel):
    """Activities of the visible area, one row each, read-only."""

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._records: tuple[ActivityRecord, ...] = ()
        self._rows: dict[str, int] = {}
        self._zone = ZoneInfo("UTC")

    def set_records(self, records: Sequence[ActivityRecord], zone: ZoneInfo) -> None:
        """Replace the rows."""
        self.beginResetModel()
        self._records = tuple(records)
        self._rows = {identity_key(r.identity): i for i, r in enumerate(self._records)}
        self._zone = zone
        self.endResetModel()

    def identity_at(self, row: int) -> ActivityIdentity | None:
        """The activity of a model row (not of the proxy)."""
        return self._records[row].identity if 0 <= row < len(self._records) else None

    def row_of(self, identity: ActivityIdentity) -> int | None:
        """The model row of an activity, if it is in the list."""
        return self._rows.get(identity_key(identity))

    def rowCount(  # noqa: N802 - Qt
        self, parent: QModelIndex | QPersistentModelIndex | None = None
    ) -> int:
        """Number of activities (no child rows)."""
        if parent is not None and parent.isValid():
            return 0
        return len(self._records)

    def columnCount(  # noqa: N802 - Qt
        self, parent: QModelIndex | QPersistentModelIndex | None = None
    ) -> int:
        """Number of columns."""
        if parent is not None and parent.isValid():
            return 0
        return len(ListColumn)

    def headerData(  # noqa: N802 - Qt
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> object:
        """Headers with units; numeric headers aligned to the right like their cells."""
        if orientation != Qt.Orientation.Horizontal or not 0 <= section < len(ListColumn):
            return None
        column = ListColumn(section)
        if role == Qt.ItemDataRole.DisplayRole:
            return self._header(column)
        if role == Qt.ItemDataRole.TextAlignmentRole:
            return _ALIGN_RIGHT if column in _NUMERIC else _ALIGN_LEFT
        return None

    def data(
        self, index: QModelIndex | QPersistentModelIndex, role: int = Qt.ItemDataRole.DisplayRole
    ) -> object:
        """Text, alignment and sort key of a cell."""
        if not index.isValid() or not 0 <= index.row() < len(self._records):
            return None
        column = ListColumn(index.column())
        record = self._records[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            return _TEXT_OF[column](record, self._zone)
        if role == Qt.ItemDataRole.TextAlignmentRole:
            return _ALIGN_RIGHT if column in _NUMERIC else _ALIGN_LEFT
        if role == SORT_ROLE:
            return _KEY_OF[column](record)
        return None

    def _header(self, column: ListColumn) -> str:
        headers = {
            ListColumn.START: self.tr("Date and time"),
            ListColumn.SPORT: self.tr("Sport"),
            ListColumn.DISTANCE: self.tr("Distance (km)"),
            ListColumn.ASCENT: self.tr("Ascent (m)"),
            ListColumn.MOVING_TIME: self.tr("Moving time (h:mm:ss)"),
        }
        return headers[column]


def sorted_list_model(model: AreaListModel, parent: QObject | None = None) -> QSortFilterProxyModel:
    """Proxy that sorts by the model's keys, most recent first."""
    proxy = QSortFilterProxyModel(parent)
    proxy.setSourceModel(model)
    proxy.setSortRole(SORT_ROLE)
    proxy.setDynamicSortFilter(True)
    proxy.sort(ListColumn.START, Qt.SortOrder.DescendingOrder)
    return proxy
