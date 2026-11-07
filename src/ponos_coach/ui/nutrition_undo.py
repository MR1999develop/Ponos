# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Undoable changes of the nutrition settings (goal periods, meal times and activity level).

Each command is a `QUndoCommand` that starts the job on the data thread and does not wait: jobs run
in order on that one thread, so a redo or an undo always lands after the one before it. The window
shows what the store holds after each job, so a change that was refused (rare: the form already
checked) never stays on screen and the stack is cleared.
"""

from __future__ import annotations

from datetime import date
from typing import Protocol

from PySide6.QtCore import QCoreApplication
from PySide6.QtGui import QUndoCommand

from ponos_coach.nutrition.domain import ActivityLevel, GoalPeriod, MealTimes


class SettingsOps(Protocol):
    """The changes a command can start (implemented by `NutritionLoader`)."""

    def add_period(self, period: GoalPeriod) -> bool:
        """Add a goal period."""
        ...

    def replace_period(self, old_first_day: date, new: GoalPeriod) -> bool:
        """Replace the period that starts on `old_first_day`."""
        ...

    def remove_period(self, first_day: date) -> bool:
        """Remove the period that starts on `first_day`."""
        ...

    def set_meal_times(self, times: MealTimes) -> bool:
        """Set the typical meal times."""
        ...

    def remove_level(self, valid_from: date) -> bool:
        """Remove the activity level entry of a day."""
        ...

    def set_level(self, level: ActivityLevel, valid_from: date) -> bool:
        """Set the activity level from a day (the same day replaces the entry)."""
        ...


class AddPeriodCommand(QUndoCommand):
    """Add a goal period; undoing removes it."""

    def __init__(self, ops: SettingsOps, period: GoalPeriod) -> None:
        super().__init__(QCoreApplication.translate("NutritionUndo", "Add goal period"))
        self._ops = ops
        self._period = period

    def redo(self) -> None:
        """Add the period."""
        self._ops.add_period(self._period)

    def undo(self) -> None:
        """Remove the period."""
        self._ops.remove_period(self._period.first_day)


class RemovePeriodCommand(QUndoCommand):
    """Remove a goal period; undoing adds it back."""

    def __init__(self, ops: SettingsOps, period: GoalPeriod) -> None:
        super().__init__(QCoreApplication.translate("NutritionUndo", "Remove goal period"))
        self._ops = ops
        self._period = period

    def redo(self) -> None:
        """Remove the period."""
        self._ops.remove_period(self._period.first_day)

    def undo(self) -> None:
        """Add the period back."""
        self._ops.add_period(self._period)


class ReplacePeriodCommand(QUndoCommand):
    """Change a goal period; undoing puts the old one back."""

    def __init__(self, ops: SettingsOps, old: GoalPeriod, new: GoalPeriod) -> None:
        super().__init__(QCoreApplication.translate("NutritionUndo", "Change goal period"))
        self._ops = ops
        self._old = old
        self._new = new

    def redo(self) -> None:
        """Put the new period in place of the old one."""
        self._ops.replace_period(self._old.first_day, self._new)

    def undo(self) -> None:
        """Put the old period back in place of the new one."""
        self._ops.replace_period(self._new.first_day, self._old)


class SetMealTimesCommand(QUndoCommand):
    """Change the typical meal times; undoing sets the previous ones."""

    def __init__(self, ops: SettingsOps, old: MealTimes, new: MealTimes) -> None:
        super().__init__(QCoreApplication.translate("NutritionUndo", "Change meal times"))
        self._ops = ops
        self._old = old
        self._new = new

    def redo(self) -> None:
        """Set the new times."""
        self._ops.set_meal_times(self._new)

    def undo(self) -> None:
        """Set the previous times."""
        self._ops.set_meal_times(self._old)


class SetActivityLevelCommand(QUndoCommand):
    """Set the activity level from a day; undoing restores exactly what that day held.

    `previous` is the level of the entry saved on `valid_from` before the change, or None if the day
    had no entry: undoing then removes the entry, so the store is as it was.
    """

    def __init__(
        self,
        ops: SettingsOps,
        valid_from: date,
        new: ActivityLevel,
        previous: ActivityLevel | None,
    ) -> None:
        super().__init__(QCoreApplication.translate("NutritionUndo", "Set activity level"))
        self._ops = ops
        self._valid_from = valid_from
        self._new = new
        self._previous = previous

    @property
    def valid_from(self) -> date:
        """The day the entry starts on."""
        return self._valid_from

    @property
    def new_level(self) -> ActivityLevel:
        """The level this command sets."""
        return self._new

    def redo(self) -> None:
        """Set the new level."""
        self._ops.set_level(self._new, self._valid_from)

    def undo(self) -> None:
        """Put back the entry of that day, or remove it if there was none."""
        if self._previous is None:
            self._ops.remove_level(self._valid_from)
        else:
            self._ops.set_level(self._previous, self._valid_from)
