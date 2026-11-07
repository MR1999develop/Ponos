# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Tabella delle attività: modello, ordinamento per colonna e pagina con il suo stato vuoto.

Il modello dà testi per la vista e chiavi numeriche per l'ordinamento; i numeri stanno a destra,
le unità nell'intestazione, la cella è vuota dove il dato manca (per esempio la distanza di una
seduta indoor). I filtri sono della fase 9.
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
    QHeaderView,
    QStackedWidget,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from ponos_coach.activities.domain import format_of
from ponos_coach.load.use_cases import ProfileActivity
from ponos_coach.ui.activity_text import (
    ascent_m_text,
    distance_km_text,
    duration_text,
    file_format_text,
    load_source_text,
    sport_text,
)
from ponos_coach.ui.state_page import StatePage

SORT_ROLE = Qt.ItemDataRole.UserRole
_MISSING_SORT_KEY = -1.0  # una cella vuota viene prima di ogni valore, in ordine crescente

type _Key = float | str

# Righe che la larghezza delle colonne guarda: i testi hanno tutti la stessa forma, quindi un
# campione basta (300 righe intere costavano circa 80 ms nel thread della UI).
_WIDTH_SAMPLE_ROWS = 30

_ALIGN_RIGHT = int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
_ALIGN_LEFT = int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)


class Column(IntEnum):
    """Colonne della tabella, nell'ordine in cui compaiono."""

    START = 0
    SPORT = 1
    MOVING_TIME = 2
    DISTANCE = 3
    ASCENT = 4
    LOAD = 5
    LOAD_SOURCE = 6
    FORMAT = 7


_NUMERIC_COLUMNS = frozenset({Column.MOVING_TIME, Column.DISTANCE, Column.ASCENT, Column.LOAD})


def _local_qdatetime(activity: ProfileActivity, zone: ZoneInfo) -> QDateTime:
    local = activity.start.astimezone(zone)
    return QDateTime(
        QDate(local.year, local.month, local.day),
        QTime(local.hour, local.minute, local.second),
    )


class ActivityTableModel(QAbstractTableModel):
    """Le attività di un profilo, una riga ciascuna, in sola lettura."""

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._activities: tuple[ProfileActivity, ...] = ()
        self._zone = ZoneInfo("UTC")

    def set_activities(self, activities: Sequence[ProfileActivity], zone: ZoneInfo) -> None:
        """Sostituisce le righe; `zone` è il fuso del profilo, per data e ora locali."""
        self.beginResetModel()
        self._activities = tuple(activities)
        self._zone = zone
        self.endResetModel()

    def rowCount(  # noqa: N802 - Qt
        self, parent: QModelIndex | QPersistentModelIndex | None = None
    ) -> int:
        """Numero di attività (nessuna riga figlia)."""
        if parent is not None and parent.isValid():
            return 0
        return len(self._activities)

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
        """Intestazioni, con l'unità di misura dove serve."""
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
        """Testo, allineamento e chiave di ordinamento di una cella."""
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
            Column.DISTANCE: self.tr("Distance (km)"),
            Column.ASCENT: self.tr("Ascent (m)"),
            Column.LOAD: self.tr("Load (hrTSS)"),
            Column.LOAD_SOURCE: self.tr("Load from"),
            Column.FORMAT: self.tr("Format"),
        }
        return headers[column]

    def activity_at(self, row: int) -> ProfileActivity | None:
        """L'attività di una riga del modello (non del proxy), o `None` se fuori intervallo."""
        return self._activities[row] if 0 <= row < len(self._activities) else None

    def _text(self, activity: ProfileActivity, column: Column) -> str:
        return _TEXT_OF[column](activity, self._zone)

    def _sort_key(self, activity: ProfileActivity, column: Column) -> _Key:
        return _SORT_KEY_OF[column](activity)


def _start_text(activity: ProfileActivity, zone: ZoneInfo) -> str:
    return QLocale().toString(_local_qdatetime(activity, zone), QLocale.FormatType.ShortFormat)


def _missing_as_key(value: float | None) -> float:
    return _MISSING_SORT_KEY if value is None else value


