# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""The undoable "Apply threshold test" command.

The command is created when the first apply has already gone through (the dialog applied it and
only then is the command pushed), so the first `redo` does nothing. Undoing takes the test back;
redoing applies it again. Both start a job on the data thread and do not wait: jobs run in order
on that one thread, so an undo always lands after the apply before it. If a job is refused (the
measurement was edited meanwhile) the window shows the message and clears the stack.
"""

from __future__ import annotations

from typing import Protocol

from PySide6.QtCore import QCoreApplication
from PySide6.QtGui import QUndoCommand

from ponos_coach.assessment.domain import ThresholdTestResult
from ponos_coach.ui.threshold_loader import AppliedCell


class ThresholdOps(Protocol):
    """The jobs a command can start (implemented by `ThresholdLoader`)."""

    def reapply(self, result: ThresholdTestResult, cell: AppliedCell) -> bool:
        """Apply the test again."""
        ...

    def remove(self, cell: AppliedCell) -> bool:
        """Take the test back."""
        ...


class ApplyThresholdTestCommand(QUndoCommand):
    """Apply a threshold test; undoing takes it back."""

    def __init__(self, ops: ThresholdOps, result: ThresholdTestResult, cell: AppliedCell) -> None:
        super().__init__(QCoreApplication.translate("ThresholdUndo", "Apply threshold test"))
        self._ops = ops
        self._result = result
        self._cell = cell
        self._already_applied = True

    def redo(self) -> None:
        """Apply the test (the first time it is already applied: nothing to do)."""
        if self._already_applied:
            self._already_applied = False
            return
        self._ops.reapply(self._result, self._cell)

    def undo(self) -> None:
        """Take the test back."""
        self._ops.remove(self._cell)
