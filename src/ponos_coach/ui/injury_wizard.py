# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""The questionnaire that opens an injury, as a guided wizard of four short pages.

Where and since when, the pain, the warning signs, and the doctor's notes. Each page asks one kind
of thing; the answers are kept in a `QuestionnaireDraft` that the use case turns into an `Injury`
(the domain checks it again). The wizard edits a copy: "Cancel" changes nothing. The day the pain
began cannot be after today (the control stops there), the pain answers cannot leave the 0 to 10
scale, and a warning sign (or strong rest pain) shows the invitation to a doctor at once, without
blocking the report. The doctor's notes are free text that stays on this computer. The last button
says what it does. Health data: never in logs or error messages.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date

from PySide6.QtCore import QLocale, Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDateEdit,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QRadioButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
    QWizard,
    QWizardPage,
)

from ponos_coach.injury.domain import BodyZone, Onset, PainFreeActivity, Side
from ponos_coach.ui.birth_date_edit import from_qdate, to_qdate
from ponos_coach.ui.form_rows import add_field_row
from ponos_coach.ui.injury_dialogs import SignsGroup, pain_scale_hint, pain_spin
from ponos_coach.ui.injury_draft import (
    MAX_NOTES,
    QuestionnaireDraft,
    alarms_of,
    notes_problem,
)
from ponos_coach.ui.injury_texts import (
    onset_text,
    pain_free_activity_text,
    side_choice_text,
    zone_text,
)
from ponos_coach.ui.tokens import DIALOG_MIN_WIDTH, SPACE_2, SPACE_4

_FIRST_DAY = date(1900, 1, 1)


def _label(text: str, parent: QWidget, name: str = "") -> QLabel:
    label = QLabel(text, parent)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    if name:
        label.setObjectName(name)
    return label


