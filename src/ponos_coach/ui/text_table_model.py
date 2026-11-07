# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""A read-only table of ready-made texts: headers, rows, and which columns hold numbers.

The pages decide the texts (with units in the headers); this model only serves them, with numbers
aligned on the right.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QObject, QPersistentModelIndex, Qt

_ALIGN_RIGHT = int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
_ALIGN_LEFT = int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)


class TextTableModel(QAbstractTableModel):
    """Rows of texts under fixed headers; `numeric_columns` are right-aligned."""

    def __init__(
        self,
        headers: Sequence[str],
        numeric_columns: Collection[int] = (),
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._headers = tuple(headers)
        self._numeric = frozenset(numeric_columns)
        self._rows: tuple[tuple[str, ...], ...] = ()

    def set_rows(self, rows: Sequence[Sequence[str]]) -> None:
        """Replace all the rows; each has one text per header."""
        converted = tuple(tuple(row) for row in rows)
        if any(len(row) != len(self._headers) for row in converted):
            raise ValueError("every row needs one text per column")
        self.beginResetModel()
        self._rows = converted
        self.endResetModel()

    @property
    def rows(self) -> tuple[tuple[str, ...], ...]:
        """The texts, row by row."""
        return self._rows

    def rowCount(  # noqa: N802 - Qt
        self, parent: QModelIndex | QPersistentModelIndex | None = None
    ) -> int:
        """Number of rows (no child rows)."""
        if parent is not None and parent.isValid():
            return 0
        return len(self._rows)

    def columnCount(  # noqa: N802 - Qt
        self, parent: QModelIndex | QPersistentModelIndex | None = None
    ) -> int:
        """Number of columns."""
        if parent is not None and parent.isValid():
            return 0
        return len(self._headers)

    def headerData(  # noqa: N802 - Qt
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> object:
        """Header text and alignment."""
        if orientation != Qt.Orientation.Horizontal or not 0 <= section < len(self._headers):
            return None
        if role == Qt.ItemDataRole.DisplayRole:
            return self._headers[section]
        if role == Qt.ItemDataRole.TextAlignmentRole:
            return _ALIGN_RIGHT if section in self._numeric else _ALIGN_LEFT
        return None

    def data(
        self,
        index: QModelIndex | QPersistentModelIndex,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> object:
        """Text and alignment of a cell."""
        if not index.isValid() or not (
            0 <= index.row() < len(self._rows) and 0 <= index.column() < len(self._headers)
        ):
            return None
        if role == Qt.ItemDataRole.DisplayRole:
            return self._rows[index.row()][index.column()]
        if role == Qt.ItemDataRole.TextAlignmentRole:
            return _ALIGN_RIGHT if index.column() in self._numeric else _ALIGN_LEFT
        return None
