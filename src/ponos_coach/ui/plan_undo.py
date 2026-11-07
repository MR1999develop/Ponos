# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Undoable changes of the goals, the availability and the session types.

Each command is a `QUndoCommand` that starts the job on the data thread and does not wait: jobs run
in order on that one thread, so a redo or an undo always lands after the one before it. Undoing
puts back exactly what the use case reported as the previous state (`before` / `after` of the
change): an added goal is removed, a changed goal goes back to its old version, a removed goal is
added again, an availability or a choice returns to what it was (an availability that was never
set returns to "not set"). If a job is refused the window shows the message and clears the stack.
"""

from __future__ import annotations

from typing import Protocol

from PySide6.QtCore import QCoreApplication
from PySide6.QtGui import QUndoCommand

from ponos_coach.goals.domain import GoalId, SessionChoices, WeeklyAvailability
from ponos_coach.goals.ports import Goal


class PlanOps(Protocol):
    """The changes a command can start (implemented by `PlanLoader`)."""

    def add_goal(self, goal: Goal) -> bool:
        """Add a goal."""
        ...

    def change_goal(self, goal: Goal) -> bool:
        """Replace the goal with the same id."""
        ...

    def remove_goal(self, goal_id: GoalId) -> bool:
        """Remove a goal."""
        ...

    def set_availability(self, availability: WeeklyAvailability | None) -> bool:
        """Set the weekly availability."""
        ...

    def set_choices(self, choices: SessionChoices) -> bool:
        """Set the session types."""
        ...


class AddGoalCommand(QUndoCommand):
    """Add a goal; undoing removes it."""

    def __init__(self, ops: PlanOps, goal: Goal) -> None:
        super().__init__(QCoreApplication.translate("PlanUndo", "Add goal"))
        self._ops = ops
        self._goal = goal

    def redo(self) -> None:
        """Add the goal."""
        self._ops.add_goal(self._goal)

    def undo(self) -> None:
        """Remove the goal."""
        self._ops.remove_goal(self._goal.goal_id)


class ChangeGoalCommand(QUndoCommand):
    """Change a goal; undoing puts the old version back."""

    def __init__(self, ops: PlanOps, before: Goal, after: Goal) -> None:
        super().__init__(QCoreApplication.translate("PlanUndo", "Change goal"))
        if before.goal_id != after.goal_id:
            raise ValueError("a change keeps the goal's id")
        self._ops = ops
        self._before = before
        self._after = after

    def redo(self) -> None:
        """Save the new version."""
        self._ops.change_goal(self._after)

    def undo(self) -> None:
        """Save the old version again."""
        self._ops.change_goal(self._before)


class RemoveGoalCommand(QUndoCommand):
    """Remove a goal; undoing adds it back, as it was."""

    def __init__(self, ops: PlanOps, goal: Goal) -> None:
        super().__init__(QCoreApplication.translate("PlanUndo", "Remove goal"))
        self._ops = ops
        self._goal = goal

    def redo(self) -> None:
        """Remove the goal."""
        self._ops.remove_goal(self._goal.goal_id)

    def undo(self) -> None:
        """Add the goal back."""
        self._ops.add_goal(self._goal)


class SetAvailabilityCommand(QUndoCommand):
    """Change the weekly availability; undoing sets the previous one (or none)."""

    def __init__(
        self,
        ops: PlanOps,
        before: WeeklyAvailability | None,
        after: WeeklyAvailability,
    ) -> None:
        super().__init__(QCoreApplication.translate("PlanUndo", "Change training time"))
        self._ops = ops
        self._before = before
        self._after = after

    def redo(self) -> None:
        """Set the new availability."""
        self._ops.set_availability(self._after)

    def undo(self) -> None:
        """Set the previous availability."""
        self._ops.set_availability(self._before)


class SetChoicesCommand(QUndoCommand):
    """Change the session types; undoing sets the previous ones."""

    def __init__(self, ops: PlanOps, before: SessionChoices, after: SessionChoices) -> None:
        super().__init__(QCoreApplication.translate("PlanUndo", "Change session types"))
        self._ops = ops
        self._before = before
        self._after = after

    def redo(self) -> None:
        """Set the new choices."""
        self._ops.set_choices(self._after)

    def undo(self) -> None:
        """Set the previous choices."""
        self._ops.set_choices(self._before)
