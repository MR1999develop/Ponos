# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Undoable commands: delete activities and restore them.

Each command is a `QUndoCommand` that starts a job on the data thread and does not wait: jobs run in
order on that one thread, so an undo or a redo always lands after the job before it. Pushing a
command runs it (the first `redo` is the change itself). What a change did stays in an
`OutcomeCell`, so the undo acts on exactly that: undoing a deletion restores the activities the
deletion really took out, undoing a restore deletes again those that really came back. Deleting
and restoring are idempotent, so a redo after an undo is the same call again. If a job cannot even
start (no profile, window closing) the command calls `on_refused`, so the stack never moves ahead
of the data.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Protocol

from PySide6.QtCore import QCoreApplication
from PySide6.QtGui import QUndoCommand

from ponos_coach.activities.deletion import RestoreSummary
from ponos_coach.activities.domain import ActivityIdentity, DeletedActivity
from ponos_coach.ui.plan_undo import OutcomeCell


class DeletionOps(Protocol):
    """The jobs a command can start (implemented by `ActivityDeletionLoader`)."""

    def delete(
        self,
        identities: Sequence[ActivityIdentity],
        cell: OutcomeCell[tuple[DeletedActivity, ...]],
    ) -> bool:
        """Delete the activities."""
        ...

    def restore_deleted_by(self, cell: OutcomeCell[tuple[DeletedActivity, ...]]) -> bool:
        """Restore what a deletion deleted."""
        ...

    def restore(
        self, identities: Sequence[ActivityIdentity], cell: OutcomeCell[RestoreSummary]
    ) -> bool:
        """Restore the activities."""
        ...

    def delete_restored_by(self, cell: OutcomeCell[RestoreSummary]) -> bool:
        """Delete again what a restore brought back."""
        ...


class DeleteActivitiesCommand(QUndoCommand):
    """Delete activities; undoing restores them from their archived files."""

    def __init__(
        self,
        ops: DeletionOps,
        identities: Sequence[ActivityIdentity],
        on_refused: Callable[[], None],
    ) -> None:
        count = len(identities)
        super().__init__(
            QCoreApplication.translate("ActivityDeletionUndo", "Delete activity")
            if count <= 1
            else QCoreApplication.translate(
                "ActivityDeletionUndo", "Delete %n activities", "", count
            )
        )
        self._ops = ops
        self._identities = tuple(identities)
        self._on_refused = on_refused
        self._cell: OutcomeCell[tuple[DeletedActivity, ...]] = OutcomeCell()

    def redo(self) -> None:
        """Delete the activities (the first time, this is the change itself)."""
        if not self._ops.delete(self._identities, self._cell):
            self._on_refused()

    def undo(self) -> None:
        """Bring back what the deletion took out."""
        if not self._ops.restore_deleted_by(self._cell):
            self._on_refused()


class RestoreActivitiesCommand(QUndoCommand):
    """Restore deleted activities; undoing deletes again those that came back."""

    def __init__(
        self,
        ops: DeletionOps,
        identities: Sequence[ActivityIdentity],
        on_refused: Callable[[], None],
    ) -> None:
        count = len(identities)
        super().__init__(
            QCoreApplication.translate("ActivityDeletionUndo", "Restore activity")
            if count <= 1
            else QCoreApplication.translate(
                "ActivityDeletionUndo", "Restore %n activities", "", count
            )
        )
        self._ops = ops
        self._identities = tuple(identities)
        self._on_refused = on_refused
        self._cell: OutcomeCell[RestoreSummary] = OutcomeCell()

    def redo(self) -> None:
        """Restore the activities (the first time, this is the change itself)."""
        if not self._ops.restore(self._identities, self._cell):
            self._on_refused()

    def undo(self) -> None:
        """Delete again the activities that came back."""
        if not self._ops.delete_restored_by(self._cell):
            self._on_refused()