class _WherePage(QWizardPage):
    """Where it hurts, which side, when it began and how."""

    def __init__(self, today: date, onset_day: date | None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setTitle(self.tr("Where and since when"))
        self.setSubTitle(self.tr("Say where it hurts, in your own words. This is not a diagnosis."))
        self.zone = QComboBox(self)
        self.zone.setObjectName("injuryZone")
        for zone in BodyZone:
            self.zone.addItem(zone_text(zone), zone)
        self.side_radios: dict[Side, QRadioButton] = {}
        side_group = QButtonGroup(self)
        sides = QHBoxLayout()
        for side in Side:
            radio = QRadioButton(side_choice_text(side), self)
            radio.setObjectName(f"injurySide_{side.value}")
            side_group.addButton(radio)
            sides.addWidget(radio)
            self.side_radios[side] = radio
        self.side_radios[Side.LEFT].setChecked(True)
        self.onset_radios: dict[Onset, QRadioButton] = {}
        onset_group = QButtonGroup(self)
        onsets = QHBoxLayout()
        for onset in Onset:
            radio = QRadioButton(onset_text(onset), self)
            radio.setObjectName(f"injuryOnset_{onset.value}")
            onset_group.addButton(radio)
            onsets.addWidget(radio)
            self.onset_radios[onset] = radio
        self.onset_radios[Onset.GRADUAL].setChecked(True)
        self.day = QDateEdit(self)
        self.day.setObjectName("injuryOnsetDay")
        self.day.setCalendarPopup(True)
        self.day.setDisplayFormat(QLocale().dateFormat(QLocale.FormatType.ShortFormat))
        self.day.setDateRange(to_qdate(_FIRST_DAY), to_qdate(today))  # never after today
        self.day.setDate(to_qdate(min(onset_day or today, today)))
        form = QFormLayout()
        form.setVerticalSpacing(SPACE_2)
        add_field_row(form, self.tr("Where does it hurt?"), self.zone)
        side_holder = QWidget(self)
        side_holder.setLayout(sides)
        add_field_row(form, self.tr("Which side?"), side_holder, side_holder)
        add_field_row(form, self.tr("Since which day?"), self.day)
        onset_holder = QWidget(self)
        onset_holder.setLayout(onsets)
        add_field_row(form, self.tr("How did it begin?"), onset_holder, onset_holder)
        self.setLayout(form)

    def side(self) -> Side:
        """The side chosen."""
        return next(side for side, radio in self.side_radios.items() if radio.isChecked())

    def onset(self) -> Onset:
        """How it began."""
        return next(onset for onset, radio in self.onset_radios.items() if radio.isChecked())


class _PainPage(QWizardPage):
    """The pain in four moments, and what does not hurt."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setTitle(self.tr("Pain and what does not hurt"))
        self.rest = pain_spin(self, "painRest", self.tr("Pain at rest"))
        self.walking = pain_spin(self, "painWalking", self.tr("Pain when walking"))
        self.running = pain_spin(
            self,
            "painRunning",
            self.tr("Pain when running"),
            self.tr("I cannot run, or I have not tried"),
        )
        self.morning = pain_spin(self, "painMorning", self.tr("Pain the morning after"))
        form = QFormLayout()
        form.setVerticalSpacing(SPACE_2)
        add_field_row(form, self.tr("Pain at rest"), self.rest)
        add_field_row(form, self.tr("Pain when walking"), self.walking)
        add_field_row(form, self.tr("Pain when running"), self.running)
        add_field_row(form, self.tr("Pain the morning after"), self.morning)
        self.pain_free_checks: dict[PainFreeActivity, QCheckBox] = {}
        free = QVBoxLayout()
        for activity in PainFreeActivity:
            check = QCheckBox(pain_free_activity_text(activity), self)
            check.setObjectName(f"painFree_{activity.value}")
            free.addWidget(check)
            self.pain_free_checks[activity] = check
        column = QVBoxLayout(self)
        column.setSpacing(SPACE_4)
        column.addWidget(_label(pain_scale_hint(), self))
        column.addLayout(form)
        column.addWidget(_label(self.tr("Which of these do you do without any pain?"), self))
        column.addLayout(free)

    def pain_free(self) -> frozenset[PainFreeActivity]:
        """The activities ticked."""
        return frozenset(a for a, check in self.pain_free_checks.items() if check.isChecked())


class _SignsPage(QWizardPage):
    """The warning signs."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setTitle(self.tr("Warning signs"))
        self.setSubTitle(
            self.tr("Tick any that apply. They do not stop you from reporting the injury.")
        )
        self.group = SignsGroup(self)
        column = QVBoxLayout(self)
        column.addWidget(self.group)


class _NotesPage(QWizardPage):
    """The doctor's notes, free text that stays on this computer."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setTitle(self.tr("Notes from your doctor"))
        self.setSubTitle(
            self.tr("Optional. This text stays on this computer and the app never reads it.")
        )
        self.notes = QPlainTextEdit(self)
        self.notes.setObjectName("injuryNotes")
        self.notes.setAccessibleName(self.tr("Notes from your doctor"))
        self.notes.textChanged.connect(self._on_changed)
        self.count = _label("", self, "injuryNotesCount")
        self.problem = _label("", self, "injuryNotesProblem")
        column = QVBoxLayout(self)
        column.addWidget(self.notes)
        column.addWidget(self.count)
        column.addWidget(self.problem)
        self._on_changed()

    def isComplete(self) -> bool:  # noqa: N802 - Qt
        """The notes fit what can be saved."""
        return not notes_problem(self.notes.toPlainText())

    def _on_changed(self) -> None:
        text = self.notes.toPlainText()
        # {count}: the characters written; {max}: the most that can be saved.
        self.count.setText(
            self.tr("Characters: {count} of {max}").format(count=len(text), max=MAX_NOTES)
        )
        bad = notes_problem(text)
        self.problem.setText(
            self.tr("The notes are too long, or have characters that cannot be saved.")
            if bad
            else ""
        )
        self.completeChanged.emit()


class InjuryWizard(QWizard):
    """The questionnaire: `draft()` gives the answers once "Report injury" accepts it."""

    def __init__(
        self, today: date, parent: QWidget | None = None, *, onset_day: date | None = None
    ) -> None:
        super().__init__(parent)
        self._today = today
        self.setWindowTitle(self.tr("Report an injury"))
        self.setWizardStyle(QWizard.WizardStyle.ClassicStyle)
        self.setMinimumWidth(DIALOG_MIN_WIDTH)
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setOption(QWizard.WizardOption.NoBackButtonOnStartPage, True)
        self._where = _WherePage(today, onset_day, self)
        self._pain = _PainPage(self)
        self._signs = _SignsPage(self)
        self._notes = _NotesPage(self)
        for page in (self._where, self._pain, self._signs, self._notes):
            self.addPage(page)
        self.setButtonText(QWizard.WizardButton.FinishButton, self.tr("Report injury"))
        for spin in (self._pain.rest, self._pain.walking, self._pain.running, self._pain.morning):
            spin.valueChanged.connect(self._update_alarm)
        self._signs.group.changed.connect(self._update_alarm)
        self._update_alarm()

    # --- Test accessors -------------------------------------------------------------

    @property
    def where_page(self) -> QWizardPage:
        """The first page."""
        return self._where

    @property
    def pain_page(self) -> QWizardPage:
        """The second page."""
        return self._pain

    @property
    def signs_page(self) -> QWizardPage:
        """The third page."""
        return self._signs

    @property
    def notes_page(self) -> QWizardPage:
        """The last page."""
        return self._notes

    @property
    def zone_combo(self) -> QComboBox:
        """Where it hurts."""
        return self._where.zone

    @property
    def side_radios(self) -> dict[Side, QRadioButton]:
        """The sides."""
        return self._where.side_radios

    @property
    def onset_radios(self) -> dict[Onset, QRadioButton]:
        """How it began."""
        return self._where.onset_radios

    @property
    def onset_day_edit(self) -> QDateEdit:
        """The day the pain began."""
        return self._where.day

    @property
    def rest_spin(self) -> QSpinBox:
        """Pain at rest."""
        return self._pain.rest

    @property
    def walking_spin(self) -> QSpinBox:
        """Pain when walking."""
        return self._pain.walking

    @property
    def running_spin(self) -> QSpinBox:
        """Pain when running (the lowest value is "cannot run")."""
        return self._pain.running

    @property
    def morning_spin(self) -> QSpinBox:
        """Pain the morning after."""
        return self._pain.morning

    @property
    def pain_free_checks(self) -> dict[PainFreeActivity, QCheckBox]:
        """What does not hurt."""
        return self._pain.pain_free_checks

    @property
    def signs_group(self) -> SignsGroup:
        """The warning signs."""
        return self._signs.group

    @property
    def notes_edit(self) -> QPlainTextEdit:
        """The doctor's notes."""
        return self._notes.notes

    # --- The answers ----------------------------------------------------------------

    def draft(self) -> QuestionnaireDraft:
        """The answers as they are now."""
        where = self._where
        pain = self._pain
        return QuestionnaireDraft(
            zone=where.zone.currentData(),
            side=where.side(),
            onset=where.onset(),
            onset_day=from_qdate(where.day.date()),
            rest=pain.rest.value(),
            walking=pain.walking.value(),
            running=pain.running.value(),
            next_morning=pain.morning.value(),
            pain_free=pain.pain_free(),
            signs=self._signs.group.signs(),
            notes=self._notes.notes.toPlainText(),
        )

    def _update_alarm(self) -> None:
        """Strong rest pain is a warning sign too: the invitation shows even with no box ticked."""
        draft = replace(self.draft(), signs=frozenset())
        self._signs.group.set_extra_alarm(bool(alarms_of(draft)))
