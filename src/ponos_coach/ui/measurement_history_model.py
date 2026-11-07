# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Tabella dello storico delle misure: una riga per data, in sola lettura.

Peso e parametri di FC hanno date proprie: una cella è vuota se quel giorno quella misura non è
stata registrata. I numeri stanno a destra, le unità nell'intestazione; l'ordinamento per colonna
usa chiavi numeriche. Peso e FC sono dati di salute, solo in locale.
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
from ponos_coach.profiles.domain import Weight
from ponos_coach.profiles.measurements import MeasurementHistory

SORT_ROLE = Qt.ItemDataRole.UserRole
_ALIGN_RIGHT = int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
_ALIGN_LEFT = int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
_MISSING_SORT_KEY = -1.0  # una cella vuota viene prima di ogni valore, in ordine crescente
_WEIGHT_DECIMALS = 1


class Column(IntEnum):
    """Colonne della tabella, nell'ordine in cui compaiono."""

    DATE = 0
    WEIGHT = 1
    MIN_BPM = 2
    MAX_BPM = 3
    THRESHOLD = 4


@dataclass(frozen=True, slots=True)
class HistoryRow:
    """Le misure registrate in un giorno; `None` dove quel giorno non c'è una voce."""

    day: date
    weight: Weight | None
    physiology: Physiology | None


def history_rows(history: MeasurementHistory) -> tuple[HistoryRow, ...]:
    """Una riga per data in cui c'è almeno una voce, per data crescente."""
    weights = {entry.valid_from: entry.weight for entry in history.weights}
    physiology = {entry.valid_from: entry.physiology for entry in history.physiology}
    days = sorted(weights.keys() | physiology.keys())
    return tuple(HistoryRow(day, weights.get(day), physiology.get(day)) for day in days)


def weight_text(weight: Weight) -> str:
    """Il peso come testo nel formato del luogo, con una cifra decimale."""
    return QLocale().toString(float(weight.kg), "f", _WEIGHT_DECIMALS)


class MeasurementHistoryModel(QAbstractTableModel):
    """Le righe dello storico di un profilo."""

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._rows: tuple[HistoryRow, ...] = ()

    def set_history(self, history: MeasurementHistory) -> None:
        """Sostituisce le righe."""
        self.beginResetModel()
        self._rows = history_rows(history)
        self.endResetModel()

    def rowCount(  # noqa: N802 - Qt
        self, parent: QModelIndex | QPersistentModelIndex | None = None
    ) -> int:
        """Numero di date."""
        if parent is not None and parent.isValid():
            return 0
        return len(self._rows)

    def columnCount(  # noqa: N802 - Qt
        self, parent: QModelIndex | QPersistentModelIndex | None = None
    ) -> int:
        """Numero di colonne."""
        if parent is not None and parent.isValid():
            return 0
        return len(Column)

    def headerData(  # noqa: N802 - Qt
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> object:
        """Intestazioni, con l'unità di misura."""
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
        """Testo, allineamento e chiave di ordinamento di una cella."""
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
            Column.THRESHOLD: self.tr("Threshold HR (bpm)"),
        }
        return headers[column]


def _bpm_of(row: HistoryRow, column: Column) -> int | None:
    if row.physiology is None:
        return None
    if column is Column.MIN_BPM:
        return row.physiology.limits.min_bpm
    if column is Column.MAX_BPM:
        return row.physiology.limits.max_bpm
    return row.physiology.threshold.bpm


def _text(row: HistoryRow, column: Column) -> str:
    if column is Column.DATE:
        return QLocale().toString(
            QDate(row.day.year, row.day.month, row.day.day), QLocale.FormatType.ShortFormat
        )
    if column is Column.WEIGHT:
        return "" if row.weight is None else weight_text(row.weight)
    bpm = _bpm_of(row, column)
    return "" if bpm is None else QLocale().toString(bpm)


def _sort_key(row: HistoryRow, column: Column) -> float:
    if column is Column.DATE:
        return float(row.day.toordinal())
    if column is Column.WEIGHT:
        return _MISSING_SORT_KEY if row.weight is None else float(row.weight.kg)
    bpm = _bpm_of(row, column)
    return _MISSING_SORT_KEY if bpm is None else float(bpm)


def sorted_history_model(
    model: MeasurementHistoryModel, parent: QObject | None = None
) -> QSortFilterProxyModel:
    """Proxy che ordina per colonna con le chiavi del modello (date più recenti prima)."""
    proxy = QSortFilterProxyModel(parent)
    proxy.setSourceModel(model)
    proxy.setSortRole(SORT_ROLE)
    proxy.setDynamicSortFilter(True)
    proxy.sort(Column.DATE, Qt.SortOrder.DescendingOrder)
    return proxy
