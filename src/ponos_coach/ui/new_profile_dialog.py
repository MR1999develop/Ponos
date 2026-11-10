# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Dialog "New profile": humble view on the `NewProfileForm` model.

The first error appears on submit, then messages update while typing. The folder
is examined on the data thread (never on the UI thread) and the outcome arrives via
`show_folder_check`.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from itertools import pairwise
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ponos_coach.load.domain import Sex
from ponos_coach.profiles.domain import FolderKind
from ponos_coach.profiles.use_cases import NewProfileRequest
from ponos_coach.ui.birth_date_edit import BirthDateEdit
from ponos_coach.ui.body_input import body_problem_text
from ponos_coach.ui.form_rows import add_field_row, optional_threshold_edit, threshold_pace_edit
from ponos_coach.ui.heart_rate_rules import (
    MAX_ALLOWED_BPM,
    MIN_ALLOWED_BPM,
    MIN_THRESHOLD_BPM,
    bike_default_placeholder,
    heart_rate_problem_text,
    other_default_placeholder,
)
from ponos_coach.ui.new_profile_form import (
    Field,
    NewProfileForm,
    Problem,
    body_problem_of,
    heart_rate_problem_of,
)
from ponos_coach.ui.tokens import DIALOG_MIN_WIDTH, SPACE_1, SPACE_2, SPACE_4, Theme, tokens_for
from ponos_coach.ui.workspace import FolderCheck


