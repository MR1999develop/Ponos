# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Dialog to add or change a nutrition goal period.

Dates, goal and, for weight loss, the pace in kilograms per week (within the domain's limits).
The dialog edits a copy: "Cancel" changes nothing. A problem (the end before the start, a period
that overlaps a saved one) is written under the dates, in words, as soon as it appears, and the
save button stays off until it is solved. The dates of the period being changed do not count as
overlapping themselves.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, timedelta

from PySide6.QtCore import QLocale, Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ponos_coach.nutrition.domain import GoalPeriod
from ponos_coach.nutrition.domain.settings import MIN_VALID_DATE
from ponos_coach.ui.birth_date_edit import from_qdate, to_qdate
from ponos_coach.ui.calendar_model import GoalKind, goal_kind_of
from ponos_coach.ui.calendar_texts import goal_kind_text
from ponos_coach.ui.form_rows import add_field_row
from ponos_coach.ui.nutrition_settings_model import (
    DEFAULT_PACE_G,
    MAX_PACE_G,
    MIN_PACE_G,
    PeriodIssue,
    PeriodProblem,
    goal_of,
    pace_in_grams,
    pace_of,
    period_issue,
)
from ponos_coach.ui.tokens import DIALOG_MIN_WIDTH, SPACE_2, SPACE_4

_LAST_DAY = date(2100, 12, 31)
_DEFAULT_LENGTH_DAYS = 27  # four weeks, first day included
_PACE_STEP_KG = 0.05
_PACE_DECIMALS = 2
_GRAMS_PER_KG = 1000.0


def _short_date(day: date) -> str:
    return QLocale().toString(to_qdate(day), QLocale.FormatType.ShortFormat)


class GoalPeriodDialog(QDialog):
    """A goal period: first day, last day (or none), goal and pace."""

    def __init__(
        self,
        saved: Sequence[GoalPeriod],
        today: date,
        editing: GoalPeriod | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._saved = tuple(saved)
        self._editing = editing
        self.setWindowTitle(
            self.tr("Add goal period") if editing is None else self.tr("Change goal period")
        )
        self.setMinimumWidth(DIALOG_MIN_WIDTH)
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self._build()
        self._load(today)
        self._update()

    # --- Construction -------------------------------------------------------------------

    def _date_edit(self, name: str) -> QDateEdit:
        edit = QDateEdit(self)
        edit.setObjectName(name)
        edit.setCalendarPopup(True)
        edit.setDisplayFormat(QLocale().dateFormat(QLocale.FormatType.ShortFormat))
        edit.setDateRange(to_qdate(MIN_VALID_DATE), to_qdate(_LAST_DAY))
        edit.dateChanged.connect(self._update)
        return edit

    def _build(self) -> None:
        self._first = self._date_edit("periodFirstDay")
        self._no_end = QCheckBox(self.tr("No end date"), self)
        self._no_end.setObjectName("periodNoEnd")
        self._no_end.toggled.connect(self._update)
        self._last = self._date_edit("periodLastDay")
        self._goal = QComboBox(self)
        self._goal.setObjectName("periodGoal")
        for kind in GoalKind:
            self._goal.addItem(goal_kind_text(kind), kind)
        self._goal.currentIndexChanged.connect(self._update)
        self._pace = QDoubleSpinBox(self)
        self._pace.setObjectName("periodPace")
        self._pace.setDecimals(_PACE_DECIMALS)
        self._pace.setSingleStep(_PACE_STEP_KG)
        self._pace.setRange(MIN_PACE_G / _GRAMS_PER_KG, MAX_PACE_G / _GRAMS_PER_KG)
        self._pace.setSuffix(self.tr(" kg per week"))
        self._pace.valueChanged.connect(self._update)
        self._error = QLabel(self)
        self._error.setObjectName("periodError")
        self._error.setTextFormat(Qt.TextFormat.PlainText)
        self._error.setWordWrap(True)
        self._error.setAccessibleName(self.tr("Problem with the dates"))

        form = QFormLayout()
        form.setVerticalSpacing(SPACE_2)
        add_field_row(form, self.tr("First day"), self._first)
        add_field_row(form, self.tr("Last day"), self._last)
        form.addRow("", self._no_end)
        add_field_row(form, self.tr("Goal"), self._goal)
        add_field_row(form, self.tr("Weight loss pace"), self._pace)

        self._buttons = QDialogButtonBox(self)
        verb = self.tr("Add period") if self._editing is None else self.tr("Save period")
        self._save = self._buttons.addButton(verb, QDialogButtonBox.ButtonRole.AcceptRole)
        self._save.setDefault(True)
        self._buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        self._buttons.accepted.connect(self.accept)
        self._buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.setSpacing(SPACE_4)
        layout.addLayout(form)
        layout.addWidget(self._error)
        layout.addWidget(self._buttons)
        QWidget.setTabOrder(self._first, self._last)
        QWidget.setTabOrder(self._last, self._no_end)
        QWidget.setTabOrder(self._no_end, self._goal)
        QWidget.setTabOrder(self._goal, self._pace)

    def _load(self, new_first_day: date) -> None:
        """Fill the fields from the period being changed, or with the defaults of a new one."""
        if self._editing is None:
            self._first.setDate(to_qdate(new_first_day))
            self._last.setDate(to_qdate(new_first_day + timedelta(days=_DEFAULT_LENGTH_DAYS)))
            self._goal.setCurrentIndex(self._goal.findData(GoalKind.WEIGHT_LOSS))
            self._pace.setValue(DEFAULT_PACE_G / _GRAMS_PER_KG)
            return
        period = self._editing
        self._first.setDate(to_qdate(period.first_day))
        last = period.last_day or period.first_day + timedelta(days=_DEFAULT_LENGTH_DAYS)
        self._last.setDate(to_qdate(last))
        self._no_end.setChecked(period.last_day is None)
        self._goal.setCurrentIndex(self._goal.findData(goal_kind_of(period.goal)))
        self._pace.setValue(pace_of(period.goal) / _GRAMS_PER_KG)

    # --- State --------------------------------------------------------------------------------

    @property
    def first_day_edit(self) -> QDateEdit:
        """The first day field."""
        return self._first

    @property
    def last_day_edit(self) -> QDateEdit:
        """The last day field."""
        return self._last

    @property
    def no_end_check(self) -> QCheckBox:
        """The "No end date" check box."""
        return self._no_end

    @property
    def goal_combo(self) -> QComboBox:
        """The goal choice."""
        return self._goal

    @property
    def pace_spin(self) -> QDoubleSpinBox:
        """The weight loss pace in kg per week."""
        return self._pace

    @property
    def save_button(self) -> QPushButton:
        """The button that saves the period."""
        return self._save

    @property
    def error_text(self) -> str:
        """The problem shown, empty if there is none."""
        return self._error.text()

    def set_dates(self, first: date, last: date | None) -> None:
        """Set the dates (for tests and shortcuts); `None` means no end date."""
        self._first.setDate(to_qdate(first))
        if last is not None:
            self._last.setDate(to_qdate(last))
        self._no_end.setChecked(last is None)

    def _dates(self) -> tuple[date, date | None]:
        first = from_qdate(self._first.date())
        last = None if self._no_end.isChecked() else from_qdate(self._last.date())
        return (first, last)

    def _issue(self) -> PeriodIssue | None:
        first, last = self._dates()
        editing_first = None if self._editing is None else self._editing.first_day
        return period_issue(first, last, self._saved, editing_first)

    def period(self) -> GoalPeriod | None:
        """The period as filled in, or `None` while there is a problem."""
        if self._issue() is not None:
            return None
        first, last = self._dates()
        kind = self._goal.currentData()
        grams = pace_in_grams(self._pace.value())
        return GoalPeriod(first, last, goal_of(kind, grams))

    def _issue_text(self, issue: PeriodIssue | None) -> str:
        if issue is None:
            return ""
        other = issue.other
        if issue.problem is PeriodProblem.LAST_BEFORE_FIRST or other is None:
            return self.tr("The last day cannot be before the first day.")
        if other.last_day is None:
            # {first}: the first day of the other period.
            return self.tr(
                "These days overlap the period that starts on {first} and has no end date."
            ).format(first=_short_date(other.first_day))
        # {first} and {last}: the first and the last day of the other period.
        return self.tr("These days overlap the period from {first} to {last}.").format(
            first=_short_date(other.first_day), last=_short_date(other.last_day)
        )

    def _update(self) -> None:
        self._last.setEnabled(not self._no_end.isChecked())
        weight_loss = self._goal.currentData() is GoalKind.WEIGHT_LOSS
        self._pace.setEnabled(weight_loss)
        issue = self._issue()
        self._error.setText(self._issue_text(issue))
        self._save.setEnabled(issue is None)
