# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Dialogo "Nuovo profilo": vista umile sul modello `NewProfileForm`.

Il primo errore compare all'invio, poi i messaggi si aggiornano mentre si scrive. La cartella
si esamina nel thread dei dati (mai nel thread della UI) e l'esito arriva con `show_folder_check`.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
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
from ponos_coach.ui.new_profile_form import (
    MAX_ALLOWED_BPM,
    MIN_ALLOWED_BPM,
    MIN_THRESHOLD_BPM,
    Field,
    NewProfileForm,
    Problem,
)
from ponos_coach.ui.tokens import DIALOG_MIN_WIDTH, SPACE_1, SPACE_2, SPACE_4, Theme, tokens_for
from ponos_coach.ui.workspace import FolderCheck


class NewProfileDialog(QDialog):
    """Raccoglie nome, cartella, FC e sesso, e emette la richiesta di creazione."""

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

        self._min = self._bpm_box(MIN_ALLOWED_BPM, "minHeartRate")
        self._max = self._bpm_box(MIN_ALLOWED_BPM, "maxHeartRate")
        self._threshold = self._bpm_box(MIN_THRESHOLD_BPM, "thresholdHeartRate")
        self._min.valueChanged.connect(self._on_min_changed)
        self._max.valueChanged.connect(self._on_max_changed)
        self._threshold.valueChanged.connect(self._on_threshold_changed)
        self._threshold_note = self._note_label()

        self._male = QRadioButton(self.tr("Male"), self)
        self._female = QRadioButton(self.tr("Female"), self)
        self._male.toggled.connect(self._on_sex_changed)

        self._errors = {field: self._error_label() for field in Field}

    def _build_layout(self) -> None:
        layout = QFormLayout()
        layout.setVerticalSpacing(SPACE_2)
        layout.addRow(self.tr("Profile name"), self._with_error(self._name, Field.NAME))
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
        layout.addRow(self.tr("Profile folder"), folder_box)
        layout.addRow(self.tr("Resting heart rate"), self._with_error(self._min, Field.MIN_BPM))
        layout.addRow(self.tr("Maximum heart rate"), self._with_error(self._max, Field.MAX_BPM))
        threshold_box = QWidget(self)
        threshold_column = QVBoxLayout(threshold_box)
        threshold_column.setContentsMargins(0, 0, 0, 0)
        threshold_column.setSpacing(SPACE_1)
        threshold_column.addWidget(self._threshold)
        threshold_column.addWidget(self._threshold_note)
        threshold_column.addWidget(self._errors[Field.THRESHOLD])
        layout.addRow(self.tr("Threshold heart rate"), threshold_box)
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
        self._buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        self._buttons.accepted.connect(self._on_create)
        self._buttons.rejected.connect(self.reject)

        outer = QVBoxLayout(self)
        outer.setSpacing(SPACE_4)
        outer.addLayout(layout)
        outer.addWidget(self._buttons)

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

    # --- Accessori per i test ---------------------------------------------------------

    @property
    def form(self) -> NewProfileForm:
        """Il modello del modulo."""
        return self._form

    @property
    def name_edit(self) -> QLineEdit:
        """Campo del nome."""
        return self._name

    @property
    def folder_edit(self) -> QLineEdit:
        """Campo della cartella."""
        return self._folder

    @property
    def browse_button(self) -> QPushButton:
        """Bottone "Sfoglia..."."""
        return self._browse

    @property
    def destination_text(self) -> str:
        """Riga "I dati andranno in: ..." (vuota se non è mostrata)."""
        return self._destination_note.text() if self._destination_note.isVisible() else ""

    @property
    def create_button(self) -> QPushButton:
        """Bottone "Crea profilo"."""
        return self._create

    @property
    def threshold_note_text(self) -> str:
        """Nota sulla soglia stimata (vuota se la soglia è stata fissata)."""
        return self._threshold_note.text()

    def error_text(self, field: Field) -> str:
        """Messaggio d'errore mostrato sotto un campo (vuoto se non c'è)."""
        return self._errors[field].text()

    # --- Dal modello alla vista -------------------------------------------------------

    def _load_from_form(self) -> None:
        form = self._form
        self._name.setText(form.name)
        self._folder.setText(form.folder_text)
        self._refresh_destination()
        self._min.setValue(form.min_bpm)
        self._max.setValue(form.max_bpm)
        self._sync_threshold()
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

    def _refresh_errors(self) -> None:
        problems = self._form.problems() if self._show_errors else ()
        for field, label in self._errors.items():
            first = next((p for p in problems if p.field is field), None)
            label.setText("" if first is None else self._problem_text(first))
            label.setVisible(first is not None)

    def _problem_text(self, problem: Problem) -> str:
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
            Problem.MIN_NOT_BELOW_MAX: self.tr(
                "The resting heart rate must be lower than the maximum."
            ),
            Problem.THRESHOLD_NOT_ABOVE_MIN: self.tr(
                "The threshold must be higher than the resting heart rate."
            ),
            Problem.THRESHOLD_ABOVE_MAX: self.tr(
                "The threshold cannot be higher than the maximum."
            ),
        }
        return texts[problem]

    # --- Dalla vista al modello -------------------------------------------------------

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
        """Dice dove andranno i dati quando la cartella scelta contiene già altro."""
        destination = self._form.destination if self._form.uses_subfolder else ""
        self._destination_note.setText(
            self.tr("Data will go in: {folder}").format(folder=destination)
        )
        self._destination_note.setVisible(bool(destination))

    def _update_create_button(self) -> None:
        """ "Crea profilo" resta disabilitato, con il motivo nel tooltip, senza cartella valida."""
        problem = self._form.folder_problem()
        self._create.setEnabled(problem is None)
        self._create.setToolTip("" if problem is None else self._problem_text(problem))

    def _on_min_changed(self, value: int) -> None:
        self._form.min_bpm = value
        self._refresh_errors()

    def _on_max_changed(self, value: int) -> None:
        self._form.max_bpm = value
        self._sync_threshold()
        self._refresh_errors()

    def _on_threshold_changed(self, value: int) -> None:
        self._form.set_threshold(value)
        self._sync_threshold()
        self._refresh_errors()

    def _on_sex_changed(self, male: bool) -> None:
        self._form.sex = Sex.MALE if male else Sex.FEMALE

    def _request_inspection(self) -> None:
        text = self._form.folder_text.strip()
        if text and Path(text).is_absolute():
            self._inspect_folder(Path(text))

    def show_folder_check(self, check: FolderCheck) -> None:
        """Esito dell'esame di una cartella; si ignora se non è più quella scritta."""
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
            Field.MIN_BPM: self._min,
            Field.MAX_BPM: self._max,
            Field.THRESHOLD: self._threshold,
        }[field]
        target.setFocus()
