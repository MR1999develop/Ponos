# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Content of the Settings > Profile page: humble view on the `MeasurementsForm` model.

At the top the editable name and the fixed data, read-only (or to complete, once only, for an
old profile); below the updatable measurements, which apply from a chosen day (today by
default); at the bottom the history. The page edits a copy: nothing changes until "Save changes".
Saving starts with `save_requested`; the outcome (ok or rejected measurements) is reported by the
coordinator, which builds the page again from what was saved.
"""

from __future__ import annotations

from datetime import date
from itertools import pairwise

from PySide6.QtCore import QDate, QLocale, Qt, Signal
from PySide6.QtWidgets import (
    QDateEdit,
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
from ponos_coach.profiles.domain import MIN_MEASUREMENT_DATE, NAME_MAX_CHARS
from ponos_coach.profiles.measurements import MeasurementHistory, MeasurementProblem
from ponos_coach.ui.birth_date_edit import BirthDateEdit, to_qdate
from ponos_coach.ui.body_input import BodyProblem, body_problem_text
from ponos_coach.ui.form_rows import add_field_row
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
from ponos_coach.ui.measurement_texts import in_force_text, superseded_text
from ponos_coach.ui.measurements_form import (
    EditField,
    EditIssue,
    MeasurementsForm,
    MeasurementsRequest,
    NameProblem,
)
from ponos_coach.ui.settings_form import SettingsForm
from ponos_coach.ui.tokens import SPACE_1, SPACE_2, SPACE_4, Theme, tokens_for

_TABLE_MIN_HEIGHT = 140
_NOTE_FIELDS = (EditField.WEIGHT, EditField.MIN_BPM, EditField.MAX_BPM, EditField.THRESHOLD)


class ProfileEditDialog(SettingsForm):
    """Read-only fixed data, dated measurements to update and history."""

    save_requested = Signal(object)  # MeasurementsRequest

    def __init__(
        self,
        form: MeasurementsForm,
        history: MeasurementHistory,
        theme: Theme,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._form = form
        self._tokens = tokens_for(theme)
        self._show_errors = False
        self._errors = {field: self._error_label() for field in EditField}
        self._notes = {field: self._note_label() for field in _NOTE_FIELDS}
        self._validity_notice = self._note_label()
        self._history_model = MeasurementHistoryModel(self)
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
        self._valid_from.setMinimumDate(to_qdate(MIN_MEASUREMENT_DATE))
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
        self._set_tab_order()
        outer = QVBoxLayout(self)
        outer.setSpacing(SPACE_4)
        outer.addWidget(self._fixed_group())
        outer.addWidget(self._measurements_group())
        outer.addWidget(QLabel(self.tr("History"), self))
        outer.addWidget(self._table, stretch=1)
        outer.addWidget(self._errors[EditField.GENERAL])
        outer.addWidget(self._buttons)

    def _build_fixed_fields(self) -> None:
        self._name = QLineEdit(self)
        self._name.setObjectName("profileName")
        self._name.setAccessibleName(self.tr("Name"))
        self._name.setMaxLength(NAME_MAX_CHARS)
        self._name.textEdited.connect(self._on_name_edited)
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
        self._table.setShowGrid(False)  # no vertical or horizontal lines
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
        self._buttons.accepted.connect(self._on_save)

    def _set_tab_order(self) -> None:
        """Tab follows the visual order, top to bottom: first the fixed data to complete."""
        order = [
            self._name,
            self._height,
            self._birth,
            self._valid_from,
            self._weight,
            self._min,
            self._max,
            self._threshold,
            self._table,
            self._save,
        ]
        for first, second in pairwise(order):
            QWidget.setTabOrder(first, second)

    def _fixed_group(self) -> QGroupBox:
        box = QGroupBox(self.tr("Profile data"), self)
        layout = QFormLayout(box)
        layout.setVerticalSpacing(SPACE_2)
        add_field_row(
            layout, self.tr("Name"), self._name, self._with_error(self._name, EditField.NAME)
        )
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
            add_field_row(
                layout,
                self.tr("Height (cm)"),
                self._height,
                self._with_error(self._height, EditField.HEIGHT),
            )
            add_field_row(
                layout,
                self.tr("Date of birth"),
                self._birth,
                self._with_error(self._birth, EditField.BIRTH_DATE),
            )
        else:
            height = self.tr("{cm} cm").format(cm=fixed.height.cm)
            layout.addRow(self.tr("Height"), self._read_only(height, "fixedHeightValue"))
            born = QLocale().toString(
                to_qdate(fixed.birth_date.value), QLocale.FormatType.ShortFormat
            )
            layout.addRow(self.tr("Date of birth"), self._read_only(born, "fixedBirthDate"))
            self._height.hide()  # nothing to complete: the fields stay out of the form
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
        rows = (
            (self.tr("Valid from"), self._valid_from, EditField.VALID_FROM),
            (self.tr("Weight (kg)"), self._weight, EditField.WEIGHT),
            (self.tr("Resting heart rate"), self._min, EditField.MIN_BPM),
            (self.tr("Maximum heart rate"), self._max, EditField.MAX_BPM),
            (self.tr("Threshold heart rate"), self._threshold, EditField.THRESHOLD),
        )
        for text, widget, field in rows:
            add_field_row(layout, text, widget, self._with_error(widget, field))
        layout.addRow(self._validity_notice)
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
        label.setStyleSheet(f"color: {self._tokens.error};")
        label.setVisible(False)
        return label

    def _note_label(self) -> QLabel:
        """Explanation row under a field (muted text, with sufficient contrast)."""
        label = QLabel(self)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setWordWrap(True)
        label.setStyleSheet(f"color: {self._tokens.text_muted};")
        label.setVisible(False)
        return label

    def _with_error(self, widget: QWidget, field: EditField) -> QWidget:
        box = QWidget(self)
        column = QVBoxLayout(box)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(SPACE_1)
        column.addWidget(widget)
        note = self._notes.get(field)
        if note is not None:
            column.addWidget(note)
        column.addWidget(self._errors[field])
        return box

    # --- Test accessors ---------------------------------------------------------

    @property
    def form(self) -> MeasurementsForm:
        """The form model."""
        return self._form

    @property
    def valid_from_edit(self) -> QDateEdit:
        """Validity day picker."""
        return self._valid_from

    @property
    def name_edit(self) -> QLineEdit:
        """Campo del nome del profilo."""
        return self._name

    @property
    def weight_edit(self) -> QLineEdit:
        """Weight field."""
        return self._weight

    @property
    def min_box(self) -> QSpinBox:
        """Minimum HR."""
        return self._min

    @property
    def max_box(self) -> QSpinBox:
        """Maximum HR."""
        return self._max

    @property
    def threshold_box(self) -> QSpinBox:
        """Threshold."""
        return self._threshold

    @property
    def height_edit(self) -> QLineEdit:
        """Height to complete (hidden if the profile already has the fixed data)."""
        return self._height

    @property
    def birth_edit(self) -> BirthDateEdit:
        """Birth date to complete (hidden if the profile already has the fixed data)."""
        return self._birth

    @property
    def save_button(self) -> QWidget:
        """Button "Save changes"."""
        return self._save

    @property
    def history_table(self) -> QTableView:
        """The history table."""
        return self._table

    def error_text(self, field: EditField) -> str:
        """Message shown under a field (empty if there is none)."""
        return self._errors[field].text()

    def note_text(self, field: EditField) -> str:
        """What applied that day for a changed field (empty if the field is unchanged)."""
        label = self._notes[field]
        return label.text() if label.isVisibleTo(self) else ""

    @property
    def validity_notice_text(self) -> str:
        """Notice that a later measurement replaces the new one (empty if there is none)."""
        label = self._validity_notice
        return label.text() if label.isVisibleTo(self) else ""

    def has_unsaved_changes(self) -> bool:
        """True while something typed differs from what the profile holds."""
        return self._form.has_changes

    def request_save(self) -> None:
        """Save what is typed (the "Save changes" button)."""
        self._on_save()

    # --- From the model to the view -------------------------------------------------------

    def set_history(self, history: MeasurementHistory) -> None:
        """Show the measurement history."""
        self._history_model.set_history(history)

    def _load_from_form(self) -> None:
        self._valid_from.blockSignals(True)
        self._valid_from.setDate(to_qdate(self._form.valid_from))
        self._valid_from.blockSignals(False)
        self._name.setText(self._form.current_name)
        self._refresh_values()

    def _refresh_values(self) -> None:
        """Show the model's values: untouched fields follow the chosen day."""
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
        if isinstance(kind, NameProblem):
            return self._name_problem_text(kind)
        texts = {
            MeasurementProblem.NOTHING_TO_UPDATE: self.tr(
                "Nothing has changed: change at least one value."
            ),
            MeasurementProblem.DATE_IN_FUTURE: self.tr("The date cannot be in the future."),
            MeasurementProblem.DATE_TOO_OLD: self.tr("The date cannot be before {first}.").format(
                first=MIN_MEASUREMENT_DATE.isoformat()
            ),
            MeasurementProblem.INVALID_HEART_RATE: self.tr(
                "The heart-rate values do not fit together: the resting rate must be below the "
                "threshold, and the threshold not above the maximum."
            ),
        }
        return texts[kind]

    def _name_problem_text(self, problem: NameProblem) -> str:
        if problem is NameProblem.EMPTY:
            return self.tr("Enter a name for the profile.")
        return self.tr("The name contains characters that are not allowed.")

    def _update_save_button(self) -> None:
        """ "Save changes" stays disabled, with the reason in the tooltip, without changes."""
        has_changes = self._form.has_changes
        self._save.setEnabled(has_changes)
        self._save.setToolTip("" if has_changes else self.tr("Change at least one value first."))
        self.edited.emit()

    # --- From the view to the model -------------------------------------------------------

    def _after_edit(self) -> None:
        self._update_save_button()
        self._refresh_notes()
        self._refresh_errors()

    def _refresh_notes(self) -> None:
        """What changes relative to the chosen day and for how long it would apply: recomputed at
        every edit
        and every day change, so the notes never belong to another day."""
        changes = {change.field: change for change in self._form.changes()}
        for field, label in self._notes.items():
            change = changes.get(field)
            label.setText("" if change is None else in_force_text(change))
            label.setVisible(change is not None)
        lines = [
            superseded_text(pending.series, pending.period.valid_to)
            for pending in self._form.pending_periods()
            if pending.period.valid_to is not None
        ]
        self._validity_notice.setText("\n".join(lines))
        self._validity_notice.setVisible(bool(lines))

    def _on_valid_from_changed(self, value: QDate) -> None:
        self._form.set_valid_from(date(value.year(), value.month(), value.day()))
        self._refresh_values()
        self._after_edit()

    def _on_name_edited(self, text: str) -> None:
        self._form.edit_name(text)
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
        self.save_failed.emit()

    def _focus_first(self, field: EditField) -> None:
        targets: dict[EditField, QWidget] = {
            EditField.NAME: self._name,
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

    # --- Save outcome --------------------------------------------------------

    def set_busy(self, busy: bool) -> None:
        """While saving is in progress the page accepts no other edits."""
        self._save.setEnabled(not busy and self._form.has_changes)
        for widget in (
            self._name,
            self._valid_from,
            self._weight,
            self._min,
            self._max,
            self._threshold,
            self._height,
            self._birth,
        ):
            widget.setEnabled(not busy)
        if not busy:
            self.save_failed.emit()

    def show_rejection(self, issue: EditIssue) -> None:
        """Measurements rejected by the use case: the problem appears on the right field."""
        self.set_busy(False)
        self._show_errors = True
        self._refresh_errors((issue,))
        self._focus_first(issue.field)
