# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Undoable changes of the goals, the availability and the session types.

Each command is a `QUndoCommand` that starts the job on the data thread and does not wait: jobs run
in order on that one thread, so a redo or an undo always lands after the one before it. Undoing
puts back exactly what the use case reported when the change ran (`before` of `GoalChanged`,
`AvailabilityChanged` and `SessionChoicesChanged`; the goal of `GoalRemoved`), not what the dialog
saw when it opened: a goal changed meanwhile by something else goes back to its real old version,
and an availability that was never set returns to "not set". The data thread leaves that outcome in
an `OutcomeCell` that the command hands to the job (as `LeanMassCell` does for the lean mass). If a
job is refused the window shows the message and clears the stack.
"""

from __future__ import annotations

from typing import Protocol

from PySide6.QtCore import QCoreApplication
from PySide6.QtGui import QUndoCommand

from ponos_coach.goals.domain import GoalId, SessionChoices, WeeklyAvailability
from ponos_coach.goals.ports import Goal
from ponos_coach.goals.use_cases import (
    AvailabilityChanged,
    GoalChanged,
    GoalRemoved,
    SessionChoicesChanged,
)


class OutcomeCell[T]:
    """Where the data thread keeps what the last run of a change reported, for the job that takes
    it back.

    Only jobs on the data thread write and read it, one after the other, so it needs no lock. It is
    empty until the change has run, and again if the change was refused.
    """

    def __init__(self) -> None:
        self.value: T | None = None


class PlanOps(Protocol):
    """The changes a command can start (implemented by `PlanLoader`)."""

    def add_goal(self, goal: Goal) -> bool:
        """Add a goal."""
        ...

    def change_goal(self, goal: Goal, cell: OutcomeCell[GoalChanged]) -> bool:
        """Replace the goal with the same id; `cell` receives the old version."""
        ...

    def restore_goal(self, cell: OutcomeCell[GoalChanged]) -> bool:
        """Put back the version that `cell` holds; nothing if the change was refused."""
        ...

    def remove_goal(self, goal_id: GoalId, cell: OutcomeCell[GoalRemoved]) -> bool:
        """Remove a goal; `cell` receives it."""
        ...

    def restore_removed_goal(self, cell: OutcomeCell[GoalRemoved]) -> bool:
        """Add back the goal that `cell` holds; nothing if the removal was refused."""
        ...

    def set_availability(
        self, availability: WeeklyAvailability | None, cell: OutcomeCell[AvailabilityChanged]
    ) -> bool:
        """Set the weekly availability; `cell` receives the previous one."""
        ...

    def restore_availability(self, cell: OutcomeCell[AvailabilityChanged]) -> bool:
        """Set the availability that `cell` holds as the previous one."""
        ...

    def set_choices(
        self, choices: SessionChoices, cell: OutcomeCell[SessionChoicesChanged]
    ) -> bool:
        """Set the session types; `cell` receives the previous ones."""
        ...

    def restore_choices(self, cell: OutcomeCell[SessionChoicesChanged]) -> bool:
        """Set the session types that `cell` holds as the previous ones."""
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
        self._ops.remove_goal(self._goal.goal_id, OutcomeCell())


class ChangeGoalCommand(QUndoCommand):
    """Change a goal; undoing puts back the version the change really replaced."""

    def __init__(self, ops: PlanOps, after: Goal) -> None:
        super().__init__(QCoreApplication.translate("PlanUndo", "Change goal"))
        self._ops = ops
        self._after = after
        self._cell: OutcomeCell[GoalChanged] = OutcomeCell()

    def redo(self) -> None:
        """Save the new version."""
        self._ops.change_goal(self._after, self._cell)

    def undo(self) -> None:
        """Save the old version again."""
        self._ops.restore_goal(self._cell)


class RemoveGoalCommand(QUndoCommand):
    """Remove a goal; undoing adds back the goal the removal really took."""

    def __init__(self, ops: PlanOps, goal_id: GoalId) -> None:
        super().__init__(QCoreApplication.translate("PlanUndo", "Remove goal"))
        self._ops = ops
        self._goal_id = goal_id
        self._cell: OutcomeCell[GoalRemoved] = OutcomeCell()

    def redo(self) -> None:
        """Remove the goal."""
        self._ops.remove_goal(self._goal_id, self._cell)

    def undo(self) -> None:
        """Add the goal back."""
        self._ops.restore_removed_goal(self._cell)


class SetAvailabilityCommand(QUndoCommand):
    """Change the weekly availability; undoing sets the one it really replaced (or none)."""

    def __init__(self, ops: PlanOps, after: WeeklyAvailability) -> None:
        super().__init__(QCoreApplication.translate("PlanUndo", "Change training time"))
        self._ops = ops
        self._after = after
        self._cell: OutcomeCell[AvailabilityChanged] = OutcomeCell()

    def redo(self) -> None:
        """Set the new availability."""
        self._ops.set_availability(self._after, self._cell)

    def undo(self) -> None:
        """Set the previous availability."""
        self._ops.restore_availability(self._cell)


class SetChoicesCommand(QUndoCommand):
    """Change the session types; undoing sets the ones it really replaced."""

    def __init__(self, ops: PlanOps, after: SessionChoices) -> None:
        super().__init__(QCoreApplication.translate("PlanUndo", "Change session types"))
        self._ops = ops
        self._after = after
        self._cell: OutcomeCell[SessionChoicesChanged] = OutcomeCell()

    def redo(self) -> None:
        """Set the new choices."""
        self._ops.set_choices(self._after, self._cell)

    def undo(self) -> None:
        """Set the previous choices."""
        self._ops.restore_choices(self._cell)
