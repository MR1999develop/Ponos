# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Page "Deleted activities": the activities the user deleted, and the Restore button.

Humble view: the rows are texts made by `deleted_rows`; the page only shows them, keeps which
identity each row stands for and says what the user picked. Without deleted activities it says so,
in a sentence, instead of showing an empty table. Positions and heart rate are not here: a deleted
activity keeps only its sport, start, duration and distance.
"""

from __future__ import annotations

from collections.abc import Sequence
from zoneinfo import ZoneInfo

from PySide6.QtCore import QDate, QDateTime, QLocale, Qt, QTime, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QLabel,
    QPushButton,
    QStackedWidget,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from ponos_coach.activities.domain import ActivityIdentity, DeletedActivity
from ponos_coach.ui.activity_text import distance_km_text, duration_text, sport_text
from ponos_coach.ui.text_table_model import TextTableModel
from ponos_coach.ui.tokens import SPACE_2, SPACE_4

_NUMERIC_COLUMNS = (2, 3)


def start_text(deleted: DeletedActivity, zone: ZoneInfo) -> str:
    """Local date and time of the start, in the locale's short format."""
    local = deleted.start.astimezone(zone)
    moment = QDateTime(
        QDate(local.year, local.month, local.day), QTime(local.hour, local.minute, local.second)
    )
    return QLocale().toString(moment, QLocale.FormatType.ShortFormat)


def title_of(deleted: DeletedActivity, zone: ZoneInfo) -> str:
    """The activity in a few words ("Running, 3/2/26 7:00 AM"), for messages."""
    return f"{sport_text(deleted.sport)}, {start_text(deleted, zone)}"


def deleted_rows(
    deleted: Sequence[DeletedActivity], zone: ZoneInfo
) -> tuple[tuple[str, str, str, str], ...]:
    """The table rows: start, sport, duration and distance (empty without a distance)."""
    return tuple(
        (
            start_text(item, zone),
            sport_text(item.sport),
            duration_text(item.elapsed_s),
            distance_km_text(item.distance_m),
        )
        for item in deleted
    )


class DeletedActivitiesPage(QWidget):
    """The table of deleted activities with "Restore", or a sentence when there are none."""

    restore_requested = Signal(object)  # tuple[ActivityIdentity, ...]: the selected rows
    selection_changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._deleted: tuple[DeletedActivity, ...] = ()
        self._model = TextTableModel(
            (
                self.tr("Date and time"),
                self.tr("Sport"),
                self.tr("Total time (h:mm:ss)"),
                self.tr("Distance (km)"),
            ),
            _NUMERIC_COLUMNS,
            self,
        )
        self._hint = QLabel(
            self.tr(
                "Deleted activities stay out of the list, the charts and the synchronization. "
                "Their files are kept, so you can restore them."
            ),
            self,
        )
        self._hint.setObjectName("deletedActivitiesHint")
        self._hint.setTextFormat(Qt.TextFormat.PlainText)
        self._hint.setWordWrap(True)
        self._table = QTableView(self)
        self._table.setObjectName("deletedActivitiesTable")
        self._table.setAccessibleName(self.tr("Deleted activities"))
        self._table.setModel(self._model)
        self._table.setShowGrid(False)  # no vertical or horizontal lines
        self._table.setAlternatingRowColors(True)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._table.verticalHeader().setVisible(False)
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._table.selectionModel().selectionChanged.connect(self._on_selection_changed)
        self._table.activated.connect(lambda _index: self.request_restore())
        self._empty = QLabel(
            self.tr(
                "No deleted activities. An activity you delete from the Activities list "
                "appears here, and you can restore it."
            ),
            self,
        )
        self._empty.setObjectName("deletedActivitiesEmpty")
        self._empty.setTextFormat(Qt.TextFormat.PlainText)
        self._empty.setWordWrap(True)
        self._empty.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self._stack = QStackedWidget(self)
        self._stack.addWidget(self._empty)
        self._stack.addWidget(self._table)
        self._restore = QPushButton(self.tr("Restore"), self)
        self._restore.setObjectName("deletedActivitiesRestore")
        self._restore.setDefault(True)
        self._restore.clicked.connect(lambda _checked=False: self.request_restore())
        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACE_4, SPACE_4, SPACE_4, SPACE_4)
        layout.setSpacing(SPACE_2)
        layout.addWidget(self._hint)
        layout.addWidget(self._stack, stretch=1)
        layout.addWidget(self._restore, alignment=Qt.AlignmentFlag.AlignLeft)
        self.show_deleted((), ZoneInfo("UTC"))

    @property
    def table(self) -> QTableView:
        """The table (the empty sentence replaces it while there is nothing to show)."""
        return self._table

    @property
    def model(self) -> TextTableModel:
        """The table's model."""
        return self._model

    @property
    def restore_button(self) -> QPushButton:
        """The "Restore" button, enabled while something is selected."""
        return self._restore

    @property
    def empty_text(self) -> str:
        """The sentence of the empty state."""
        return self._empty.text()

    @property
    def is_empty_state(self) -> bool:
        """True while the sentence is shown instead of the table."""
        return self._stack.currentWidget() is self._empty

    def show_deleted(self, deleted: Sequence[DeletedActivity], zone: ZoneInfo) -> None:
        """Show these activities (the most recently deleted first), or the empty state."""
        self._deleted = tuple(deleted)
        self._model.set_rows(deleted_rows(self._deleted, zone))
        self._stack.setCurrentWidget(self._table if self._deleted else self._empty)
        self._update_button()

    def selected_identities(self) -> tuple[ActivityIdentity, ...]:
        """The identities of the selected rows, in list order."""
        rows = sorted(index.row() for index in self._table.selectionModel().selectedRows())
        return tuple(self._deleted[row].identity for row in rows if row < len(self._deleted))

    def request_restore(self) -> None:
        """Ask to restore the selected activities (no effect without a selection)."""
        selected = self.selected_identities()
        if selected:
            self.restore_requested.emit(selected)

    def _on_selection_changed(self) -> None:
        self._update_button()
        self.selection_changed.emit()

    def _update_button(self) -> None:
        has_selection = bool(self.selected_identities())
        self._restore.setEnabled(has_selection)
        self._restore.setToolTip(
            "" if has_selection else self.tr("Select a deleted activity in the list first.")
        )
