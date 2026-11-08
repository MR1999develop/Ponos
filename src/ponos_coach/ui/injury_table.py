# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""The injuries and illnesses as a table: model and sorting proxy.

One row per record: what it is, when it began, where it stands and whether today's check-in is
missing. The texts come ready from `injury_texts`; the model serves them with the dates aligned on
the right and gives the proxy numeric keys, so a date sorts as a date and not as text. Every state
is words: the table never relies on colour.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from enum import IntEnum

from PySide6.QtCore import (
    QAbstractTableModel,
    QCoreApplication,
    QModelIndex,
    QObject,
    QPersistentModelIndex,
    QSortFilterProxyModel,
    Qt,
)

from ponos_coach.ui.injury_presentation import ProblemSummary
from ponos_coach.ui.injury_texts import checkin_cell_text, date_text, problem_title, status_text

SORT_ROLE = Qt.ItemDataRole.UserRole
PROBLEM_ID_ROLE = Qt.ItemDataRole.UserRole + 1

_ALIGN_RIGHT = int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
_ALIGN_LEFT = int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)


class InjuryColumn(IntEnum):
    """Columns, in display order."""

    PROBLEM = 0
    STARTED = 1
    STATUS = 2
    CHECK_IN = 3


def _header_text(column: InjuryColumn) -> str:
    texts = {
        InjuryColumn.PROBLEM: QCoreApplication.translate("InjuryTable", "Problem"),
        InjuryColumn.STARTED: QCoreApplication.translate("InjuryTable", "Started"),
        InjuryColumn.STATUS: QCoreApplication.translate("InjuryTable", "Status"),
        InjuryColumn.CHECK_IN: QCoreApplication.translate("InjuryTable", "Check-in"),
    }
    return texts[column]


def _cell_text(item: ProblemSummary, column: InjuryColumn) -> str:
    readers = {
        InjuryColumn.PROBLEM: problem_title,
        InjuryColumn.STARTED: lambda i: date_text(i.started_on),
        InjuryColumn.STATUS: status_text,
        InjuryColumn.CHECK_IN: checkin_cell_text,
    }
    return readers[column](item)


def _sort_key(item: ProblemSummary, column: InjuryColumn) -> object:
    if column is InjuryColumn.STARTED:
        return item.started_on.toordinal()
    return _cell_text(item, column).casefold()


class InjuryTableModel(QAbstractTableModel):
    """The records, one row each, read-only."""

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._items: tuple[ProblemSummary, ...] = ()

    def set_items(self, items: Sequence[ProblemSummary]) -> None:
        """Replace the records shown."""
        self.beginResetModel()
        self._items = tuple(items)
        self.endResetModel()

    def item_at(self, row: int) -> ProblemSummary | None:
        """The record of a row of the model, if there is one."""
        return self._items[row] if 0 <= row < len(self._items) else None

    def rowCount(  # noqa: N802 - Qt
        self, parent: QModelIndex | QPersistentModelIndex | None = None
    ) -> int:
        """Number of records (no child rows)."""
        if parent is not None and parent.isValid():
            return 0
        return len(self._items)

    def columnCount(  # noqa: N802 - Qt
        self, parent: QModelIndex | QPersistentModelIndex | None = None
    ) -> int:
        """Number of columns."""
        if parent is not None and parent.isValid():
            return 0
        return len(InjuryColumn)

    def headerData(  # noqa: N802 - Qt
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> object:
        """Header text and alignment."""
        if orientation != Qt.Orientation.Horizontal or not 0 <= section < len(InjuryColumn):
            return None
        column = InjuryColumn(section)
        if role == Qt.ItemDataRole.DisplayRole:
            return _header_text(column)
        if role == Qt.ItemDataRole.TextAlignmentRole:
            return _ALIGN_RIGHT if column is InjuryColumn.STARTED else _ALIGN_LEFT
        return None

    def data(
        self,
        index: QModelIndex | QPersistentModelIndex,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> object:
        """Text, sort key, alignment and id of a cell."""
        item = self.item_at(index.row()) if index.isValid() else None
        if item is None or not 0 <= index.column() < len(InjuryColumn):
            return None
        column = InjuryColumn(index.column())
        readers: dict[int, Callable[[], object]] = {
            Qt.ItemDataRole.DisplayRole: lambda: _cell_text(item, column),
            SORT_ROLE: lambda: _sort_key(item, column),
            PROBLEM_ID_ROLE: lambda: item.problem_id,
            Qt.ItemDataRole.TextAlignmentRole: lambda: (
                _ALIGN_RIGHT if column is InjuryColumn.STARTED else _ALIGN_LEFT
            ),
        }
        read = readers.get(role)
        return None if read is None else read()


class InjuryProxy(QSortFilterProxyModel):
    """Sorts the records by the keys of the model; the most recent first by default."""

    def problem_id_at(self, row: int) -> str | None:
        """The id of the record at a row of the proxy."""
        value = self.data(self.index(row, 0), PROBLEM_ID_ROLE)
        return value if isinstance(value, str) else None


def sorted_injuries_model(source: InjuryTableModel, parent: QObject | None = None) -> InjuryProxy:
    """A proxy over the model that sorts by the model's sort keys."""
    proxy = InjuryProxy(parent)
    proxy.setSourceModel(source)
    proxy.setSortRole(SORT_ROLE)
    return proxy
