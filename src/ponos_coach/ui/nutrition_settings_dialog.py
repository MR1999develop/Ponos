# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Dialog "Nutrition settings": activity level, typical meal times and goal periods.

The dialog shows what the profile holds (`NutritionSettings`, re-read after every change: it is
the only source of truth) and starts the changes on the data thread through `SettingsOps`.
Changes of goal periods, meal times and activity level go through a `QUndoStack` (Ctrl+Z,
Ctrl+Shift+Z). Problems are written on the field that has them.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

from PySide6.QtCore import QCoreApplication, QLocale, QModelIndex, Qt, QTime
from PySide6.QtGui import QAction, QKeySequence, QUndoStack
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableView,
    QTimeEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ponos_coach.nutrition.domain import (
    ActivityLevel,
    GoalPeriod,
    LeanMass,
    LeanMassEntry,
    MealTimes,
    WeightLossGoal,
    level_for_day,
)
from ponos_coach.nutrition.domain.settings import MIN_VALID_DATE
from ponos_coach.nutrition.use_cases import NutritionSettings
from ponos_coach.ui.birth_date_edit import from_qdate, to_qdate
from ponos_coach.ui.errors import ErrorMessage
from ponos_coach.ui.form_rows import add_field_row
from ponos_coach.ui.goal_period_dialog import GoalPeriodDialog
from ponos_coach.ui.lean_mass_section import LeanMassSection
from ponos_coach.ui.nutrition_loader import LeanMassApplied
from ponos_coach.ui.nutrition_settings_model import (
    MIN_MEAL_GAP_MIN,
    MealField,
    meal_time_problems,
    meal_times_of,
    pace_of,
)
from ponos_coach.ui.nutrition_texts import activity_level_text, goal_name_text
from ponos_coach.ui.nutrition_undo import (
    AddPeriodCommand,
    RemoveLeanMassCommand,
    RemovePeriodCommand,
    ReplacePeriodCommand,
    SetActivityLevelCommand,
    SetLeanMassCommand,
    SetMealTimesCommand,
    SettingsOps,
)
from ponos_coach.ui.text_table_model import TextTableModel
from ponos_coach.ui.tokens import DIALOG_MIN_WIDTH, SPACE_2, SPACE_4

_MINUTES_PER_HOUR = 60
_TABLE_MIN_HEIGHT = 110
_PACE_DECIMALS = 2
_GRAMS_PER_KG = 1000.0
_MEAL_FIELDS = tuple(MealField)


def _short_date(day: date) -> str:
    return QLocale().toString(to_qdate(day), QLocale.FormatType.ShortFormat)


def _minutes_of(moment: QTime) -> int:
    return moment.hour() * _MINUTES_PER_HOUR + moment.minute()


