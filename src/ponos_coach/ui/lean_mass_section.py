# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""The measured lean mass in "Nutrition settings": enter, list and remove dated measurements.

A humble view: it reads what the user types (`read_lean_mass`), says what is wrong on the field, and
hands the domain value or the measurement to remove to the dialog through signals; the dialog
starts the job and the undo. Lean mass is health data: never in logs.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

from PySide6.QtCore import QLocale, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDateEdit,
    QFormLayout,
    QGroupBox,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from ponos_coach.nutrition.domain import LeanMass, LeanMassEntry
from ponos_coach.nutrition.domain.settings import MIN_VALID_DATE
from ponos_coach.ui.birth_date_edit import from_qdate, to_qdate
from ponos_coach.ui.form_rows import add_field_row
from ponos_coach.ui.lean_mass_input import LeanMassProblem, lean_mass_problem_text, read_lean_mass
from ponos_coach.ui.text_table_model import TextTableModel
from ponos_coach.ui.tokens import SPACE_2

_TABLE_MIN_HEIGHT = 90
_DECIMALS = 1


def _short_date(day: date) -> str:
    return QLocale().toString(to_qdate(day), QLocale.FormatType.ShortFormat)


def _kg_text(lean_mass: LeanMass) -> str:
    return QLocale().toString(lean_mass.kg, "f", _DECIMALS)


class LeanMassSection(QGroupBox):
    """Group box with the lean mass field, its day, the "Set" button and the list."""

    set_requested = Signal(object, object)  # LeanMass, date
    remove_requested = Signal(object)  # LeanMassEntry

    def __init__(self, today: date, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setTitle(self.tr("Measured lean mass"))
        self._entries: tuple[LeanMassEntry, ...] = ()
        self._model = TextTableModel((self.tr("Valid from"), self.tr("Lean mass (kg)")), (1,), self)
        self._value = QLineEdit(self)
        self._value.setObjectName("settingsLeanMass")
        self._value.returnPressed.connect(self._on_set_clicked)
        self._value.textEdited.connect(self._clear_problem)
        self._error = QLabel(self)
        self._error.setObjectName("settingsLeanMassError")
        self._error.setTextFormat(Qt.TextFormat.PlainText)
        self._error.setWordWrap(True)
        self._error.setAccessibleName(self.tr("Problem with the lean mass"))
        self._from = QDateEdit(self)
        self._from.setObjectName("settingsLeanMassFrom")
        self._from.setCalendarPopup(True)
        self._from.setDisplayFormat(QLocale().dateFormat(QLocale.FormatType.ShortFormat))
        self._from.setDateRange(to_qdate(MIN_VALID_DATE), to_qdate(today))
        self._from.setDate(to_qdate(today))
        self._set = QPushButton(self.tr("Set lean mass"), self)
        self._set.setObjectName("settingsSetLeanMass")
        self._set.clicked.connect(self._on_set_clicked)
        self._remove = QPushButton(self.tr("Remove measurement"), self)
        self._remove.setObjectName("settingsRemoveLeanMass")
        self._remove.clicked.connect(self._on_remove_clicked)
        self._table = self._build_table()
        self._build_layout()
        self._update_remove_button()

    def _build_table(self) -> QTableView:
        table = QTableView(self)
        table.setObjectName("settingsLeanMasses")
        table.setAccessibleName(self.tr("Measured lean mass"))
        table.setModel(self._model)
        table.setShowGrid(False)  # no vertical or horizontal lines
        table.setAlternatingRowColors(True)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        table.setMinimumHeight(_TABLE_MIN_HEIGHT)
        table.selectionModel().selectionChanged.connect(self._update_remove_button)
        return table

    def _build_layout(self) -> None:
        note = QLabel(
            self.tr(
                "Optional. A body scan or an impedance scale gives it. When there is one, the "
                "limit on energy availability uses it instead of an estimate."
            ),
            self,
        )
        note.setWordWrap(True)
        holder = QWidget(self)
        column = QVBoxLayout(holder)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        column.addWidget(self._value)
        column.addWidget(self._error)
        form = QFormLayout()
        form.setVerticalSpacing(SPACE_2)
        add_field_row(form, self.tr("Lean mass (kg)"), self._value, holder)
        add_field_row(form, self.tr("Valid from"), self._from)
        layout = QVBoxLayout(self)
        layout.addWidget(note)
        layout.addLayout(form)
        layout.addWidget(self._set, alignment=Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(self._table)
        layout.addWidget(self._remove, alignment=Qt.AlignmentFlag.AlignLeft)

    # --- Test accessors ---------------------------------------------------------------------

    @property
    def value_edit(self) -> QLineEdit:
        """The field where the lean mass is typed."""
        return self._value

    @property
    def from_edit(self) -> QDateEdit:
        """The day from which the measurement holds."""
        return self._from

    @property
    def set_button(self) -> QPushButton:
        """The "Set lean mass" button."""
        return self._set

    @property
    def remove_button(self) -> QPushButton:
        """The "Remove measurement" button."""
        return self._remove

    @property
    def model(self) -> TextTableModel:
        """The list of measurements, newest first."""
        return self._model

    @property
    def table(self) -> QTableView:
        """The list view."""
        return self._table

    @property
    def problem_text(self) -> str:
        """The problem written under the field (empty if none)."""
        return self._error.text()

    # --- Showing and asking -----------------------------------------------------------------

    def apply(self, entries: Sequence[LeanMassEntry]) -> None:
        """Show the measurements the profile holds now (by ascending date, shown newest first)."""
        self._entries = tuple(reversed(entries))
        self._model.set_rows(
            [(_short_date(entry.valid_from), _kg_text(entry.lean_mass)) for entry in self._entries]
        )
        self._update_remove_button()

    def show_problem(self, text: str) -> None:
        """Write a problem under the field (a rule the domain refused)."""
        self._error.setText(text)

    def selected_entry(self) -> LeanMassEntry | None:
        """The measurement of the selected row, if any."""
        rows = self._table.selectionModel().selectedRows()
        row = rows[0].row() if rows else -1
        return self._entries[row] if 0 <= row < len(self._entries) else None

    def _clear_problem(self) -> None:
        self._error.clear()

    def _on_set_clicked(self) -> None:
        read = read_lean_mass(self._value.text())
        if isinstance(read, LeanMassProblem):
            self._error.setText(lean_mass_problem_text(read))
            return
        self._error.clear()
        self.set_requested.emit(read, from_qdate(self._from.date()))

    def _on_remove_clicked(self) -> None:
        entry = self.selected_entry()
        if entry is not None:
            self.remove_requested.emit(entry)

    def _update_remove_button(self) -> None:
        has_selection = self.selected_entry() is not None
        self._remove.setEnabled(has_selection)
        self._remove.setToolTip(
            "" if has_selection else self.tr("Select a measurement in the list first.")
        )
