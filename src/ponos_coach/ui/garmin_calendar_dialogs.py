# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Garmin calendar dialogs: what to send, and the list of the workouts on the calendar.

Humble views: the texts and the availability come ready from `garmin_calendar_ui`, and the
buttons only hand the choice over with a signal. The jobs run on a thread (`SyncController`); the
list dialog stays open and says when it is busy.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from PySide6.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    QObject,
    QPersistentModelIndex,
    QSortFilterProxyModel,
    Qt,
    Signal,
)
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QHeaderView,
    QLabel,
    QPushButton,
    QRadioButton,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from ponos_coach.planning.calendar_use_cases import MAX_WORKOUTS_PER_SEND
from ponos_coach.planning.domain.workouts import ScheduledWorkout
from ponos_coach.ui.garmin_calendar_model import (
    MAX_SELECTED_FOR_DELETION,
    ScopeChoice,
    too_many_selected,
)
from ponos_coach.ui.garmin_calendar_text import day_text
from ponos_coach.ui.tokens import DIALOG_MIN_WIDTH, SPACE_2, SPACE_4

_SORT_ROLE = Qt.ItemDataRole.UserRole


@dataclass(frozen=True, slots=True)
class ScopeOption:
    """One thing the user can send: its label, how many sessions it covers and whether it can be
    chosen (with the reason in `tooltip` when not)."""

    choice: ScopeChoice
    label: str
    count: int
    enabled: bool
    tooltip: str = ""


class SendToGarminDialog(QDialog):
    """Choose what to send: the whole plan, the selected week or the selected day's session."""

    chosen = Signal(object)  # ScopeChoice

    def __init__(self, options: Sequence[ScopeOption], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("sendToGarminDialog")
        self.setWindowTitle(self.tr("Send plan to Garmin"))
        self.setMinimumWidth(DIALOG_MIN_WIDTH)
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self._options = tuple(options)
        self._radios: dict[ScopeChoice, QRadioButton] = {}
        self._build()
        self._on_selection_changed()

    def _build(self) -> None:
        intro = QLabel(
            self.tr(
                "The sessions are sent as structured workouts with heart-rate targets. "
                "Workouts already on the calendar are left as they are."
            ),
            self,
        )
        intro.setWordWrap(True)
        intro.setTextFormat(Qt.TextFormat.PlainText)
        layout = QVBoxLayout(self)
        layout.setSpacing(SPACE_4)
        layout.addWidget(intro)
        first_enabled = True
        for option in self._options:
            radio = QRadioButton(option.label, self)
            radio.setObjectName(f"scope_{option.choice.value}")
            radio.setEnabled(option.enabled)
            radio.setToolTip(option.tooltip)
            if option.enabled and first_enabled:
                radio.setChecked(True)
                first_enabled = False
            layout.addWidget(radio)
            self._radios[option.choice] = radio
        self._cap_note = QLabel(self)
        self._cap_note.setWordWrap(True)
        self._cap_note.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self._cap_note)
        buttons = QDialogButtonBox(self)
        self._send = buttons.addButton(self.tr("Send"), QDialogButtonBox.ButtonRole.AcceptRole)
        self._send.setObjectName("sendToGarminSend")
        self._send.setDefault(True)
        self._cancel = buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        self._send.clicked.connect(self._submit)
        self._cancel.clicked.connect(self.reject)
        layout.addWidget(buttons)
        for radio in self._radios.values():  # only now: the handler needs the button
            radio.toggled.connect(self._on_selection_changed)

    # --- Accessors for tests and for the view -------------------------------------------------

    @property
    def send_button(self) -> QPushButton:
        """The "Send ..." button (its text says how many workouts)."""
        return self._send

    def radio(self, choice: ScopeChoice) -> QRadioButton:
        """The radio button of a scope."""
        return self._radios[choice]

    @property
    def selected_choice(self) -> ScopeChoice | None:
        """The scope chosen now, if any can be."""
        for choice, radio in self._radios.items():
            if radio.isChecked():
                return choice
        return None

    @property
    def cap_note_text(self) -> str:
        """The note about the limit of one send (empty if the choice fits)."""
        return "" if self._cap_note.isHidden() else self._cap_note.text()

    # --- Behaviour -----------------------------------------------------------------------------

    def _count_of(self, choice: ScopeChoice | None) -> int:
        return next((o.count for o in self._options if o.choice is choice), 0)

    def _on_selection_changed(self, *_ignored: object) -> None:
        choice = self.selected_choice
        count = self._count_of(choice)
        sent = min(count, MAX_WORKOUTS_PER_SEND)
        self._send.setText(self.tr("Send workouts (%n)", "", sent))
        self._send.setEnabled(choice is not None and sent > 0)
        beyond = count - sent
        self._cap_note.setVisible(beyond > 0)
        if beyond > 0:
            # {limit}: workouts sent at a time; %n: sessions that wait for another send.
            self._cap_note.setText(
                self.tr(
                    "At most {limit} workouts are sent at a time. Sessions waiting for another "
                    "send: %n.",
                    "",
                    beyond,
                ).format(limit=MAX_WORKOUTS_PER_SEND)
            )

    def _submit(self) -> None:
        choice = self.selected_choice
        if choice is not None:
            self.chosen.emit(choice)
            self.accept()


