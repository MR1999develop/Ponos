# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Dialogs of the injuries: report an illness, the daily check-in, and the pieces the questionnaire
shares (the pain answer, the warning signs).

Every dialog edits a copy: "Cancel" changes nothing. The pain answers are spin boxes that cannot
leave the 0 to 10 scale; the answer that may be missing ("cannot run", "no activity today") is the
box's lowest value, with words. A warning sign ticked shows the invitation to a doctor at once,
under the signs, and does not block anything. The save button says what it does.
Health data: never in logs or error messages.
"""

from __future__ import annotations

from datetime import date

from PySide6.QtCore import QCoreApplication, QLocale, Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ponos_coach.injury.domain import IllnessKind, WarningSign
from ponos_coach.injury.domain.vocabulary import MAX_PAIN
from ponos_coach.ui.birth_date_edit import from_qdate, to_qdate
from ponos_coach.ui.form_rows import add_field_row
from ponos_coach.ui.injury_draft import NO_ANSWER, CheckInDraft
from ponos_coach.ui.injury_texts import (
    date_text,
    doctor_invitation,
    illness_kind_hint,
    illness_kind_text,
    sign_text,
)
from ponos_coach.ui.tokens import DIALOG_MIN_WIDTH, SPACE_2, SPACE_4

_FIRST_DAY = date(1900, 1, 1)
_SIGNS_HEIGHT = 240


def pain_spin(parent: QWidget, name: str, accessible_name: str, missing_text: str = "") -> QSpinBox:
    """A pain answer from 0 to 10. With `missing_text` the lowest value is "no answer" (it reads
    that text), for the answers that may not exist."""
    spin = QSpinBox(parent)
    spin.setObjectName(name)
    spin.setAccessibleName(accessible_name)
    spin.setRange(NO_ANSWER if missing_text else 0, MAX_PAIN)
    if missing_text:
        spin.setSpecialValueText(missing_text)
        spin.setValue(NO_ANSWER)
    return spin


def pain_scale_hint() -> str:
    """What the ends of the pain scale mean."""
    return QCoreApplication.translate(
        "InjuryDialogs", "Pain from 0 (none) to 10 (the worst you can imagine)."
    )


class SignsGroup(QGroupBox):
    """The warning signs as boxes to tick, with the invitation to a doctor under them as soon as
    one is ticked (or `set_extra_alarm` says there is another reason, such as strong rest pain)."""

    changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("signsGroup")
        self.setTitle(self.tr("Do you have any of these?"))
        self._extra_alarm = False
        self._checks: dict[WarningSign, QCheckBox] = {}
        inner = QWidget(self)
        column = QVBoxLayout(inner)
        column.setSpacing(SPACE_2)
        for sign in WarningSign:
            check = QCheckBox(sign_text(sign), inner)
            check.setObjectName(f"sign_{sign.value}")
            check.toggled.connect(self._on_toggled)
            column.addWidget(check)
            self._checks[sign] = check
        scroll = QScrollArea(self)
        scroll.setObjectName("signsScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setMinimumHeight(_SIGNS_HEIGHT)
        scroll.setWidget(inner)
        self._invitation = QLabel(self)
        self._invitation.setObjectName("signsInvitation")
        self._invitation.setTextFormat(Qt.TextFormat.PlainText)
        self._invitation.setWordWrap(True)
        self._invitation.setText(doctor_invitation())
        self._invitation.setVisible(False)
        layout = QVBoxLayout(self)
        layout.addWidget(scroll)
        layout.addWidget(self._invitation)

    @property
    def checks(self) -> dict[WarningSign, QCheckBox]:
        """The check boxes by sign."""
        return self._checks

    def invitation_shown(self) -> bool:
        """True when the invitation to a doctor is on screen."""
        return not self._invitation.isHidden()

    def signs(self) -> frozenset[WarningSign]:
        """The signs ticked."""
        return frozenset(sign for sign, check in self._checks.items() if check.isChecked())

    def set_signs(self, signs: frozenset[WarningSign]) -> None:
        """Tick exactly these signs."""
        for sign, check in self._checks.items():
            check.setChecked(sign in signs)

    def set_extra_alarm(self, alarm: bool) -> None:
        """Another reason for the doctor's invitation (strong pain at rest)."""
        self._extra_alarm = alarm
        self._update()

    def _on_toggled(self) -> None:
        self._update()
        self.changed.emit()

    def _update(self) -> None:
        self._invitation.setVisible(bool(self.signs()) or self._extra_alarm)


