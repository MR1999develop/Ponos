# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Dialogo "Modifica profilo": vista umile sul modello `MeasurementsForm`.

In alto i dati fissi in sola lettura (o da completare, una volta sola, per un profilo vecchio);
sotto le misure aggiornabili, che valgono da un giorno scelto (oggi per default); in fondo lo
storico. Il dialogo modifica una copia: "Annulla" non cambia nulla. Il salvataggio parte con
`save_requested`; l'esito (ok o misure rifiutate) lo comunica la finestra.
"""

from __future__ import annotations

from datetime import date

from PySide6.QtCore import QDate, QLocale, Qt, Signal
from PySide6.QtGui import QShowEvent
from PySide6.QtWidgets import (
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHeaderView,
    QLabel,
    QLineEdit,
    QSpinBox,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from ponos_coach.load.domain import Sex
from ponos_coach.profiles.domain import MIN_BIRTH_DATE
from ponos_coach.profiles.measurements import MeasurementHistory, MeasurementProblem
from ponos_coach.ui.birth_date_edit import BirthDateEdit, to_qdate
from ponos_coach.ui.body_input import BodyProblem, body_problem_text
from ponos_coach.ui.heart_rate_rules import (
    MAX_ALLOWED_BPM,
    MIN_ALLOWED_BPM,
    MIN_THRESHOLD_BPM,
    HeartRateProblem,
    heart_rate_problem_text,
)
from ponos_coach.ui.measurement_history_model import (
    MeasurementHistoryModel,
    sorted_history_model,
    weight_text,
)
from ponos_coach.ui.measurements_form import (
    EditField,
    EditIssue,
    MeasurementsForm,
    MeasurementsRequest,
)
from ponos_coach.ui.tokens import DIALOG_MIN_WIDTH, SPACE_1, SPACE_2, SPACE_4, Theme, tokens_for

_TABLE_MIN_HEIGHT = 140


class ProfileEditDialog(QDialog):
    """Dati fissi in sola lettura, misure datate da aggiornare e storico."""

    save_requested = Signal(object)  # MeasurementsRequest

    def __init__(
        self,
        form: MeasurementsForm,
        history: MeasurementHistory,
        profile_name: str,
        theme: Theme,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._form = form
        self._profile_name = profile_name
        self._error_color = tokens_for(theme).error
        self._show_errors = False
        self._errors = {field: self._error_label() for field in EditField}
        self._history_model = MeasurementHistoryModel(self)
        self.setWindowTitle(self.tr("Edit profile"))
        self.setMinimumWidth(DIALOG_MIN_WIDTH)
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self._build()
        self.set_history(history)
        self._load_from_form()
        self._update_save_button()

    # --- Costruzione ------------------------------------------------------------------

    def _build(self) -> None:
        self._valid_from = QDateEdit(self)
        self._valid_from.setObjectName("measurementsValidFrom")
        self._valid_from.setAccessibleName(self.tr("Valid from"))
        self._valid_from.setCalendarPopup(True)
        self._valid_from.setDisplayFormat(QLocale().dateFormat(QLocale.FormatType.ShortFormat))
        self._valid_from.setMinimumDate(to_qdate(MIN_BIRTH_DATE))
        self._valid_from.setMaximumDate(to_qdate(self._form.today))
        self._valid_from.dateChanged.connect(self._on_valid_from_changed)

        self._weight = QLineEdit(self)
        self._weight.setObjectName("measurementsWeight")
        self._weight.setAccessibleName(self.tr("Weight (kg)"))
        self._weight.textEdited.connect(self._on_weight_edited)
        self._min = self._bpm_box(MIN_ALLOWED_BPM, "measurementsMinHeartRate")
        self._max = self._bpm_box(MIN_ALLOWED_BPM, "measurementsMaxHeartRate")
        self._threshold = self._bpm_box(MIN_THRESHOLD_BPM, "measurementsThresholdHeartRate")
        self._min.valueChanged.connect(self._on_min_changed)
        self._max.valueChanged.connect(self._on_max_changed)
        self._threshold.valueChanged.connect(self._on_threshold_changed)

        self._build_fixed_fields()
        self._build_history_table()
        self._build_buttons()
        outer = QVBoxLayout(self)
        outer.setSpacing(SPACE_4)
        outer.addWidget(self._fixed_group())
        outer.addWidget(self._measurements_group())
        outer.addWidget(QLabel(self.tr("History"), self))
        outer.addWidget(self._table, stretch=1)
        outer.addWidget(self._errors[EditField.GENERAL])
        outer.addWidget(self._buttons)

    def _build_fixed_fields(self) -> None:
        self._height = QLineEdit(self)
        self._height.setObjectName("fixedHeight")
        self._height.setAccessibleName(self.tr("Height (cm)"))
        self._height.textEdited.connect(self._on_height_edited)
        self._birth = BirthDateEdit(self._form.today, self)
        self._birth.selection_changed.connect(self._on_birth_changed)

    def _build_history_table(self) -> None:
        self._table = QTableView(self)
        self._table.setObjectName("measurementsHistory")
        self._table.setAccessibleName(self.tr("Measurement history"))
        proxy = sorted_history_model(self._history_model, self)
        self._table.setModel(proxy)
        self._table.setSortingEnabled(True)
        self._table.setShowGrid(False)  # niente linee verticali né orizzontali
        self._table.setAlternatingRowColors(True)
        self._table.setEditTriggers(QTableView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self._table.verticalHeader().setVisible(False)
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._table.setMinimumHeight(_TABLE_MIN_HEIGHT)

    def _build_buttons(self) -> None:
        self._buttons = QDialogButtonBox(self)
        self._save = self._buttons.addButton(
            self.tr("Save changes"), QDialogButtonBox.ButtonRole.AcceptRole
        )
        self._save.setDefault(True)
        self._buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        self._buttons.accepted.connect(self._on_save)
        self._buttons.rejected.connect(self.reject)

    def _fixed_group(self) -> QGroupBox:
        box = QGroupBox(self.tr("Profile data"), self)
        layout = QFormLayout(box)
        layout.setVerticalSpacing(SPACE_2)
        layout.addRow(self.tr("Name"), self._read_only(self._profile_name, "fixedName"))
        sex_text = self.tr("Male") if self._form.sex is Sex.MALE else self.tr("Female")
        fixed = self._form.fixed
        if fixed is None:
            note = QLabel(
                self.tr(
                    "Height and date of birth are missing from this profile. "
                    "Enter them once: they cannot be changed afterwards."
                ),
                self,
            )
            note.setWordWrap(True)
            layout.addRow(note)
            layout.addRow(self.tr("Height (cm)"), self._with_error(self._height, EditField.HEIGHT))
            layout.addRow(
                self.tr("Date of birth"), self._with_error(self._birth, EditField.BIRTH_DATE)
            )
        else:
            height = self.tr("{cm} cm").format(cm=fixed.height.cm)
            layout.addRow(self.tr("Height"), self._read_only(height, "fixedHeightValue"))
            born = QLocale().toString(
                to_qdate(fixed.birth_date.value), QLocale.FormatType.ShortFormat
            )
            layout.addRow(self.tr("Date of birth"), self._read_only(born, "fixedBirthDate"))
            self._height.hide()  # niente da completare: i campi restano fuori dal modulo
            self._birth.hide()
        layout.addRow(self.tr("Sex"), self._read_only(sex_text, "fixedSex"))
        return box

    def _measurements_group(self) -> QGroupBox:
        box = QGroupBox(self.tr("New measurements"), self)
        layout = QFormLayout(box)
        layout.setVerticalSpacing(SPACE_2)
        hint = QLabel(
            self.tr(
                "Fields you do not change keep the values in force on the chosen day. "
                "The load of past activities is recalculated."
            ),
            self,
        )
        hint.setWordWrap(True)
        layout.addRow(hint)
        layout.addRow(
            self.tr("Valid from"), self._with_error(self._valid_from, EditField.VALID_FROM)
        )
        layout.addRow(self.tr("Weight (kg)"), self._with_error(self._weight, EditField.WEIGHT))
        layout.addRow(self.tr("Resting heart rate"), self._with_error(self._min, EditField.MIN_BPM))
        layout.addRow(self.tr("Maximum heart rate"), self._with_error(self._max, EditField.MAX_BPM))
        layout.addRow(
            self.tr("Threshold heart rate"), self._with_error(self._threshold, EditField.THRESHOLD)
        )
        return box

    def _read_only(self, text: str, name: str) -> QLabel:
        label = QLabel(text, self)
        label.setObjectName(name)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        return label

    def _bpm_box(self, low: int, name: str) -> QSpinBox:
        box = QSpinBox(self)
        box.setObjectName(name)
        box.setRange(low, MAX_ALLOWED_BPM)
        box.setSuffix(self.tr(" bpm"))
        return box

    def _error_label(self) -> QLabel:
        label = QLabel(self)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setWordWrap(True)
        label.setStyleSheet(f"color: {self._error_color};")
        label.setVisible(False)
        return label

    def _with_error(self, widget: QWidget, field: EditField) -> QWidget:
        box = QWidget(self)
        column = QVBoxLayout(box)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(SPACE_1)
        column.addWidget(widget)
        column.addWidget(self._errors[field])
        return box

    # --- Accessori per i test ---------------------------------------------------------

    @property
    def form(self) -> MeasurementsForm:
        """Il modello del modulo."""
        return self._form

    @property
    def valid_from_edit(self) -> QDateEdit:
        """Selettore del giorno di validità."""
        return self._valid_from

    @property
    def weight_edit(self) -> QLineEdit:
        """Campo del peso."""
        return self._weight

    @property
    def min_box(self) -> QSpinBox:
        """FC minima."""
        return self._min

    @property
    def max_box(self) -> QSpinBox:
        """FC massima."""
        return self._max

    @property
    def threshold_box(self) -> QSpinBox:
        """Soglia."""
        return self._threshold

    @property
    def height_edit(self) -> QLineEdit:
        """Altezza da completare (nascosta se il profilo ha già i dati fissi)."""
        return self._height

    @property
    def birth_edit(self) -> BirthDateEdit:
        """Data di nascita da completare (nascosta se il profilo ha già i dati fissi)."""
        return self._birth

    @property
    def save_button(self) -> QWidget:
        """Bottone "Salva modifiche"."""
        return self._save

    @property
    def history_table(self) -> QTableView:
        """La tabella dello storico."""
        return self._table

    def error_text(self, field: EditField) -> str:
        """Messaggio mostrato sotto un campo (vuoto se non c'è)."""
        return self._errors[field].text()

    # --- Dal modello alla vista -------------------------------------------------------

    def set_history(self, history: MeasurementHistory) -> None:
        """Mostra lo storico delle misure."""
        self._history_model.set_history(history)

    def _load_from_form(self) -> None:
        self._valid_from.blockSignals(True)
        self._valid_from.setDate(to_qdate(self._form.valid_from))
        self._valid_from.blockSignals(False)
        self._refresh_values()

    def _refresh_values(self) -> None:
        """Mostra i valori del modello: i campi non toccati seguono il giorno scelto."""
        form = self._form
        for box, value in (
            (self._min, form.min_bpm),
            (self._max, form.max_bpm),
            (self._threshold, form.threshold_bpm),
        ):
            box.blockSignals(True)
            box.setValue(value)
            box.blockSignals(False)
        if form.edited_weight_text is None:
            baseline = form.baseline_weight
            self._weight.setText("" if baseline is None else weight_text(baseline))

    def _refresh_errors(self, extra: tuple[EditIssue, ...] = ()) -> None:
        issues = (*self._form.issues(), *extra) if self._show_errors else extra
        for field, label in self._errors.items():
            first = next((i for i in issues if i.field is field), None)
            label.setText("" if first is None else self._issue_text(first))
            label.setVisible(first is not None)

    def _issue_text(self, issue: EditIssue) -> str:
        kind = issue.kind
        if isinstance(kind, BodyProblem):
            return body_problem_text(kind)
        if isinstance(kind, HeartRateProblem):
            return heart_rate_problem_text(kind)
        texts = {
            MeasurementProblem.NOTHING_TO_UPDATE: self.tr(
                "Nothing has changed: change at least one value."
            ),
            MeasurementProblem.DATE_IN_FUTURE: self.tr("The date cannot be in the future."),
            MeasurementProblem.DATE_TOO_OLD: self.tr("The date cannot be before {first}.").format(
                first=MIN_BIRTH_DATE.isoformat()
            ),
            MeasurementProblem.INVALID_HEART_RATE: self.tr(
                "The heart-rate values do not fit together: the resting rate must be below the "
                "threshold, and the threshold not above the maximum."
            ),
        }
        return texts[kind]

    def _update_save_button(self) -> None:
        """ "Salva modifiche" resta disabilitato, con il motivo nel tooltip, senza modifiche."""
        has_changes = self._form.has_changes
        self._save.setEnabled(has_changes)
        self._save.setToolTip("" if has_changes else self.tr("Change at least one value first."))

    # --- Dalla vista al modello -------------------------------------------------------

    def _after_edit(self) -> None:
        self._update_save_button()
        self._refresh_errors()

    def _on_valid_from_changed(self, value: QDate) -> None:
        self._form.set_valid_from(date(value.year(), value.month(), value.day()))
        self._refresh_values()
        self._after_edit()

    def _on_weight_edited(self, text: str) -> None:
        self._form.edit_weight(text)
        self._after_edit()

    def _on_min_changed(self, value: int) -> None:
        self._form.edit_min_bpm(value)
        self._after_edit()

    def _on_max_changed(self, value: int) -> None:
        self._form.edit_max_bpm(value)
        self._after_edit()

    def _on_threshold_changed(self, value: int) -> None:
        self._form.edit_threshold_bpm(value)
        self._after_edit()

    def _on_height_edited(self, text: str) -> None:
        self._form.height_text = text
        self._after_edit()

    def _on_birth_changed(self, value: object) -> None:
        self._form.birth_date = value if isinstance(value, date) else None
        self._after_edit()

    def _on_save(self) -> None:
        self._show_errors = True
        result = self._form.build()
        if isinstance(result, MeasurementsRequest):
            self._refresh_errors()
            self.set_busy(True)
            self.save_requested.emit(result)
            return
        self._refresh_errors()
        self._focus_first(result[0].field)

    def _focus_first(self, field: EditField) -> None:
        targets: dict[EditField, QWidget] = {
            EditField.VALID_FROM: self._valid_from,
            EditField.WEIGHT: self._weight,
            EditField.MIN_BPM: self._min,
            EditField.MAX_BPM: self._max,
            EditField.THRESHOLD: self._threshold,
            EditField.HEIGHT: self._height,
            EditField.BIRTH_DATE: self._birth,
        }
        target = targets.get(field)
        if target is not None:
            target.setFocus()

    # --- Esito del salvataggio --------------------------------------------------------

    def set_busy(self, busy: bool) -> None:
        """Mentre il salvataggio è in corso il dialogo non accetta altre modifiche."""
        self._save.setEnabled(not busy and self._form.has_changes)
        for widget in (
            self._valid_from,
            self._weight,
            self._min,
            self._max,
            self._threshold,
            self._height,
            self._birth,
        ):
            widget.setEnabled(not busy)

    def show_rejection(self, issue: EditIssue) -> None:
        """Le misure rifiutate dal caso d'uso: il problema compare sul campo giusto."""
        self.set_busy(False)
        self._show_errors = True
        self._refresh_errors((issue,))
        self._focus_first(issue.field)

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802 - Qt
        """Il primo campo da scrivere ha il fuoco: il peso, o l'altezza se va completata."""
        super().showEvent(event)
        (self._height if self._form.needs_fixed_data else self._weight).setFocus()