def _qtime(minute_of_day: int) -> QTime:
    return QTime(minute_of_day // _MINUTES_PER_HOUR, minute_of_day % _MINUTES_PER_HOUR)


class NutritionSettingsDialog(QDialog):
    """Level, meal times and goal periods of the open profile."""

    def __init__(
        self,
        settings: NutritionSettings,
        ops: SettingsOps,
        today: date,
        parent: QWidget | None = None,
        *,
        lean_masses: Sequence[LeanMassEntry] = (),
    ) -> None:
        super().__init__(parent)
        self._ops = ops
        self._today = today
        self._settings = settings
        self._saved_times = settings.meal_times
        self._stack = QUndoStack(self)
        self._levels_model = TextTableModel((self.tr("Valid from"), self.tr("Level")), (), self)
        self._periods_model = TextTableModel(
            (
                self.tr("First day"),
                self.tr("Last day"),
                self.tr("Goal"),
                self.tr("Weight loss pace (kg per week)"),
            ),
            (3,),
            self,
        )
        self._period_dialog: GoalPeriodDialog | None = None
        self.setWindowTitle(self.tr("Nutrition settings"))
        self.setMinimumWidth(DIALOG_MIN_WIDTH)
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self._build()
        self.apply(settings, reset_times=True)
        self.apply_lean_masses(lean_masses)

    # --- Construction -------------------------------------------------------------------

    def _build(self) -> None:
        self._failure = QLabel(self)
        self._failure.setObjectName("settingsFailure")
        self._failure.setTextFormat(Qt.TextFormat.PlainText)
        self._failure.setWordWrap(True)
        self._failure.setAccessibleName(self.tr("Problem"))
        self._failure.hide()
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        buttons.rejected.connect(self.reject)
        outer = QVBoxLayout(self)
        outer.setSpacing(SPACE_4)
        outer.addWidget(self._level_group())
        outer.addWidget(self._lean_mass_group())
        outer.addWidget(self._times_group())
        outer.addWidget(self._periods_group(), stretch=1)
        outer.addWidget(self._failure)
        outer.addWidget(buttons)

    def _level_group(self) -> QGroupBox:
        box = QGroupBox(self.tr("Daily activity outside sport"), self)
        self._level = QComboBox(box)
        self._level.setObjectName("settingsLevel")
        for level in ActivityLevel:
            self._level.addItem(activity_level_text(level), level)
        self._level_from = QDateEdit(box)
        self._level_from.setObjectName("settingsLevelFrom")
        self._level_from.setCalendarPopup(True)
        self._level_from.setDisplayFormat(QLocale().dateFormat(QLocale.FormatType.ShortFormat))
        self._level_from.setDateRange(to_qdate(MIN_VALID_DATE), to_qdate(self._today))
        self._level_from.setDate(to_qdate(self._today))
        self._set_level = QPushButton(self.tr("Set level"), box)
        self._set_level.setObjectName("settingsSetLevel")
        self._set_level.clicked.connect(self._on_set_level)
        note = QLabel(self.tr("The level holds from that day until you set another one."), box)
        note.setWordWrap(True)
        self._levels_table = self._table(self._levels_model, "settingsLevels", box)
        form = QFormLayout()
        form.setVerticalSpacing(SPACE_2)
        add_field_row(form, self.tr("Level"), self._level)
        add_field_row(form, self.tr("Valid from"), self._level_from)
        layout = QVBoxLayout(box)
        layout.addLayout(form)
        layout.addWidget(note)
        layout.addWidget(self._set_level, alignment=Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(self._levels_table)
        return box

    def _lean_mass_group(self) -> LeanMassSection:
        section = LeanMassSection(self._today, self)
        section.set_requested.connect(self._on_set_lean_mass)
        section.remove_requested.connect(self._on_remove_lean_mass)
        self._lean_section = section
        return section

    def _times_group(self) -> QGroupBox:
        box = QGroupBox(self.tr("Typical meal times"), self)
        self._time_edits: dict[MealField, QTimeEdit] = {}
        self._time_errors: dict[MealField, QLabel] = {}
        form = QFormLayout()
        form.setVerticalSpacing(SPACE_2)
        labels = {
            MealField.BREAKFAST: self.tr("Breakfast"),
            MealField.LUNCH: self.tr("Lunch"),
            MealField.DINNER: self.tr("Dinner"),
        }
        for field in _MEAL_FIELDS:
            edit = QTimeEdit(box)
            edit.setObjectName(f"settings{field.value.capitalize()}Time")
            edit.setDisplayFormat(QLocale().timeFormat(QLocale.FormatType.ShortFormat))
            edit.timeChanged.connect(self._update_times)
            error = QLabel(box)
            error.setObjectName(f"settings{field.value.capitalize()}Error")
            error.setTextFormat(Qt.TextFormat.PlainText)
            error.setWordWrap(True)
            error.setAccessibleName(self.tr("Problem with {meal} time").format(meal=labels[field]))
            holder = QWidget(box)
            column = QVBoxLayout(holder)
            column.setContentsMargins(0, 0, 0, 0)
            column.setSpacing(0)
            column.addWidget(edit)
            column.addWidget(error)
            add_field_row(form, labels[field], edit, holder)
            self._time_edits[field] = edit
            self._time_errors[field] = error
        self._times_note = QLabel(box)
        self._times_note.setWordWrap(True)
        self._save_times = QPushButton(self.tr("Save meal times"), box)
        self._save_times.setObjectName("settingsSaveTimes")
        self._save_times.clicked.connect(self._on_save_times)
        layout = QVBoxLayout(box)
        layout.addLayout(form)
        layout.addWidget(self._times_note)
        layout.addWidget(self._save_times, alignment=Qt.AlignmentFlag.AlignLeft)
        return box

    def _periods_group(self) -> QGroupBox:
        box = QGroupBox(self.tr("Goal periods"), self)
        note = QLabel(self.tr("Days outside any period are treated as maintenance."), box)
        note.setWordWrap(True)
        self._periods_table = self._table(self._periods_model, "settingsPeriods", box)
        self._periods_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._periods_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._periods_table.activated.connect(self._on_period_activated)
        self._periods_table.selectionModel().selectionChanged.connect(self._update_period_buttons)
        self._add_period = QPushButton(self.tr("Add period..."), box)
        self._add_period.setObjectName("settingsAddPeriod")
        self._add_period.clicked.connect(self.add_period)
        self._edit_period = QPushButton(self.tr("Change period..."), box)
        self._edit_period.setObjectName("settingsEditPeriod")
        self._edit_period.clicked.connect(self.edit_period)
        self._remove_period = QPushButton(self.tr("Remove period"), box)
        self._remove_period.setObjectName("settingsRemovePeriod")
        self._remove_period.clicked.connect(self.remove_period)
        self._undo_action = self._stack.createUndoAction(self, self.tr("Undo"))
        self._undo_action.setShortcut(QKeySequence.StandardKey.Undo)
        self._redo_action = self._stack.createRedoAction(self, self.tr("Redo"))
        self._redo_action.setShortcut(QKeySequence.StandardKey.Redo)
        for action in (self._undo_action, self._redo_action):
            action.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
            self.addAction(action)
        self._undo_button = self._action_button(self._undo_action, "settingsUndo", box)
        self._redo_button = self._action_button(self._redo_action, "settingsRedo", box)
        row = QHBoxLayout()
        for widget in (
            self._add_period,
            self._edit_period,
            self._remove_period,
            self._undo_button,
            self._redo_button,
        ):
            row.addWidget(widget)
        row.addStretch(1)
        layout = QVBoxLayout(box)
        layout.addWidget(note)
        layout.addWidget(self._periods_table, stretch=1)
        layout.addLayout(row)
        return box

    def _table(self, model: TextTableModel, name: str, parent: QWidget) -> QTableView:
        table = QTableView(parent)
        table.setObjectName(name)
        table.setModel(model)
        table.setShowGrid(False)  # no vertical or horizontal lines
        table.setAlternatingRowColors(True)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        table.setMinimumHeight(_TABLE_MIN_HEIGHT)
        return table

    def _action_button(self, action: QAction, name: str, parent: QWidget) -> QToolButton:
        button = QToolButton(parent)
        button.setObjectName(name)
        button.setDefaultAction(action)
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        return button

    # --- Test accessors --------------------------------------------------------------------

    @property
    def undo_stack(self) -> QUndoStack:
        """The stack of the undoable changes."""
        return self._stack

    @property
    def lean_mass_section(self) -> LeanMassSection:
        """The measured lean mass part of the dialog."""
        return self._lean_section

    @property
    def levels_model(self) -> TextTableModel:
        """The history of the activity levels."""
        return self._levels_model

    @property
    def periods_model(self) -> TextTableModel:
        """The goal periods table."""
        return self._periods_model

    @property
    def periods_table(self) -> QTableView:
        """The goal periods table view."""
        return self._periods_table

    @property
    def level_combo(self) -> QComboBox:
        """The activity level choice."""
        return self._level

    @property
    def level_from_edit(self) -> QDateEdit:
        """The day from which the level holds."""
        return self._level_from

    @property
    def set_level_button(self) -> QPushButton:
        """The button that sets the level."""
        return self._set_level

    @property
    def time_edits(self) -> dict[MealField, QTimeEdit]:
        """The meal time fields."""
        return self._time_edits

    @property
    def save_times_button(self) -> QPushButton:
        """The button that saves the meal times."""
        return self._save_times

    @property
    def add_period_button(self) -> QPushButton:
        """The "Add period..." button."""
        return self._add_period

    @property
    def edit_period_button(self) -> QPushButton:
        """The "Change period..." button."""
        return self._edit_period

    @property
    def remove_period_button(self) -> QPushButton:
        """The "Remove period" button."""
        return self._remove_period

    @property
    def undo_button(self) -> QToolButton:
        """The "Undo" button."""
        return self._undo_button

    @property
    def redo_button(self) -> QToolButton:
        """The "Redo" button."""
        return self._redo_button

    @property
    def period_dialog(self) -> GoalPeriodDialog | None:
        """The open "Add/Change goal period" dialog, if any."""
        return self._period_dialog

    @property
    def failure_text(self) -> str:
        """The failure shown at the bottom, empty if none."""
        return "" if self._failure.isHidden() else self._failure.text()

    def time_error(self, field: MealField) -> str:
        """The problem written under a meal time field, empty if none."""
        return self._time_errors[field].text()

    # --- Showing the stored settings ---------------------------------------------------------

    def apply(self, settings: NutritionSettings, *, reset_times: bool = False) -> None:
        """Show what the profile holds now (after a read or a change).

        The meal time fields follow the stored times when those changed, so an undo shows; text
        the user is typing is kept otherwise.
        """
        self._settings = settings
        self._failure.hide()
        self._levels_model.set_rows(
            [
                (_short_date(entry.valid_from), activity_level_text(entry.level))
                for entry in reversed(settings.levels)
            ]
        )
        current = level_for_day(settings.levels, self._today)
        if current is not None and not self._level.hasFocus():
            self._level.setCurrentIndex(self._level.findData(current))
        self._periods_model.set_rows([_period_row(p) for p in settings.goal_periods])
        if reset_times or settings.meal_times != self._saved_times:
            self._saved_times = settings.meal_times
            self._show_times(settings.meal_times)
        self._times_note.setText(
            ""
            if settings.meal_times_set
            else self.tr("These are the default times. Save them or change them.")
        )
        self._update_times()
        self._update_period_buttons()

    def apply_lean_masses(self, entries: Sequence[LeanMassEntry]) -> None:
        """Show the measured lean mass the profile holds now (after a read or a change)."""
        self._lean_section.apply(entries)

    def show_failure(self, message: ErrorMessage) -> None:
        """Say that a change did not go through; the stack is cleared, it no longer matches."""
        self._stack.clear()
        self._failure.setText(f"{message.title}. {message.what} {message.remedy}")
        self._failure.show()

    def _show_times(self, times: MealTimes) -> None:
        for field, minute in zip(_MEAL_FIELDS, times.minutes, strict=True):
            edit = self._time_edits[field]
            blocked = edit.blockSignals(True)
            edit.setTime(_qtime(minute))
            edit.blockSignals(blocked)

    # --- Activity level ---------------------------------------------------------------------

    def _on_set_level(self) -> None:
        level = self._level.currentData()
        if isinstance(level, ActivityLevel):
            valid_from = from_qdate(self._level_from.date())
            previous = self._level_before_next_command(valid_from)
            self._stack.push(SetActivityLevelCommand(self._ops, valid_from, level, previous))

    def _level_before_next_command(self, valid_from: date) -> ActivityLevel | None:
        """The level the entry of a day holds once every command on the stack has run.

        The stored settings arrive after the jobs, so they can be behind the stack: the last
        command on the stack for that day is the truth, and the stored entry only counts when no
        command touched the day.
        """
        for row in range(self._stack.index() - 1, -1, -1):
            command = self._stack.command(row)
            if isinstance(command, SetActivityLevelCommand) and command.valid_from == valid_from:
                return command.new_level
        return next((e.level for e in self._settings.levels if e.valid_from == valid_from), None)

    # --- Lean mass --------------------------------------------------------------------------

    def _on_set_lean_mass(self, lean_mass: object, valid_from: object) -> None:
        if isinstance(lean_mass, LeanMass) and isinstance(valid_from, date):
            self._ops.set_lean_mass(lean_mass, valid_from)

    def lean_mass_applied(self, applied: LeanMassApplied) -> None:
        """The first set went through: it becomes the undoable step."""
        self._lean_section.show_problem("")
        self._stack.push(
            SetLeanMassCommand(self._ops, applied.lean_mass, applied.valid_from, applied.cell)
        )

    def show_lean_mass_problem(self, message: ErrorMessage) -> None:
        """A rule refused the lean mass: say what and how to fix it, on the field."""
        self._lean_section.show_problem(f"{message.title}. {message.what} {message.remedy}")

    def _on_remove_lean_mass(self, entry: object) -> None:
        if isinstance(entry, LeanMassEntry):
            self._stack.push(RemoveLeanMassCommand(self._ops, entry))

    # --- Meal times -------------------------------------------------------------------------

    def _entered_minutes(self) -> tuple[int, int, int]:
        first, second, third = (_minutes_of(self._time_edits[f].time()) for f in _MEAL_FIELDS)
        return (first, second, third)

    def _update_times(self) -> None:
        minutes = self._entered_minutes()
        problems = meal_time_problems(minutes)
        for field in _MEAL_FIELDS:
            self._time_errors[field].setText(self._time_problem_text(field, field in problems))
        changed = minutes != self._saved_times.minutes
        reason = ""
        if problems:
            reason = self.tr("Solve the problem with the times first.")
        elif not changed and self._settings.meal_times_set:
            reason = self.tr("The times are already saved.")
        self._save_times.setEnabled(not problems and (changed or not self._settings.meal_times_set))
        self._save_times.setToolTip(reason)

    def _time_problem_text(self, field: MealField, has_problem: bool) -> str:
        if not has_problem:
            return ""
        hours = MIN_MEAL_GAP_MIN // _MINUTES_PER_HOUR
        if field is MealField.LUNCH:
            # {hours}: the least number of hours between two meals.
            return self.tr("Lunch must be at least {hours} hours after breakfast.").format(
                hours=hours
            )
        # {hours}: the least number of hours between two meals.
        return self.tr("Dinner must be at least {hours} hours after lunch.").format(hours=hours)

    def _on_save_times(self) -> None:
        times = meal_times_of(self._entered_minutes())
        if times is None:
            return
        self._stack.push(SetMealTimesCommand(self._ops, self._saved_times, times))
        self._saved_times = times
        self._update_times()

    # --- Goal periods -----------------------------------------------------------------------

    def selected_period(self) -> GoalPeriod | None:
        """The period of the selected row, if any."""
        index = self._periods_table.currentIndex()
        rows = self._periods_table.selectionModel().selectedRows()
        row = rows[0].row() if rows else index.row() if index.isValid() else -1
        periods = self._settings.goal_periods
        return periods[row] if 0 <= row < len(periods) else None

    def select_period(self, first_day: date) -> None:
        """Select the row of the period that starts on a day."""
        for row, period in enumerate(self._settings.goal_periods):
            if period.first_day == first_day:
                self._periods_table.selectRow(row)
                return

    def _update_period_buttons(self) -> None:
        has_selection = self.selected_period() is not None
        reason = "" if has_selection else self.tr("Select a period in the table first.")
        for button in (self._edit_period, self._remove_period):
            button.setEnabled(has_selection)
            button.setToolTip(reason)

    def add_period(self) -> None:
        """Open the dialog that adds a goal period."""
        self._open_period_dialog(None)

    def edit_period(self) -> None:
        """Open the dialog that changes the selected goal period."""
        period = self.selected_period()
        if period is not None:
            self._open_period_dialog(period)

    def remove_period(self) -> None:
        """Remove the selected goal period (undoable)."""
        period = self.selected_period()
        if period is not None:
            self._stack.push(RemovePeriodCommand(self._ops, period))

    def _on_period_activated(self, _index: QModelIndex) -> None:
        self.edit_period()

    def _open_period_dialog(self, editing: GoalPeriod | None) -> None:
        if self._period_dialog is not None:
            return
        dialog = GoalPeriodDialog(self._settings.goal_periods, self._today, editing, self)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.accepted.connect(lambda: self._on_period_accepted(dialog, editing))
        dialog.finished.connect(self._on_period_dialog_closed)
        self._period_dialog = dialog
        dialog.open()

    def _on_period_accepted(self, dialog: GoalPeriodDialog, editing: GoalPeriod | None) -> None:
        period = dialog.period()
        if period is None:
            return
        if editing is None:
            self._stack.push(AddPeriodCommand(self._ops, period))
        elif period != editing:
            self._stack.push(ReplacePeriodCommand(self._ops, editing, period))

    def _on_period_dialog_closed(self) -> None:
        self._period_dialog = None


def _period_row(period: GoalPeriod) -> tuple[str, str, str, str]:
    last = (
        QCoreApplication.translate("NutritionSettings", "No end date")
        if period.last_day is None
        else _short_date(period.last_day)
    )
    goal = period.goal
    pace = (
        QLocale().toString(pace_of(goal) / _GRAMS_PER_KG, "f", _PACE_DECIMALS)
        if isinstance(goal, WeightLossGoal)
        else ""
    )
    return (_short_date(period.first_day), last, goal_name_text(goal), pace)