class IllnessDialog(QDialog):
    """Report an illness: which group of symptoms and since when."""

    def __init__(self, today: date, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(self.tr("Report illness"))
        self.setMinimumWidth(DIALOG_MIN_WIDTH)
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self._kinds = QButtonGroup(self)
        self._radios: dict[IllnessKind, QRadioButton] = {}
        column = QVBoxLayout()
        for kind in IllnessKind:
            radio = QRadioButton(illness_kind_text(kind), self)
            radio.setObjectName(f"illness_{kind.value}")
            self._kinds.addButton(radio)
            self._radios[kind] = radio
            column.addWidget(radio)
        self._radios[IllnessKind.NECK_UP].setChecked(True)
        self._hint = QLabel(self)
        self._hint.setObjectName("illnessHint")
        self._hint.setTextFormat(Qt.TextFormat.PlainText)
        self._hint.setWordWrap(True)
        self._date = QDateEdit(self)
        self._date.setObjectName("illnessDate")
        self._date.setCalendarPopup(True)
        self._date.setDisplayFormat(QLocale().dateFormat(QLocale.FormatType.ShortFormat))
        self._date.setDateRange(to_qdate(_FIRST_DAY), to_qdate(today))
        self._date.setDate(to_qdate(today))
        form = QFormLayout()
        add_field_row(form, self.tr("I have felt ill since"), self._date)
        self._buttons = QDialogButtonBox(self)
        self._save = self._buttons.addButton(
            self.tr("Report illness"), QDialogButtonBox.ButtonRole.AcceptRole
        )
        self._save.setObjectName("illnessSave")
        self._save.setDefault(True)
        self._buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        self._buttons.accepted.connect(self.accept)
        self._buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.setSpacing(SPACE_4)
        layout.addLayout(column)
        layout.addWidget(self._hint)
        layout.addLayout(form)
        layout.addWidget(self._buttons)
        for radio in self._radios.values():
            radio.toggled.connect(self._update_hint)
        self._update_hint()

    @property
    def date_edit(self) -> QDateEdit:
        """The day the illness began."""
        return self._date

    @property
    def radios(self) -> dict[IllnessKind, QRadioButton]:
        """The choices of symptoms."""
        return self._radios

    @property
    def save_button(self) -> QPushButton:
        """The button that reports the illness."""
        return self._save

    def kind(self) -> IllnessKind:
        """The group of symptoms chosen."""
        return next(kind for kind, radio in self._radios.items() if radio.isChecked())

    def started_on(self) -> date:
        """The day the illness began."""
        return from_qdate(self._date.date())

    def _update_hint(self) -> None:
        self._hint.setText(illness_kind_hint(self.kind()))


class CheckInDialog(QDialog):
    """The day's answers: pain during the activity, pain this morning, any warning sign."""

    def __init__(
        self, problem: str, day: date, draft: CheckInDraft, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(self.tr("Daily check-in"))
        self.setMinimumWidth(DIALOG_MIN_WIDTH)
        self.setWindowModality(Qt.WindowModality.WindowModal)
        # {problem}: the injury; {date}: the day of the check-in.
        intro = QLabel(
            self.tr("Check-in of {date} for: {problem}").format(
                date=date_text(day), problem=problem
            ),
            self,
        )
        intro.setObjectName("checkinIntro")
        intro.setTextFormat(Qt.TextFormat.PlainText)
        intro.setWordWrap(True)
        scale = QLabel(pain_scale_hint(), self)
        scale.setTextFormat(Qt.TextFormat.PlainText)
        scale.setWordWrap(True)
        self._during = pain_spin(
            self,
            "checkinDuring",
            self.tr("Pain during the activity today"),
            self.tr("No activity today"),
        )
        self._morning = pain_spin(self, "checkinMorning", self.tr("Pain this morning"))
        self._signs = SignsGroup(self)
        form = QFormLayout()
        add_field_row(form, self.tr("Pain during the activity today"), self._during)
        add_field_row(form, self.tr("Pain this morning"), self._morning)
        self._buttons = QDialogButtonBox(self)
        self._save = self._buttons.addButton(
            self.tr("Save check-in"), QDialogButtonBox.ButtonRole.AcceptRole
        )
        self._save.setObjectName("checkinSave")
        self._save.setDefault(True)
        self._buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        self._buttons.accepted.connect(self.accept)
        self._buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.setSpacing(SPACE_4)
        layout.addWidget(intro)
        layout.addWidget(scale)
        layout.addLayout(form)
        layout.addWidget(self._signs)
        layout.addWidget(self._buttons)
        self._during.setValue(draft.pain_during)
        self._morning.setValue(draft.pain_morning)
        self._signs.set_signs(draft.signs)

    @property
    def during_spin(self) -> QSpinBox:
        """Pain during the activity (the lowest value is "no activity today")."""
        return self._during

    @property
    def morning_spin(self) -> QSpinBox:
        """Pain this morning."""
        return self._morning

    @property
    def signs_group(self) -> SignsGroup:
        """The warning signs."""
        return self._signs

    @property
    def save_button(self) -> QPushButton:
        """The button that saves the check-in."""
        return self._save

    def draft(self) -> CheckInDraft:
        """The answers as they are now."""
        return CheckInDraft(self._during.value(), self._morning.value(), self._signs.signs())
