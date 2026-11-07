# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""The threshold test history as a table: model and sorting proxy.

The texts come ready from the presentation (`history_row`); the model serves them with numbers
aligned on the right and gives the proxy numeric keys, so a date or a pace sorts as a number and
not as text. The most recent test comes first.
"""

from __future__ import annotations

from collections.abc import Sequence
from enum import IntEnum
from zoneinfo import ZoneInfo

from PySide6.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    QObject,
    QPersistentModelIndex,
    QSortFilterProxyModel,
    Qt,
)

from ponos_coach.ui.threshold_presentation import HistoryEntry, HistoryRow, history_row

SORT_ROLE = Qt.ItemDataRole.UserRole

_ALIGN_RIGHT = int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
_ALIGN_LEFT = int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)


class HistoryColumn(IntEnum):
    """Columns, in display order."""

    DAY = 0
    ACTIVITY = 1
    THRESHOLD = 2
    PACE = 3
    METHOD = 4


_NUMERIC_COLUMNS = frozenset({HistoryColumn.THRESHOLD, HistoryColumn.PACE})


class ThresholdHistoryModel(QAbstractTableModel):
    """The applied tests, one row each, read-only."""

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._rows: tuple[HistoryRow, ...] = ()

    def set_entries(self, entries: Sequence[HistoryEntry], zone: ZoneInfo) -> None:
        """Replace the rows; `zone` is the profile's time zone, for the activities' local time."""
        self.beginResetModel()
        self._rows = tuple(history_row(entry, zone) for entry in entries)
        self.endResetModel()

    def rowCount(  # noqa: N802 - Qt
        self, parent: QModelIndex | QPersistentModelIndex | None = None
    ) -> int:
        """Number of tests (no child rows)."""
        if parent is not None and parent.isValid():
            return 0
        return len(self._rows)

    def columnCount(  # noqa: N802 - Qt
        self, parent: QModelIndex | QPersistentModelIndex | None = None
    ) -> int:
        """Number of columns."""
        if parent is not None and parent.isValid():
            return 0
        return len(HistoryColumn)

    def headerData(  # noqa: N802 - Qt
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> object:
        """Headers, with the unit where needed."""
        if orientation != Qt.Orientation.Horizontal or not 0 <= section < len(HistoryColumn):
            return None
        column = HistoryColumn(section)
        if role == Qt.ItemDataRole.DisplayRole:
            return self._header_text(column)
        if role == Qt.ItemDataRole.TextAlignmentRole:
            return _ALIGN_RIGHT if column in _NUMERIC_COLUMNS else _ALIGN_LEFT
        return None

    def data(
        self,
        index: QModelIndex | QPersistentModelIndex,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> object:
        """Text, alignment and sort key of a cell."""
        if not index.isValid() or not (
            0 <= index.row() < len(self._rows) and 0 <= index.column() < len(HistoryColumn)
        ):
            return None
        row = self._rows[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            return row.cells[index.column()]
        if role == Qt.ItemDataRole.TextAlignmentRole:
            return (
                _ALIGN_RIGHT if HistoryColumn(index.column()) in _NUMERIC_COLUMNS else _ALIGN_LEFT
            )
        if role == SORT_ROLE:
            return row.keys[index.column()]
        return None

    def _header_text(self, column: HistoryColumn) -> str:
        headers = {
            HistoryColumn.DAY: self.tr("Date"),
            HistoryColumn.ACTIVITY: self.tr("Activity"),
            HistoryColumn.THRESHOLD: self.tr("Threshold heart rate (bpm)"),
            HistoryColumn.PACE: self.tr("Threshold pace (min:s per km)"),
            HistoryColumn.METHOD: self.tr("Method"),
        }
        return headers[column]


def sorted_history_model(
    model: ThresholdHistoryModel, parent: QObject | None = None
) -> QSortFilterProxyModel:
    """Proxy that sorts by column using the model's keys (the most recent test first)."""
    proxy = QSortFilterProxyModel(parent)
    proxy.setSourceModel(model)
    proxy.setSortRole(SORT_ROLE)
    proxy.setDynamicSortFilter(True)
    proxy.sort(HistoryColumn.DAY, Qt.SortOrder.DescendingOrder)
    return proxy
