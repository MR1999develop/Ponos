# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Dialog to add or change a goal: a race or a form goal (a CTL to reach on a day).

The dialog edits a copy: "Cancel" changes nothing. A problem (a day that is not after today, a day
that already has another race, a time that is not `h:mm:ss`) is written under its field, in words,
as soon as it appears, and the save button stays off until it is solved. The rules are in
`goal_draft`; the store checks them again when saving.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

from PySide6.QtCore import QCoreApplication, QLocale, Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ponos_coach.goals.domain import GoalId, Priority
from ponos_coach.goals.ports import Goal
from ponos_coach.ui.activity_text import sport_text
from ponos_coach.ui.birth_date_edit import from_qdate, to_qdate
from ponos_coach.ui.form_rows import add_field_row
from ponos_coach.ui.goal_draft import (
    COMMON_DISTANCES_M,
    MAX_DISTANCE_KM,
    MAX_FORM_CTL,
    MIN_DISTANCE_KM,
    RACE_SPORTS,
    GoalDraft,
    GoalField,
    GoalFormKind,
    GoalIssue,
    GoalProblem,
    draft_of,
    issues_of,
)
from ponos_coach.ui.plan_texts import distance_name
from ponos_coach.ui.tokens import DIALOG_MIN_WIDTH, SPACE_2, SPACE_4

_FIRST_DAY = date(1900, 1, 1)
_LAST_DAY = date(2100, 12, 31)
_DISTANCE_DECIMALS = 3
_DISTANCE_STEP_KM = 0.5
_CTL_STEP = 1.0
_METRES_PER_KM = 1000


def problem_text(issue: GoalIssue) -> str:
    """A problem of a field, in plain words with the way out."""
    translate = QCoreApplication.translate
    match issue.problem:
        case GoalProblem.DATE_NOT_AFTER_TODAY:
            return translate("GoalDialog", "Choose a day after today: the plan starts tomorrow.")
        case GoalProblem.RACE_DAY_TAKEN:
            return translate(
                "GoalDialog", "Another race is already on this day. Choose another day."
            )
        case GoalProblem.DISTANCE_OUT_OF_RANGE:
            # {first} and {last}: the shortest and the longest distance, in kilometres.
            return translate(
                "GoalDialog", "The distance must be between {first} and {last} km."
            ).format(
                first=QLocale().toString(MIN_DISTANCE_KM, "g", 6),
                last=QLocale().toString(MAX_DISTANCE_KM, "g", 6),
            )
        case GoalProblem.TIME_NOT_UNDERSTOOD:
            return translate("GoalDialog", "Write the time as h:mm:ss, for example 1:45:00.")
        case GoalProblem.TIME_OUT_OF_RANGE:
            return translate("GoalDialog", "The target time must be between 1 minute and 7 days.")
        case GoalProblem.CTL_OUT_OF_RANGE:
            # {last}: the highest fitness (CTL) that can be asked for.
            return translate("GoalDialog", "The fitness must be between 0 and {last}.").format(
                last=QLocale().toString(MAX_FORM_CTL, "f", 0)
            )


def priority_choice_text(priority: Priority) -> str:
    """A priority with what it means."""
    texts = {
        Priority.A: QCoreApplication.translate("GoalDialog", "A: the goal of the season"),
        Priority.B: QCoreApplication.translate("GoalDialog", "B: an important race"),
        Priority.C: QCoreApplication.translate("GoalDialog", "C: a race for training"),
    }
    return texts[priority]


