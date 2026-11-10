# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Measurement history table: one row per date, read-only.

Weight, HR parameters and threshold pace have their own dates: a cell is empty if that
measurement was not recorded that day. The cycling and other-activities threshold HR are optional
parts of the HR entry: their cell is empty when the entry leaves them at the default. Numbers sit
on the right, units are in the header;
per-column sorting uses numeric keys. Weight and HR are health data, local only.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import IntEnum

from PySide6.QtCore import (
    QAbstractTableModel,
    QDate,
    QLocale,
    QModelIndex,
    QObject,
    QPersistentModelIndex,
    QSortFilterProxyModel,
    Qt,
)

from ponos_coach.load.domain import Physiology
from ponos_coach.profiles.domain import ThresholdPace, Weight
from ponos_coach.profiles.measurements import MeasurementHistory

SORT_ROLE = Qt.ItemDataRole.UserRole
_ALIGN_RIGHT = int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
_ALIGN_LEFT = int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
_MISSING_SORT_KEY = -1.0  # an empty cell comes before every value, in ascending order
_WEIGHT_DECIMALS = 1
_SECONDS_PER_MINUTE = 60


class Column(IntEnum):
    """Table columns, in display order."""

    DATE = 0
    WEIGHT = 1
    MIN_BPM = 2
    MAX_BPM = 3
    THRESHOLD = 4
    BIKE_THRESHOLD = 5
    OTHER_THRESHOLD = 6
    THRESHOLD_PACE = 7


@dataclass(frozen=True, slots=True)
class HistoryRow:
    """The measurements recorded on a day; `None` where that day has no entry."""

    day: date
    weight: Weight | None
    physiology: Physiology | None
    pace: ThresholdPace | None = None


def history_rows(history: MeasurementHistory) -> tuple[HistoryRow, ...]:
    """One row per date that has at least one entry, in ascending date order."""
    weights = {entry.valid_from: entry.weight for entry in history.weights}
    physiology = {entry.valid_from: entry.physiology for entry in history.physiology}
    paces = {entry.valid_from: entry.pace for entry in history.threshold_paces}
    days = sorted(weights.keys() | physiology.keys() | paces.keys())
    return tuple(
        HistoryRow(day, weights.get(day), physiology.get(day), paces.get(day)) for day in days
    )


def pace_text(pace: ThresholdPace) -> str:
    """The pace as `m:ss` (per kilometre)."""
    minutes, seconds = divmod(pace.s_per_km, _SECONDS_PER_MINUTE)
    return f"{minutes}:{seconds:02d}"


def weight_text(weight: Weight) -> str:
    """The weight as text in the locale's format, with one decimal digit."""
    return QLocale().toString(float(weight.kg), "f", _WEIGHT_DECIMALS)


class MeasurementHistoryModel(QAbstractTableModel):
    """The rows of a profile's history."""

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._rows: tuple[HistoryRow, ...] = ()

    def set_history(self, history: MeasurementHistory) -> None:
        """Replace the rows."""
        self.beginResetModel()
        self._rows = history_rows(history)
        self.endResetModel()

    def rowCount(  # noqa: N802 - Qt
        self, parent: QModelIndex | QPersistentModelIndex | None = None
    ) -> int:
        """Number of dates."""
        if parent is not None and parent.isValid():
            return 0
        return len(self._rows)

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
        """Headers, with the unit of measure."""
        if orientation != Qt.Orientation.Horizontal or not 0 <= section < len(Column):
            return None
        if role == Qt.ItemDataRole.DisplayRole:
            return self._header_text(Column(section))
        if role == Qt.ItemDataRole.TextAlignmentRole:
            return _ALIGN_LEFT if section == Column.DATE else _ALIGN_RIGHT
        return None

    def data(
        self,
        index: QModelIndex | QPersistentModelIndex,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> object:
        """Text, alignment and sort key of a cell."""
        if not index.isValid() or not 0 <= index.row() < len(self._rows):
            return None
        column = Column(index.column())
        row = self._rows[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            return _text(row, column)
        if role == Qt.ItemDataRole.TextAlignmentRole:
            return _ALIGN_LEFT if column is Column.DATE else _ALIGN_RIGHT
        if role == SORT_ROLE:
            return _sort_key(row, column)
        return None

    def _header_text(self, column: Column) -> str:
        headers = {
            Column.DATE: self.tr("Valid from"),
            Column.WEIGHT: self.tr("Weight (kg)"),
            Column.MIN_BPM: self.tr("Resting HR (bpm)"),
            Column.MAX_BPM: self.tr("Maximum HR (bpm)"),
            Column.THRESHOLD: self.tr("Running threshold HR (bpm)"),
            Column.BIKE_THRESHOLD: self.tr("Cycling threshold HR (bpm)"),
            Column.OTHER_THRESHOLD: self.tr("Other activities threshold HR (bpm)"),
            Column.THRESHOLD_PACE: self.tr("Threshold pace (min/km)"),
        }
        return headers[column]


def _bpm_of(row: HistoryRow, column: Column) -> int | None:
    if row.physiology is None:
        return None
    if column is Column.MIN_BPM:
        return row.physiology.limits.min_bpm
    if column is Column.MAX_BPM:
        return row.physiology.limits.max_bpm
    if column is Column.BIKE_THRESHOLD:
        entered = row.physiology.bike_threshold
        return None if entered is None else entered.bpm
    if column is Column.OTHER_THRESHOLD:
        entered = row.physiology.other_threshold
        return None if entered is None else entered.bpm
    return row.physiology.threshold.bpm


def _text(row: HistoryRow, column: Column) -> str:
    if column is Column.DATE:
        return QLocale().toString(
            QDate(row.day.year, row.day.month, row.day.day), QLocale.FormatType.ShortFormat
        )
    if column is Column.WEIGHT:
        return "" if row.weight is None else weight_text(row.weight)
    if column is Column.THRESHOLD_PACE:
        return "" if row.pace is None else pace_text(row.pace)
    bpm = _bpm_of(row, column)
    return "" if bpm is None else QLocale().toString(bpm)


def _sort_key(row: HistoryRow, column: Column) -> float:
    if column is Column.DATE:
        return float(row.day.toordinal())
    if column is Column.WEIGHT:
        return _MISSING_SORT_KEY if row.weight is None else float(row.weight.kg)
    if column is Column.THRESHOLD_PACE:
        return _MISSING_SORT_KEY if row.pace is None else float(row.pace.s_per_km)
    bpm = _bpm_of(row, column)
    return _MISSING_SORT_KEY if bpm is None else float(bpm)


def sorted_history_model(
    model: MeasurementHistoryModel, parent: QObject | None = None
) -> QSortFilterProxyModel:
    """Proxy that sorts by column using the model's keys (most recent dates first)."""
    proxy = QSortFilterProxyModel(parent)
    proxy.setSourceModel(model)
    proxy.setSortRole(SORT_ROLE)
    proxy.setDynamicSortFilter(True)
    proxy.sort(Column.DATE, Qt.SortOrder.DescendingOrder)
    return proxy