class ScheduledWorkoutsModel(QAbstractTableModel):
    """The workouts on the Garmin calendar: day, name and who made them. Read-only."""

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._headers = (
            self.tr("Date"),
            self.tr("Workout"),
            self.tr("Created by"),
        )
        self._app_text = self.tr("Ponos Coach")
        self._other_text = self.tr("Other")
        self._items: tuple[ScheduledWorkout, ...] = ()

    def set_workouts(self, items: Sequence[ScheduledWorkout]) -> None:
        """Replace all the rows."""
        self.beginResetModel()
        self._items = tuple(items)
        self.endResetModel()

    def workout_at(self, row: int) -> ScheduledWorkout | None:
        """The workout of a row, if the row exists."""
        return self._items[row] if 0 <= row < len(self._items) else None

    def rowCount(  # noqa: N802 - Qt
        self, parent: QModelIndex | QPersistentModelIndex | None = None
    ) -> int:
        """Number of rows (no child rows)."""
        if parent is not None and parent.isValid():
            return 0
        return len(self._items)

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
        """Header text."""
        if (
            orientation == Qt.Orientation.Horizontal
            and role == Qt.ItemDataRole.DisplayRole
            and 0 <= section < len(self._headers)
        ):
            return self._headers[section]
        return None

    def flags(self, index: QModelIndex | QPersistentModelIndex) -> Qt.ItemFlag:
        """Rows can be selected, nothing can be edited."""
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable

    def data(
        self,
        index: QModelIndex | QPersistentModelIndex,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> object:
        """The text of a cell, and the key it sorts by."""
        item = self.workout_at(index.row()) if index.isValid() else None
        if item is None:
            return None
        column = index.column()
        if role == Qt.ItemDataRole.DisplayRole:
            return (day_text(item.day), item.name, self._creator(item))[column]
        if role == _SORT_ROLE:
            day, name, creator = item.day.toordinal(), item.name.casefold(), self._creator(item)
            return ((day, name), (name, day), (creator, day, name))[column]
        return None

    def _creator(self, item: ScheduledWorkout) -> str:
        return self._app_text if item.is_app_created else self._other_text


class _KeyProxy(QSortFilterProxyModel):
    """Sorts by the sort keys the model gives (tuples, which Qt cannot compare by itself)."""

    def lessThan(  # noqa: N802 - Qt
        self,
        left: QModelIndex | QPersistentModelIndex,
        right: QModelIndex | QPersistentModelIndex,
    ) -> bool:
        """Compare the sort keys of the two cells."""
        return bool(left.data(_SORT_ROLE) < right.data(_SORT_ROLE))


class GarminCalendarDialog(QDialog):
    """The workouts on the Garmin calendar from today on, with "Delete selected" and "Recreate
    deleted"."""

    refresh_requested = Signal()
    delete_requested = Signal(object)  # tuple[ScheduledWorkout, ...]
    recreate_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("garminCalendarDialog")
        self.setWindowTitle(self.tr("Garmin calendar"))
        self.setMinimumWidth(DIALOG_MIN_WIDTH + 2 * SPACE_4)
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self._busy = False
        self._pending = 0
        self._model = ScheduledWorkoutsModel(self)
        self._proxy = _KeyProxy(self)
        self._proxy.setSourceModel(self._model)
        self._proxy.setSortRole(_SORT_ROLE)
        self._build()
        self.show_loading()

    def _build(self) -> None:
        self._status = QLabel(self)
        self._status.setObjectName("garminCalendarStatus")
        self._status.setWordWrap(True)
        self._status.setTextFormat(Qt.TextFormat.PlainText)
        self._table = QTableView(self)
        self._table.setObjectName("garminCalendarTable")
        self._table.setAccessibleName(self.tr("Workouts on the Garmin calendar"))
        self._table.setModel(self._proxy)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setShowGrid(False)
        self._table.setSortingEnabled(True)
        self._table.sortByColumn(0, Qt.SortOrder.AscendingOrder)
        self._table.verticalHeader().setVisible(False)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._table.selectionModel().selectionChanged.connect(self._update_buttons)
        self._refresh = QPushButton(self.tr("Refresh"), self)
        self._refresh.setObjectName("garminCalendarRefresh")
        self._refresh.setShortcut(QKeySequence("F5"))
        self._refresh.clicked.connect(self.refresh_requested)
        self._delete = QPushButton(self.tr("Delete selected"), self)
        self._delete.setObjectName("garminCalendarDelete")
        self._delete.setShortcut(QKeySequence(QKeySequence.StandardKey.Delete))
        self._delete.clicked.connect(self._on_delete)
        self._recreate = QPushButton(self.tr("Recreate deleted"), self)
        self._recreate.setObjectName("garminCalendarRecreate")
        self._recreate.clicked.connect(self.recreate_requested)
        self._close = QPushButton(self.tr("Close"), self)
        self._close.clicked.connect(self.reject)
        row = QDialogButtonBox(self)
        for button in (self._refresh, self._delete, self._recreate):
            row.addButton(button, QDialogButtonBox.ButtonRole.ActionRole)
        row.addButton(self._close, QDialogButtonBox.ButtonRole.RejectRole)
        layout = QVBoxLayout(self)
        layout.setSpacing(SPACE_2)
        layout.addWidget(self._status)
        layout.addWidget(self._table, stretch=1)
        layout.addWidget(row)

    # --- Accessors for tests and for the view -------------------------------------------------

    @property
    def table(self) -> QTableView:
        """The table of workouts."""
        return self._table

    @property
    def model(self) -> ScheduledWorkoutsModel:
        """The workouts, as listed."""
        return self._model

    @property
    def delete_button(self) -> QPushButton:
        """The "Delete selected" button."""
        return self._delete

    @property
    def recreate_button(self) -> QPushButton:
        """The "Recreate deleted" button."""
        return self._recreate

    @property
    def refresh_button(self) -> QPushButton:
        """The "Refresh" button."""
        return self._refresh

    @property
    def status_text(self) -> str:
        """The line above the table."""
        return self._status.text()

    def selected_workouts(self) -> tuple[ScheduledWorkout, ...]:
        """The workouts of the selected rows, in the order shown."""
        rows = sorted(index.row() for index in self._table.selectionModel().selectedRows())
        found = (
            self._model.workout_at(self._proxy.mapToSource(self._proxy.index(r, 0)).row())
            for r in rows
        )
        return tuple(item for item in found if item is not None)

    # --- States --------------------------------------------------------------------------------

    def show_loading(self) -> None:
        """The list is being read."""
        self._model.set_workouts(())
        self._status.setText(self.tr("Reading the Garmin calendar..."))
        self._update_buttons()

    def show_workouts(
        self, items: Sequence[ScheduledWorkout], pending: int, problem: str | None
    ) -> None:
        """Show the list. `pending` is how many deleted workouts wait to be created again;
        `problem` says why the list may be incomplete."""
        self._model.set_workouts(items)
        self._pending = pending
        if problem is not None:
            self._status.setText(problem)
        elif items:
            self._status.setText(self.tr("Workouts from today on: %n", "", len(items)))
        else:
            self._status.setText(self.tr("No workouts are scheduled from today on."))
        self._recreate.setText(self.tr("Recreate deleted (%n)", "", pending))
        self._update_buttons()

    def set_busy(self, busy: bool) -> None:
        """A job is running: the buttons wait for it."""
        self._busy = busy
        self._update_buttons()

    # --- Behaviour -----------------------------------------------------------------------------

    def _update_buttons(self, *_ignored: object) -> None:
        selected = len(self._table.selectionModel().selectedRows())
        self._refresh.setEnabled(not self._busy)
        busy = self.tr("Wait for the Garmin task to end.")
        if self._busy:
            reason = busy
        elif selected == 0:
            reason = self.tr("Select the workouts to delete first.")
        elif too_many_selected(selected):
            reason = self.tr("Select at most %n workouts at a time.", "", MAX_SELECTED_FOR_DELETION)
        else:
            reason = ""
        self._delete.setEnabled(not reason)
        self._delete.setToolTip(reason)
        can_recreate = not self._busy and self._pending > 0
        self._recreate.setEnabled(can_recreate)
        if self._busy:
            self._recreate.setToolTip(busy)
        elif self._pending == 0:
            self._recreate.setToolTip(self.tr("No deleted workouts are waiting."))
        else:
            self._recreate.setToolTip("")

    def _on_delete(self) -> None:
        items = self.selected_workouts()
        if items and not too_many_selected(len(items)):
            self.delete_requested.emit(items)