class GoalDialog(QDialog):
    """A goal: its day and, for a race, priority, sport, distance and target time; for a form goal,
    the CTL to reach."""

    def __init__(
        self,
        draft: GoalDraft,
        goals: Sequence[Goal],
        today: date,
        editing: GoalId | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._kind = draft.kind
        self._goals = tuple(goals)
        self._today = today
        self._editing = editing
        self.setWindowTitle(self._title())
        self.setMinimumWidth(DIALOG_MIN_WIDTH)
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self._build()
        self._load(draft)
        self._update()

    @classmethod
    def for_goal(
        cls,
        goal: Goal,
        goals: Sequence[Goal],
        today: date,
        parent: QWidget | None = None,
    ) -> GoalDialog:
        """The dialog that changes a saved goal."""
        return cls(draft_of(goal), goals, today, goal.goal_id, parent)

    def _title(self) -> str:
        adding = self._editing is None
        if self._kind is GoalFormKind.RACE:
            return self.tr("Add race") if adding else self.tr("Change race")
        return self.tr("Add form goal") if adding else self.tr("Change form goal")

    # --- Construction -------------------------------------------------------------------

    def _error_label(self, name: str, accessible_name: str) -> QLabel:
        label = QLabel(self)
        label.setObjectName(name)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setWordWrap(True)
        label.setAccessibleName(accessible_name)
        return label

    def _build(self) -> None:
        self._date = QDateEdit(self)
        self._date.setObjectName("goalDate")
        self._date.setCalendarPopup(True)
        self._date.setDisplayFormat(QLocale().dateFormat(QLocale.FormatType.ShortFormat))
        self._date.setDateRange(to_qdate(_FIRST_DAY), to_qdate(_LAST_DAY))
        self._date.dateChanged.connect(self._update)
        self._date_error = self._error_label("goalDateError", self.tr("Problem with the date"))
        form = QFormLayout()
        form.setVerticalSpacing(SPACE_2)
        add_field_row(
            form, self.tr("Date"), self._date, self._with_error(self._date, self._date_error)
        )
        if self._kind is GoalFormKind.RACE:
            self._build_race_fields(form)
        else:
            self._build_form_fields(form)
        self._buttons = QDialogButtonBox(self)
        verb = self.tr("Add goal") if self._editing is None else self.tr("Save goal")
        self._save = self._buttons.addButton(verb, QDialogButtonBox.ButtonRole.AcceptRole)
        self._save.setObjectName("goalSave")
        self._save.setDefault(True)
        self._buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        self._buttons.accepted.connect(self.accept)
        self._buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.setSpacing(SPACE_4)
        layout.addLayout(form)
        layout.addWidget(self._buttons)

    def _with_error(self, field: QWidget, error: QLabel) -> QWidget:
        holder = QWidget(self)
        column = QVBoxLayout(holder)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        column.addWidget(field)
        column.addWidget(error)
        return holder

    def _build_race_fields(self, form: QFormLayout) -> None:
        self._priority = QComboBox(self)
        self._priority.setObjectName("goalPriority")
        for priority in Priority:
            self._priority.addItem(priority_choice_text(priority), priority)
        self._sport = QComboBox(self)
        self._sport.setObjectName("goalSport")
        for sport in RACE_SPORTS:
            self._sport.addItem(sport_text(sport), sport)
        self._distance = QComboBox(self)
        self._distance.setObjectName("goalDistance")
        for metres in COMMON_DISTANCES_M:
            self._distance.addItem(distance_name(metres), metres)
        self._distance.addItem(self.tr("Other distance"), None)
        self._distance.currentIndexChanged.connect(self._update)
        self._custom_km = QDoubleSpinBox(self)
        self._custom_km.setObjectName("goalDistanceKm")
        self._custom_km.setDecimals(_DISTANCE_DECIMALS)
        self._custom_km.setSingleStep(_DISTANCE_STEP_KM)
        self._custom_km.setRange(MIN_DISTANCE_KM, MAX_DISTANCE_KM)
        self._custom_km.setSuffix(self.tr(" km"))
        self._custom_km.setAccessibleName(self.tr("Distance in kilometres"))
        self._custom_km.valueChanged.connect(self._update)
        self._distance_error = self._error_label(
            "goalDistanceError", self.tr("Problem with the distance")
        )
        distance_holder = QWidget(self)
        column = QVBoxLayout(distance_holder)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(SPACE_2)
        column.addWidget(self._distance)
        column.addWidget(self._custom_km)
        column.addWidget(self._distance_error)
        self._time = QLineEdit(self)
        self._time.setObjectName("goalTargetTime")
        self._time.setPlaceholderText(self.tr("h:mm:ss"))
        self._time.setAccessibleDescription(self.tr("Optional. For example 1:45:00."))
        self._time.textChanged.connect(self._update)
        self._time_error = self._error_label("goalTimeError", self.tr("Problem with the time"))
        add_field_row(form, self.tr("Priority"), self._priority)
        add_field_row(form, self.tr("Sport"), self._sport)
        add_field_row(form, self.tr("Distance"), self._distance, distance_holder)
        add_field_row(
            form,
            self.tr("Target time (optional)"),
            self._time,
            self._with_error(self._time, self._time_error),
        )
        QWidget.setTabOrder(self._date, self._priority)
        QWidget.setTabOrder(self._priority, self._sport)
        QWidget.setTabOrder(self._sport, self._distance)
        QWidget.setTabOrder(self._distance, self._custom_km)
        QWidget.setTabOrder(self._custom_km, self._time)

    def _build_form_fields(self, form: QFormLayout) -> None:
        self._ctl = QDoubleSpinBox(self)
        self._ctl.setObjectName("goalCtl")
        self._ctl.setDecimals(0)
        self._ctl.setSingleStep(_CTL_STEP)
        self._ctl.setRange(0.0, MAX_FORM_CTL)
        self._ctl.valueChanged.connect(self._update)
        self._ctl_error = self._error_label("goalCtlError", self.tr("Problem with the fitness"))
        add_field_row(
            form,
            self.tr("Fitness to reach (CTL)"),
            self._ctl,
            self._with_error(self._ctl, self._ctl_error),
        )
        QWidget.setTabOrder(self._date, self._ctl)

    def _load(self, draft: GoalDraft) -> None:
        """Fill the fields from the draft."""
        self._date.setDate(to_qdate(draft.day))
        if self._kind is GoalFormKind.FORM:
            self._ctl.setValue(draft.ctl)
            return
        self._priority.setCurrentIndex(self._priority.findData(draft.priority))
        self._sport.setCurrentIndex(max(self._sport.findData(draft.sport), 0))
        index = self._distance.findData(draft.distance_m)
        if index >= 0:
            self._distance.setCurrentIndex(index)
        else:
            self._distance.setCurrentIndex(self._distance.count() - 1)
            self._custom_km.setValue(draft.distance_m / _METRES_PER_KM)
        self._time.setText(draft.target_time_text)

    # --- State ---------------------------------------------------------------------------------

    @property
    def date_edit(self) -> QDateEdit:
        """The date field."""
        return self._date

    @property
    def priority_combo(self) -> QComboBox:
        """The priority choice (races only)."""
        return self._priority

    @property
    def sport_combo(self) -> QComboBox:
        """The sport choice (races only)."""
        return self._sport

    @property
    def distance_combo(self) -> QComboBox:
        """The distance choice (races only)."""
        return self._distance

    @property
    def custom_distance_spin(self) -> QDoubleSpinBox:
        """The distance in kilometres, for "Other distance" (races only)."""
        return self._custom_km

    @property
    def target_time_edit(self) -> QLineEdit:
        """The target time field (races only)."""
        return self._time

    @property
    def ctl_spin(self) -> QDoubleSpinBox:
        """The CTL to reach (form goals only)."""
        return self._ctl

    @property
    def save_button(self) -> QPushButton:
        """The button that saves the goal."""
        return self._save

    @property
    def kind(self) -> GoalFormKind:
        """What the dialog asks for."""
        return self._kind

    def draft(self) -> GoalDraft:
        """What the fields hold now."""
        day = from_qdate(self._date.date())
        if self._kind is GoalFormKind.FORM:
            return GoalDraft(self._kind, day, ctl=self._ctl.value())
        priority = self._priority.currentData()
        sport = self._sport.currentData()
        metres = self._distance.currentData()
        distance_m = round(self._custom_km.value() * _METRES_PER_KM) if metres is None else metres
        return GoalDraft(
            self._kind,
            day,
            priority if isinstance(priority, Priority) else Priority.A,
            sport if sport in RACE_SPORTS else RACE_SPORTS[0],
            distance_m,
            self._time.text(),
        )

    def issues(self) -> tuple[GoalIssue, ...]:
        """The problems of the fields now."""
        return issues_of(self.draft(), self._goals, self._today, self._editing)

    def error_text(self, field: GoalField) -> str:
        """The problem written under a field, empty if there is none."""
        label = dict(self._error_labels()).get(field)
        return "" if label is None else label.text()

    def _update(self) -> None:
        issues = {issue.field: issue for issue in self.issues()}
        for field, label in self._error_labels():
            issue = issues.get(field)
            label.setText("" if issue is None else problem_text(issue))
        if self._kind is GoalFormKind.RACE:
            self._custom_km.setEnabled(self._distance.currentData() is None)
        self._save.setEnabled(not issues)
        self._save.setToolTip(
            "" if not issues else self.tr("Solve the problem written under the field first.")
        )

    def _error_labels(self) -> list[tuple[GoalField, QLabel]]:
        labels = [(GoalField.DATE, self._date_error)]
        if self._kind is GoalFormKind.RACE:
            labels += [
                (GoalField.DISTANCE, self._distance_error),
                (GoalField.TARGET_TIME, self._time_error),
            ]
        else:
            labels.append((GoalField.CTL, self._ctl_error))
        return labels
