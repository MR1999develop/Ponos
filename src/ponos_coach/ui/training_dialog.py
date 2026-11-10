# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Content of the Settings > Training plan page: the days available, how many sessions a week, the
long day, the most time a week and the session types the plan may use for each sport.

The page edits a copy: nothing changes until "Save settings". What the plan cannot do with the
settings (no day or no time at all) is written under the fields as soon as it appears; it does not
stop saving, because the plan says the same in its own notices. A profile that saved nothing yet
starts from suggested values, and the page says so.
"""

from __future__ import annotations

from PySide6.QtCore import QCoreApplication, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QLabel,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ponos_coach.activities.domain import Sport
from ponos_coach.goals.domain import SessionType
from ponos_coach.goals.domain.availability import WEEKDAYS
from ponos_coach.ui.activity_text import sport_text
from ponos_coach.ui.form_rows import FieldKind, add_field_row, size_field
from ponos_coach.ui.plan_texts import session_type_text, weekday_name
from ponos_coach.ui.settings_form import SettingsForm
from ponos_coach.ui.tokens import SPACE_2, SPACE_4
from ponos_coach.ui.training_draft import (
    MAX_WEEK_HOURS,
    MINUTES_PER_HOUR,
    TrainingDraft,
    TrainingHint,
    hints_of,
    shown_sports,
)

_DAY_COLUMNS = 4  # weekday check boxes per row
_HOURS_STEP = 0.25
_HOURS_DECIMALS = 2


def hint_text(hint: TrainingHint) -> str:
    """What the plan cannot do with the settings, and how to put it right."""
    del hint  # one hint so far: no day or no weekly time
    return QCoreApplication.translate(
        "TrainingDialog",
        "No training time is set, so the plan cannot be made. "
        "Tick at least one day, and give the week some hours.",
    )


class TrainingSettingsDialog(SettingsForm):
    """Weekly availability and session types of the open profile."""

    save_requested = Signal()  # the coordinator reads `draft()` and saves it

    def __init__(
        self,
        draft: TrainingDraft,
        *,
        suggested: bool,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._initial = draft
        self._draft = draft
        self._suggested = suggested
        self._loading = True
        self._build()
        self._show_draft(draft)
        self._loading = False
        self._update()

    # --- Construction -------------------------------------------------------------------

    def _build(self) -> None:
        self._note = QLabel(self)
        self._note.setObjectName("trainingNote")
        self._note.setTextFormat(Qt.TextFormat.PlainText)
        self._note.setWordWrap(True)
        self._note.setVisible(self._suggested)
        self._note.setText(
            self.tr("Nothing is saved yet. These are suggestions: change them, then save.")
        )
        self._hints = QLabel(self)
        self._hints.setObjectName("trainingHints")
        self._hints.setTextFormat(Qt.TextFormat.PlainText)
        self._hints.setWordWrap(True)
        self._hints.setAccessibleName(self.tr("Problem with the settings"))
        self._buttons = QDialogButtonBox(self)
        self._save = self._buttons.addButton(
            self.tr("Save settings"), QDialogButtonBox.ButtonRole.AcceptRole
        )
        self._save.setObjectName("trainingSave")
        self._save.setDefault(True)
        self._buttons.accepted.connect(self.request_save)
        layout = QVBoxLayout(self)
        layout.setSpacing(SPACE_4)
        layout.addWidget(self._note)
        layout.addWidget(self._time_group())
        layout.addWidget(self._hints)
        layout.addWidget(self._types_group())
        layout.addWidget(self._buttons)

    def _time_group(self) -> QGroupBox:
        box = QGroupBox(self.tr("Time available"), self)
        form = QFormLayout()
        form.setVerticalSpacing(SPACE_2)
        days = QWidget(box)
        grid = QGridLayout(days)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(SPACE_4)
        grid.setVerticalSpacing(SPACE_2)
        self._day_checks: list[QCheckBox] = []
        previous: QWidget | None = None
        for weekday in range(WEEKDAYS):
            check = QCheckBox(weekday_name(weekday), days)
            check.setObjectName(f"trainingDay{weekday}")
            check.setAccessibleName(weekday_name(weekday))
            check.toggled.connect(self._on_days_changed)
            grid.addWidget(check, weekday // _DAY_COLUMNS, weekday % _DAY_COLUMNS)
            self._day_checks.append(check)
            if previous is not None:
                QWidget.setTabOrder(previous, check)
            previous = check
        self._sessions = QSpinBox(box)
        self._sessions.setObjectName("trainingSessions")
        self._sessions.setRange(1, WEEKDAYS)
        self._sessions.valueChanged.connect(self._on_sessions_changed)
        self._long_day = QComboBox(box)
        self._long_day.setObjectName("trainingLongDay")
        self._long_day.currentIndexChanged.connect(self._on_long_day_changed)
        self._week_hours = QDoubleSpinBox(box)
        self._week_hours.setObjectName("trainingWeekHours")
        self._week_hours.setRange(0.0, float(MAX_WEEK_HOURS))
        self._week_hours.setDecimals(_HOURS_DECIMALS)
        self._week_hours.setSingleStep(_HOURS_STEP)
        self._week_hours.setSuffix(self.tr(" h"))
        self._week_hours.valueChanged.connect(self._on_week_changed)
        size_field(self._sessions, FieldKind.SHORT)
        size_field(self._long_day, FieldKind.CHOICE)
        size_field(self._week_hours, FieldKind.SHORT)
        add_field_row(form, self.tr("Days available"), days)
        add_field_row(form, self.tr("Sessions per week"), self._sessions)
        add_field_row(form, self.tr("Long session day"), self._long_day)
        add_field_row(form, self.tr("Most training in a week"), self._week_hours)
        if previous is not None:
            QWidget.setTabOrder(previous, self._sessions)
        QWidget.setTabOrder(self._sessions, self._long_day)
        QWidget.setTabOrder(self._long_day, self._week_hours)
        outer = QVBoxLayout(box)
        outer.addLayout(form)
        return box

    def _types_group(self) -> QGroupBox:
        box = QGroupBox(self.tr("Session types the plan may use"), self)
        grid = QGridLayout()
        grid.setHorizontalSpacing(SPACE_4)
        grid.setVerticalSpacing(SPACE_2)
        self._checks: dict[tuple[Sport, SessionType], QCheckBox] = {}
        sports = shown_sports()
        for column, sport in enumerate(sports, start=1):
            header = QLabel(sport_text(sport), box)
            header.setTextFormat(Qt.TextFormat.PlainText)
            grid.addWidget(header, 0, column)
        for row, session_type in enumerate(SessionType, start=1):
            type_label = QLabel(session_type_text(session_type), box)
            type_label.setTextFormat(Qt.TextFormat.PlainText)
            grid.addWidget(type_label, row, 0)
            for column, sport in enumerate(sports, start=1):
                check = QCheckBox(box)
                check.setObjectName(f"trainingType_{sport.value}_{session_type.value}")
                # {sport} and {type}: for example "Running" and "Threshold".
                check.setAccessibleName(
                    self.tr("{sport}: {type}").format(
                        sport=sport_text(sport), type=session_type_text(session_type)
                    )
                )
                check.toggled.connect(self._on_type_toggled)
                grid.addWidget(check, row, column)
                self._checks[(sport, session_type)] = check
        outer = QVBoxLayout(box)
        outer.addLayout(grid)
        return box

    # --- Showing and reading ---------------------------------------------------------------

    def _show_draft(self, draft: TrainingDraft) -> None:
        for weekday, check in enumerate(self._day_checks):
            check.setChecked(weekday in draft.days)
        self._show_day_dependents(draft)
        self._week_hours.setValue(draft.max_week_minutes / MINUTES_PER_HOUR)
        for (sport, session_type), check in self._checks.items():
            check.setChecked(session_type in draft.choices.for_sport(sport))

    def _show_day_dependents(self, draft: TrainingDraft) -> None:
        """The sessions field and the long day list follow the ticked days: at most one session
        per day, the long day among them."""
        self._sessions.setRange(1, max(len(draft.days), 1))
        self._sessions.setValue(draft.sessions)
        self._sessions.setEnabled(bool(draft.days))
        self._long_day.clear()
        for weekday in sorted(draft.days):
            self._long_day.addItem(weekday_name(weekday), weekday)
        self._long_day.setCurrentIndex(self._long_day.findData(draft.long_day))
        self._long_day.setEnabled(bool(draft.days))

    def _on_days_changed(self) -> None:
        if self._loading:
            return
        draft = self._draft
        for weekday, check in enumerate(self._day_checks):
            draft = draft.with_day(weekday, check.isChecked())
        self._draft = draft
        self._loading = True  # the fields below follow the days: they do not change the draft
        self._show_day_dependents(draft)
        self._loading = False
        self._update()

    def _on_sessions_changed(self) -> None:
        if self._loading:
            return
        self._draft = self._draft.with_sessions(self._sessions.value())
        self._update()

    def _on_long_day_changed(self) -> None:
        if self._loading:
            return
        weekday = self._long_day.currentData()
        if isinstance(weekday, int):
            self._draft = self._draft.with_long_day(weekday)
        self._update()

    def _on_week_changed(self) -> None:
        if self._loading:
            return
        self._draft = self._draft.with_max_week_minutes(
            round(self._week_hours.value() * MINUTES_PER_HOUR)
        )
        self._update()

    def _on_type_toggled(self) -> None:
        if self._loading:
            return
        draft = self._draft
        for (sport, session_type), check in self._checks.items():
            draft = draft.with_type(sport, session_type, check.isChecked())
        self._draft = draft
        self._update()

    def _update(self) -> None:
        texts = [hint_text(hint) for hint in hints_of(self._draft)]
        self._hints.setText("\n".join(texts))
        self._hints.setVisible(bool(texts))
        changed = self._draft != self._initial
        self._save.setEnabled(changed or self._suggested)
        self._save.setToolTip("" if changed or self._suggested else self.tr("Nothing has changed."))
        self.edited.emit()

    def has_unsaved_changes(self) -> bool:
        """True while a field differs from what was saved."""
        return self._draft != self._initial

    def request_save(self) -> None:
        """Ask the coordinator to save the fields (the "Save settings" button)."""
        if self._save.isEnabled():
            self.save_requested.emit()

    def show_saved(self, draft: TrainingDraft, *, suggested: bool) -> None:
        """What is saved now: the fields show it and nothing is left to save (also used when the
        saved settings changed elsewhere, for example by an undo)."""
        self._loading = True
        self._initial = draft
        self._draft = draft
        self._suggested = suggested
        self._note.setVisible(suggested)
        self._show_draft(draft)
        self._loading = False
        self._update()

    # --- State for the tests -----------------------------------------------------------------

    @property
    def day_checks(self) -> list[QCheckBox]:
        """The check box of each weekday, Monday first."""
        return self._day_checks

    @property
    def sessions_spin(self) -> QSpinBox:
        """The sessions per week."""
        return self._sessions

    @property
    def long_day_combo(self) -> QComboBox:
        """The long session day."""
        return self._long_day

    @property
    def week_hours_spin(self) -> QDoubleSpinBox:
        """The most training in a week, in hours."""
        return self._week_hours

    @property
    def type_checks(self) -> dict[tuple[Sport, SessionType], QCheckBox]:
        """The session type check boxes."""
        return self._checks

    @property
    def save_button(self) -> QPushButton:
        """The button that saves the settings."""
        return self._save

    @property
    def hint_texts(self) -> str:
        """What the plan cannot do with the settings now (empty if nothing)."""
        return "" if self._hints.isHidden() else self._hints.text()

    @property
    def note_is_shown(self) -> bool:
        """True when the dialog starts from suggestions."""
        return not self._note.isHidden()

    def draft(self) -> TrainingDraft:
        """What the fields hold now."""
        return self._draft
