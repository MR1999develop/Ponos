# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Undoable commands on the injuries and illnesses.

Each command is a `QUndoCommand` that starts a job on the data thread and does not wait: jobs run in
order on that one thread, so a redo or an undo always lands after the one before it. The first
`redo` runs the use case; what it reports (`ProblemOpened`, `ProblemChanged`) stays in an
`OutcomeCell`, and from then on undo and redo put back exactly the record the use case saw
(`before`, `after`), never a guess. Opening is taken back by removing the record and redone by
putting the same record back. If a job is refused the window shows the message and clears the stack.
Health data: never in logs or error messages.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from typing import Protocol

from PySide6.QtCore import QCoreApplication
from PySide6.QtGui import QUndoCommand

from ponos_coach.injury.domain import CheckIn, Injury, InjuryId, Problem, problem_id
from ponos_coach.injury.use_cases import ProblemChanged, ProblemOpened
from ponos_coach.ui.plan_undo import OutcomeCell


class InjuryOps(Protocol):
    """The changes a command can start (implemented by `InjuryUi` over `InjuryLoader`)."""

    def open_problem(self, problem: Problem, cell: OutcomeCell[ProblemOpened]) -> bool:
        """Open an injury or report an illness."""
        ...

    def remove_problem(self, injury_id: InjuryId) -> bool:
        """Delete a record."""
        ...

    def restore_problem(self, problem: Problem) -> bool:
        """Put a record back exactly as it was."""
        ...

    def record_checkin(
        self, injury_id: InjuryId, checkin: CheckIn, cell: OutcomeCell[ProblemChanged]
    ) -> bool:
        """Save the day's answers."""
        ...

    def confirm_review(
        self, injury_id: InjuryId, day: date, cell: OutcomeCell[ProblemChanged]
    ) -> bool:
        """Say a doctor has seen the warning signs."""
        ...

    def close_problem(
        self, injury_id: InjuryId, day: date, cell: OutcomeCell[ProblemChanged]
    ) -> bool:
        """Close an injury or say an illness is over."""
        ...

    def restore_before(self, cell: OutcomeCell[ProblemChanged]) -> bool:
        """Put back the record as it was before the change."""
        ...

    def restore_after(self, cell: OutcomeCell[ProblemChanged]) -> bool:
        """Put back the record as the change left it."""
        ...


class OpenProblemCommand(QUndoCommand):
    """Report an injury or an illness; undoing deletes it, redoing puts the same record back."""

    def __init__(self, ops: InjuryOps, problem: Problem) -> None:
        text = (
            QCoreApplication.translate("InjuryUndo", "Report injury")
            if isinstance(problem, Injury)
            else QCoreApplication.translate("InjuryUndo", "Report illness")
        )
        super().__init__(text)
        self._ops = ops
        self._problem = problem
        self._cell: OutcomeCell[ProblemOpened] = OutcomeCell()
        self._opened = False

    def redo(self) -> None:
        """Open the record (the first time through the use case, then as it was)."""
        if self._opened:
            self._ops.restore_problem(self._problem)
        else:
            self._opened = True
            self._ops.open_problem(self._problem, self._cell)

    def undo(self) -> None:
        """Delete the record."""
        self._ops.remove_problem(problem_id(self._problem))


class _ChangeProblemCommand(QUndoCommand):
    """A change of a record that the use case reports as `ProblemChanged`."""

    def __init__(
        self, ops: InjuryOps, text: str, run: Callable[[OutcomeCell[ProblemChanged]], bool]
    ) -> None:
        super().__init__(text)
        self._ops = ops
        self._run = run
        self._cell: OutcomeCell[ProblemChanged] = OutcomeCell()
        self._ran = False

    def redo(self) -> None:
        """Run the change (the first time through the use case, then put back what it left)."""
        if self._ran:
            self._ops.restore_after(self._cell)
        else:
            self._ran = True
            self._run(self._cell)

    def undo(self) -> None:
        """Put back the record as it was before."""
        self._ops.restore_before(self._cell)


class CheckInCommand(_ChangeProblemCommand):
    """Save the day's check-in."""

    def __init__(self, ops: InjuryOps, injury_id: InjuryId, checkin: CheckIn) -> None:
        super().__init__(
            ops,
            QCoreApplication.translate("InjuryUndo", "Save check-in"),
            lambda cell: ops.record_checkin(injury_id, checkin, cell),
        )


class MedicalReviewCommand(_ChangeProblemCommand):
    """Say a doctor has seen the warning signs."""

    def __init__(self, ops: InjuryOps, injury_id: InjuryId, day: date) -> None:
        super().__init__(
            ops,
            QCoreApplication.translate("InjuryUndo", "Medical review done"),
            lambda cell: ops.confirm_review(injury_id, day, cell),
        )


class CloseCommand(_ChangeProblemCommand):
    """Close an injury or an illness."""

    def __init__(self, ops: InjuryOps, injury_id: InjuryId, day: date) -> None:
        super().__init__(
            ops,
            QCoreApplication.translate("InjuryUndo", "Close"),
            lambda cell: ops.close_problem(injury_id, day, cell),
        )