class NewProfileDialog(QDialog):
    """Collect name, folder, body data, HR and sex, and emit the creation request."""

    create_requested = Signal(object)  # NewProfileRequest

    def __init__(
        self,
        form: NewProfileForm,
        today: date,
        inspect_folder: Callable[[Path], None],
        choose_folder: Callable[[], str],
        theme: Theme,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._form = form
        self._today = today
        self._inspect_folder = inspect_folder
        self._choose_folder = choose_folder
        self._error_color = tokens_for(theme).error
        self._show_errors = False
        self.setWindowTitle(self.tr("New profile"))
        self.setMinimumWidth(DIALOG_MIN_WIDTH)
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self._build()
        self._load_from_form()

    # --- Costruzione ------------------------------------------------------------------

    def _build(self) -> None:
        self._build_fields()
        self._build_layout()

    def _build_fields(self) -> None:
        self._name = QLineEdit(self)
        self._name.setObjectName("profileName")
        self._name.setPlaceholderText(self.tr("For example, your name"))
        self._name.textEdited.connect(self._on_name_edited)
        self._name.editingFinished.connect(self._request_inspection)

        self._folder = QLineEdit(self)
        self._folder.setObjectName("profileFolder")
        self._folder.setPlaceholderText(self.tr("Choose where to save the profile"))
        self._folder.textEdited.connect(self._on_folder_edited)
        self._folder.editingFinished.connect(self._request_inspection)
        self._browse = QPushButton(self.tr("Browse..."), self)
        self._browse.clicked.connect(self._on_browse)
        self._folder_note = self._note_label()
        self._destination_note = self._note_label()

        self._height = self._text_field("profileHeight", self.tr("Height (cm)"))
        self._height.setPlaceholderText(self.tr("For example, 175"))
        self._height.textEdited.connect(self._on_height_edited)
        self._weight = self._text_field("profileWeight", self.tr("Weight (kg)"))
        self._weight.setPlaceholderText(self.tr("For example, 68.5"))
        self._weight.textEdited.connect(self._on_weight_edited)
        self._birth = BirthDateEdit(self._today, self)
        self._birth.selection_changed.connect(self._on_birth_changed)

        self._min = self._bpm_box(MIN_ALLOWED_BPM, "minHeartRate")
        self._max = self._bpm_box(MIN_ALLOWED_BPM, "maxHeartRate")
        self._threshold = self._bpm_box(MIN_THRESHOLD_BPM, "thresholdHeartRate")
        self._min.valueChanged.connect(self._on_min_changed)
        self._max.valueChanged.connect(self._on_max_changed)
        self._threshold.valueChanged.connect(self._on_threshold_changed)
        self._threshold_note = self._note_label()
        self._bike = optional_threshold_edit(self, "bikeThresholdHeartRate")
        self._bike.textEdited.connect(self._on_bike_edited)
        self._other = optional_threshold_edit(self, "otherThresholdHeartRate")
        self._other.textEdited.connect(self._on_other_edited)
        self._pace = threshold_pace_edit(
            self, "thresholdPace", self.tr("Threshold pace (min/km)"), self.tr("m:ss")
        )
        self._pace.textEdited.connect(self._on_pace_edited)

        self._male = QRadioButton(self.tr("Male"), self)
        self._female = QRadioButton(self.tr("Female"), self)
        self._male.toggled.connect(self._on_sex_changed)

        self._errors = {field: self._error_label() for field in Field}

    def _build_layout(self) -> None:
        layout = QFormLayout()
        layout.setVerticalSpacing(SPACE_2)
        add_field_row(
            layout, self.tr("Profile name"), self._name, self._with_error(self._name, Field.NAME)
        )
        folder_row = QHBoxLayout()
        folder_row.addWidget(self._folder, stretch=1)
        folder_row.addWidget(self._browse)
        folder_box = QWidget(self)
        folder_column = QVBoxLayout(folder_box)
        folder_column.setContentsMargins(0, 0, 0, 0)
        folder_column.setSpacing(SPACE_1)
        folder_column.addLayout(folder_row)
        folder_column.addWidget(self._destination_note)
        folder_column.addWidget(self._folder_note)
        folder_column.addWidget(self._errors[Field.FOLDER])
        add_field_row(layout, self.tr("Profile folder"), self._folder, folder_box)
        rows = (
            (self.tr("Height (cm)"), self._height, Field.HEIGHT),
            (self.tr("Weight (kg)"), self._weight, Field.WEIGHT),
            (self.tr("Date of birth"), self._birth, Field.BIRTH_DATE),
            (self.tr("Resting heart rate"), self._min, Field.MIN_BPM),
            (self.tr("Maximum heart rate"), self._max, Field.MAX_BPM),
        )
        for text, widget, field in rows:
            add_field_row(layout, text, widget, self._with_error(widget, field))
        layout.addRow(self._thresholds_group())
        sex_box = QWidget(self)
        sex_row = QHBoxLayout(sex_box)
        sex_row.setContentsMargins(0, 0, 0, 0)
        sex_row.addWidget(self._male)
        sex_row.addWidget(self._female)
        sex_row.addStretch(1)
        layout.addRow(self.tr("Sex (for the load formula)"), sex_box)
        zone = QLabel(self._form.timezone_name, self)
        zone.setTextFormat(Qt.TextFormat.PlainText)
        layout.addRow(self.tr("Time zone"), zone)

        self._buttons = QDialogButtonBox(self)
        self._create = self._buttons.addButton(
            self.tr("Create profile"), QDialogButtonBox.ButtonRole.AcceptRole
        )
        self._create.setDefault(True)
        self._update_create_button()
        self._cancel = self._buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        self._buttons.accepted.connect(self._on_create)
        self._buttons.rejected.connect(self.reject)

        outer = QVBoxLayout(self)
        outer.setSpacing(SPACE_4)
        outer.addLayout(layout)
        outer.addWidget(self._buttons)
        self._set_tab_order()

    def _thresholds_group(self) -> QGroupBox:
        """Running threshold plus the optional ones, together: the note says they can wait."""
        group = QGroupBox(self.tr("Thresholds"), self)
        form = QFormLayout(group)
        form.setVerticalSpacing(SPACE_2)
        threshold_box = QWidget(group)
        threshold_column = QVBoxLayout(threshold_box)
        threshold_column.setContentsMargins(0, 0, 0, 0)
        threshold_column.setSpacing(SPACE_1)
        threshold_column.addWidget(self._threshold)
        threshold_column.addWidget(self._threshold_note)
        threshold_column.addWidget(self._errors[Field.THRESHOLD])
        add_field_row(form, self.tr("Threshold heart rate"), self._threshold, threshold_box)
        rows = (
            (self.tr("Cycling threshold heart rate"), self._bike, Field.BIKE_THRESHOLD),
            (self.tr("Other activities threshold heart rate"), self._other, Field.OTHER_THRESHOLD),
            (self.tr("Threshold pace (min/km)"), self._pace, Field.THRESHOLD_PACE),
        )
        for text, widget, field in rows:
            add_field_row(form, text, widget, self._with_error(widget, field))
        optional = self._note_label()
        optional.setText(
            self.tr(
                "Cycling, other activities and pace are optional: leave them empty and change "
                "them later in Edit profile."
            )
        )
        optional.setVisible(True)
        form.addRow(optional)
        return group

    def _set_tab_order(self) -> None:
        """Tab follows the visual order, top to bottom."""
        order: list[QWidget] = [
            self._name,
            self._folder,
            self._browse,
            self._height,
            self._weight,
            self._birth,
            self._min,
            self._max,
            self._threshold,
            self._bike,
            self._other,
            self._pace,
            self._male,
            self._female,
            self._create,
            self._cancel,
        ]
        for first, second in pairwise(order):
            QWidget.setTabOrder(first, second)

    def _text_field(self, name: str, accessible_name: str) -> QLineEdit:
        field = QLineEdit(self)
        field.setObjectName(name)
        field.setAccessibleName(accessible_name)
        return field

    def _bpm_box(self, low: int, name: str) -> QSpinBox:
        box = QSpinBox(self)
        box.setObjectName(name)
        box.setRange(low, MAX_ALLOWED_BPM)
        box.setSuffix(self.tr(" bpm"))
        return box

    def _note_label(self) -> QLabel:
        label = QLabel(self)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setWordWrap(True)
        label.setVisible(False)
        return label

    def _error_label(self) -> QLabel:
        label = QLabel(self)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setWordWrap(True)
        label.setStyleSheet(f"color: {self._error_color};")
        label.setVisible(False)
        return label

    def _with_error(self, widget: QWidget, field: Field) -> QWidget:
        box = QWidget(self)
        column = QVBoxLayout(box)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(SPACE_1)
        column.addWidget(widget)
        column.addWidget(self._errors[field])
        return box

    # --- Test accessors ---------------------------------------------------------

    @property
    def form(self) -> NewProfileForm:
        """The form model."""
        return self._form

    @property
    def name_edit(self) -> QLineEdit:
        """Name field."""
        return self._name

    @property
    def folder_edit(self) -> QLineEdit:
        """Folder field."""
        return self._folder

    @property
    def browse_button(self) -> QPushButton:
        """Button "Browse..."."""
        return self._browse

    @property
    def height_edit(self) -> QLineEdit:
        """Height field."""
        return self._height

    @property
    def weight_edit(self) -> QLineEdit:
        """Weight field."""
        return self._weight

    @property
    def birth_edit(self) -> BirthDateEdit:
        """Birth date picker."""
        return self._birth

    @property
    def threshold_spin(self) -> QSpinBox:
        """Running threshold heart rate."""
        return self._threshold

    @property
    def bike_edit(self) -> QLineEdit:
        """Cycling threshold (empty: the default, shown as the placeholder)."""
        return self._bike

    @property
    def other_edit(self) -> QLineEdit:
        """Threshold of the other activities (empty: the default, shown as the placeholder)."""
        return self._other

    @property
    def pace_edit(self) -> QLineEdit:
        """Threshold pace field (`m:ss`; empty: no pace)."""
        return self._pace

    @property
    def destination_text(self) -> str:
        """Row "Data will go in: ..." (empty if not shown)."""
        return self._destination_note.text() if self._destination_note.isVisible() else ""

    @property
    def create_button(self) -> QPushButton:
        """Button "Create profile"."""
        return self._create

    @property
    def threshold_note_text(self) -> str:
        """Note on the estimated threshold (empty if the threshold was set)."""
        return self._threshold_note.text()

    def error_text(self, field: Field) -> str:
        """Error message shown under a field (empty if there is none)."""
        return self._errors[field].text()

    # --- From the model to the view -------------------------------------------------------

    def _load_from_form(self) -> None:
        form = self._form
        self._name.setText(form.name)
        self._folder.setText(form.folder_text)
        self._refresh_destination()
        self._height.setText(form.height_text)
        self._weight.setText(form.weight_text)
        self._birth.select(form.birth_date)
        self._min.setValue(form.min_bpm)
        self._max.setValue(form.max_bpm)
        self._sync_threshold()
        self._bike.setText(form.bike_text)
        self._other.setText(form.other_text)
        self._pace.setText(form.pace_text)
        (self._male if form.sex is Sex.MALE else self._female).setChecked(True)

    def _sync_threshold(self) -> None:
        self._threshold.blockSignals(True)
        self._threshold.setValue(self._form.threshold_bpm)
        self._threshold.blockSignals(False)
        if self._form.threshold_is_estimate:
            self._threshold_note.setText(
                self.tr(
                    "Estimated at 90% of your maximum. Confirm it with the threshold test "
                    "or change it if you know it."
                )
            )
        else:
            self._threshold_note.setText("")
        self._threshold_note.setVisible(self._form.threshold_is_estimate)
        self._refresh_placeholders()

    def _refresh_placeholders(self) -> None:
        """The default of each optional threshold, for the heart rates now entered."""
        form = self._form
        self._bike.setPlaceholderText(
            bike_default_placeholder(form.threshold_default(Field.BIKE_THRESHOLD))
        )
        self._other.setPlaceholderText(
            other_default_placeholder(form.threshold_default(Field.OTHER_THRESHOLD))
        )

    def _refresh_errors(self) -> None:
        problems = self._form.problems(self._today) if self._show_errors else ()
        for field, label in self._errors.items():
            first = next((p for p in problems if p.field is field), None)
            label.setText("" if first is None else self._problem_text(first))
            label.setVisible(first is not None)

    def _problem_text(self, problem: Problem) -> str:
        body = body_problem_of(problem)
        if body is not None:
            return body_problem_text(body)
        rule = heart_rate_problem_of(problem)
        if rule is not None:
            return heart_rate_problem_text(rule)
        texts = {
            Problem.NAME_EMPTY: self.tr("Enter a name for the profile."),
            Problem.NAME_TOO_LONG: self.tr("The name is too long: use at most 60 characters."),
            Problem.NAME_INVALID: self.tr("The name contains characters that are not allowed."),
            Problem.FOLDER_EMPTY: self.tr(
                "Choose the folder where the profile data will be saved."
            ),
            Problem.FOLDER_NOT_ABSOLUTE: self.tr(
                "Enter the full path of the folder, starting from the drive or root."
            ),
        }
        return texts[problem]

    # --- From the view to the model -------------------------------------------------------

    def _on_name_edited(self, text: str) -> None:
        self._form.name = text
        self._refresh_destination()
        self._refresh_errors()

    def _on_folder_edited(self, text: str) -> None:
        self._form.choose_folder(text)
        self._folder_note.setVisible(False)
        self._refresh_destination()
        self._update_create_button()
        self._refresh_errors()

    def _on_browse(self) -> None:
        chosen = self._choose_folder()
        if chosen:
            self._folder.setText(chosen)
            self._on_folder_edited(chosen)
            self._request_inspection()

    def _refresh_destination(self) -> None:
        """Say where the data will go when the chosen folder already contains something else."""
        destination = self._form.destination if self._form.uses_subfolder else ""
        self._destination_note.setText(
            self.tr("Data will go in: {folder}").format(folder=destination)
        )
        self._destination_note.setVisible(bool(destination))

    def _update_create_button(self) -> None:
        """ "Create profile" stays disabled, with the reason in the tooltip, without a valid
        folder."""
        problem = self._form.folder_problem()
        self._create.setEnabled(problem is None)
        self._create.setToolTip("" if problem is None else self._problem_text(problem))

    def _on_height_edited(self, text: str) -> None:
        self._form.height_text = text
        self._refresh_errors()

    def _on_weight_edited(self, text: str) -> None:
        self._form.weight_text = text
        self._refresh_errors()

    def _on_birth_changed(self, value: object) -> None:
        self._form.birth_date = value if isinstance(value, date) else None
        self._refresh_errors()

    def _on_min_changed(self, value: int) -> None:
        self._form.min_bpm = value
        self._refresh_placeholders()
        self._refresh_errors()

    def _on_max_changed(self, value: int) -> None:
        self._form.max_bpm = value
        self._sync_threshold()
        self._refresh_errors()

    def _on_threshold_changed(self, value: int) -> None:
        self._form.set_threshold(value)
        self._sync_threshold()
        self._refresh_errors()

    def _on_bike_edited(self, text: str) -> None:
        self._form.bike_text = text
        self._refresh_errors()

    def _on_other_edited(self, text: str) -> None:
        self._form.other_text = text
        self._refresh_errors()

    def _on_pace_edited(self, text: str) -> None:
        self._form.pace_text = text
        self._refresh_errors()

    def _on_sex_changed(self, male: bool) -> None:
        self._form.sex = Sex.MALE if male else Sex.FEMALE

    def _request_inspection(self) -> None:
        text = self._form.folder_text.strip()
        if text and Path(text).is_absolute():
            self._inspect_folder(Path(text))

    def show_folder_check(self, check: FolderCheck) -> None:
        """Outcome of examining a folder; ignored if it is no longer the one typed."""
        if str(check.folder) != self._form.folder_text.strip():
            return
        if check.inspection is None:
            self._form.folder_state = None
            self._refresh_destination()
            self._folder_note.setText(
                self.tr("This folder could not be examined. Check that you can read and write it.")
            )
            self._folder_note.setVisible(True)
            return
        self._form.folder_state = check.inspection.state
        notes = []
        if check.inspection.kind is FolderKind.NETWORK:
            notes.append(
                self.tr("This is a network folder: the database may be slow or unreliable.")
            )
        elif check.inspection.kind is FolderKind.CLOUD_SYNCED:
            notes.append(
                self.tr("This folder is synchronised online: the database may become damaged.")
            )
        self._folder_note.setText(" ".join(notes))
        self._folder_note.setVisible(bool(notes))
        self._refresh_destination()
        self._refresh_errors()

    def _on_create(self) -> None:
        self._show_errors = True
        self._refresh_errors()
        result = self._form.build_request(self._today)
        if isinstance(result, NewProfileRequest):
            self.create_requested.emit(result)
            self.accept()
            return
        self._focus_first(result[0].field)

    def _focus_first(self, field: Field) -> None:
        target: QWidget = {
            Field.NAME: self._name,
            Field.FOLDER: self._folder,
            Field.HEIGHT: self._height,
            Field.WEIGHT: self._weight,
            Field.BIRTH_DATE: self._birth,
            Field.MIN_BPM: self._min,
            Field.MAX_BPM: self._max,
            Field.THRESHOLD: self._threshold,
            Field.BIKE_THRESHOLD: self._bike,
            Field.OTHER_THRESHOLD: self._other,
            Field.THRESHOLD_PACE: self._pace,
        }[field]
        target.setFocus()
