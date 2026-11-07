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

from ponos_coach.nutrition.domain import (
    ActivityLevel,
    GoalPeriod,
    LeanMass,
    LeanMassEntry,
    MealTimes,
)
from ponos_coach.profiles.lean_mass import AppliedLeanMass


class LeanMassCell:
    """Where the data thread keeps what the last set of a lean mass did, for the job that takes it
    back (`UndoLeanMass` needs it).

    Only jobs on the data thread write and read it, one after the other, so it needs no lock. It is
    empty until the set has run, and again if a set failed.
    """

    def __init__(self) -> None:
        self.value: AppliedLeanMass | None = None


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

    def set_lean_mass(self, lean_mass: LeanMass, valid_from: date) -> bool:
        """Set the lean mass from a day (the first set; a refusal comes back as a signal)."""
        ...

    def reset_lean_mass(self, lean_mass: LeanMass, valid_from: date, cell: LeanMassCell) -> bool:
        """Set a lean mass again (redo, or undoing a removal); `cell` receives what to undo."""
        ...

    def undo_lean_mass(self, cell: LeanMassCell) -> bool:
        """Take a set lean mass back, exactly."""
        ...

    def remove_lean_mass(self, valid_from: date) -> bool:
        """Remove the measurement of a day."""
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


class SetLeanMassCommand(QUndoCommand):
    """Set the measured lean mass from a day; undoing puts back what the day held.

    The command is created when the first set has already gone through (the section sets it and
    only then pushes the command, so that the problems of the domain stay on the field): the first
    `redo` does nothing.
    """

    def __init__(
        self, ops: SettingsOps, lean_mass: LeanMass, valid_from: date, cell: LeanMassCell
    ) -> None:
        super().__init__(QCoreApplication.translate("NutritionUndo", "Set lean mass"))
        self._ops = ops
        self._lean_mass = lean_mass
        self._valid_from = valid_from
        self._cell = cell
        self._already_set = True

    def redo(self) -> None:
        """Set the lean mass (the first time it is already set: nothing to do)."""
        if self._already_set:
            self._already_set = False
            return
        self._ops.reset_lean_mass(self._lean_mass, self._valid_from, self._cell)

    def undo(self) -> None:
        """Put back the measurement of that day, or remove it if there was none."""
        self._ops.undo_lean_mass(self._cell)


class RemoveLeanMassCommand(QUndoCommand):
    """Remove a measurement of the lean mass; undoing sets it back."""

    def __init__(self, ops: SettingsOps, entry: LeanMassEntry) -> None:
        super().__init__(QCoreApplication.translate("NutritionUndo", "Remove lean mass"))
        self._ops = ops
        self._entry = entry

    def redo(self) -> None:
        """Remove the measurement."""
        self._ops.remove_lean_mass(self._entry.valid_from)

    def undo(self) -> None:
        """Set the measurement back."""
        self._ops.reset_lean_mass(self._entry.lean_mass, self._entry.valid_from, LeanMassCell())