# Una funzione per colonna: testo mostrato e chiave di ordinamento.
_TEXT_OF: dict[Column, Callable[[ProfileActivity, ZoneInfo], str]] = {
    Column.START: _start_text,
    Column.SPORT: lambda a, _z: sport_text(a.sport),
    Column.MOVING_TIME: lambda a, _z: duration_text(a.timer_s),
    Column.DISTANCE: lambda a, _z: distance_km_text(a.summary.distance_m),
    Column.ASCENT: lambda a, _z: ascent_m_text(a.summary.ascent_m),
    Column.LOAD: lambda a, _z: QLocale().toString(round(a.load.load)),
    Column.LOAD_SOURCE: lambda a, _z: load_source_text(a.load.source),
    Column.FORMAT: lambda a, _z: file_format_text(format_of(a.identity)),
}

_SORT_KEY_OF: dict[Column, Callable[[ProfileActivity], _Key]] = {
    Column.START: lambda a: a.start.timestamp(),
    Column.SPORT: lambda a: sport_text(a.sport).casefold(),
    Column.MOVING_TIME: lambda a: a.timer_s,
    Column.DISTANCE: lambda a: _missing_as_key(a.summary.distance_m),
    Column.ASCENT: lambda a: _missing_as_key(a.summary.ascent_m),
    Column.LOAD: lambda a: a.load.load,
    Column.LOAD_SOURCE: lambda a: load_source_text(a.load.source).casefold(),
    Column.FORMAT: lambda a: file_format_text(format_of(a.identity)),
}


def sorted_table_model(
    model: ActivityTableModel, parent: QObject | None = None
) -> QSortFilterProxyModel:
    """Proxy che ordina per colonna con le chiavi del modello (più recenti prima)."""
    proxy = QSortFilterProxyModel(parent)
    proxy.setSourceModel(model)
    proxy.setSortRole(SORT_ROLE)
    proxy.setDynamicSortFilter(True)
    proxy.sort(Column.START, Qt.SortOrder.DescendingOrder)
    return proxy


class ActivitiesPage(QWidget):
    """Pagina "Attività": la tabella, o lo stato vuoto con il primo passo."""

    import_requested = Signal()
    detail_requested = Signal(object)  # ActivityIdentity, con doppio clic o Invio
    selection_changed = Signal()

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
        self._table.setShowGrid(False)  # niente linee verticali né orizzontali
        self._table.setAlternatingRowColors(True)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.verticalHeader().setVisible(False)
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.horizontalHeader().setResizeContentsPrecision(_WIDTH_SAMPLE_ROWS)
        self._empty = StatePage(self)
        self._empty.set_content(
            self.tr("No activities yet"),
            self.tr("To get started, import FIT, GPX or ZIP files."),
            self.tr("Import files..."),
        )
        self._empty.action_requested.connect(self.import_requested)
        self._stack = QStackedWidget(self)
        self._stack.addWidget(self._empty)
        self._stack.addWidget(self._table)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._stack)
        self._stack.setCurrentWidget(self._empty)

    @property
    def table(self) -> QTableView:
        """La vista a tabella."""
        return self._table

    @property
    def model(self) -> ActivityTableModel:
        """Il modello (le righe nell'ordine del dominio)."""
        return self._model

    @property
    def proxy(self) -> QSortFilterProxyModel:
        """Il proxy che ordina."""
        return self._proxy

    @property
    def empty_page(self) -> StatePage:
        """Lo stato vuoto."""
        return self._empty

    def selected_activity(self) -> ProfileActivity | None:
        """L'attività della riga selezionata (la corrente, se nessuna è selezionata)."""
        rows = self._table.selectionModel().selectedRows()
        index = rows[0] if rows else self._table.currentIndex()
        if not index.isValid():
            return None
        return self._model.activity_at(self._proxy.mapToSource(index).row())

    def request_detail(self) -> None:
        """Chiede il dettaglio dell'attività selezionata (nessun effetto senza selezione)."""
        activity = self.selected_activity()
        if activity is not None:
            self.detail_requested.emit(activity.identity)

    def _on_activated(self, index: QModelIndex) -> None:
        activity = self._model.activity_at(self._proxy.mapToSource(index).row())
        if activity is not None:
            self.detail_requested.emit(activity.identity)

    def show_activities(self, activities: Sequence[ProfileActivity], zone: ZoneInfo) -> None:
        """Mostra le attività, o lo stato vuoto se non ce ne sono."""
        self._model.set_activities(activities, zone)
        self._stack.setCurrentWidget(self._table if activities else self._empty)
        if activities:
            self._table.resizeColumnsToContents()
