# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""The injuries and illnesses: jobs on the data thread.

Every job builds its use cases inside the data thread with the open profile's connection and hands
the outcome back as a signal on the UI thread; the UI never reads the profile. A change saves and
then re-reads the whole list, so the interface always shows what is really stored. Jobs run in order
on the one data thread, so a redo or an undo lands after the change before it. What a change
reported (`ProblemOpened`, `ProblemChanged`, `ProblemRemoved`) is left in an `OutcomeCell`, so an
undo puts back exactly what the use case saw. Health data: never in logs or error messages.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

from PySide6.QtCore import QObject, Signal

from ponos_coach.injury.domain import CheckIn, Injury, InjuryId, Problem
from ponos_coach.injury.use_cases import (
    CloseProblem,
    ConfirmMedicalReview,
    InjuryView,
    ListInjuries,
    OpenInjury,
    ProblemChanged,
    ProblemOpened,
    RecordCheckIn,
    RemoveProblem,
    ReportIllness,
    RestoreProblem,
)
from ponos_coach.ui.data_thread import JobFailed, JobOutcome, JobSucceeded
from ponos_coach.ui.plan_undo import OutcomeCell
from ponos_coach.ui.workspace import ProfileAccess, WorkspaceController


@dataclass(frozen=True, slots=True)
class InjuryList:
    """The records with where each stands, and the day they were read for."""

    views: tuple[InjuryView, ...]
    today: date


def _read(access: ProfileAccess) -> InjuryList:
    today = access.today()
    return InjuryList(ListInjuries(access.session.injuries)(today), today)


class InjuryLoader(QObject):
    """Runs the jobs on the injuries; outcomes arrive as signals on the UI thread."""

    loaded = Signal(object)  # InjuryList: a plain read
    changed = Signal(object)  # InjuryList: re-read after a change that went through
    command_failed = Signal(object)  # ErrorMessage: a change did not go through, nothing changed
    failed = Signal(object)  # ErrorMessage: a read failed

    def __init__(self, controller: WorkspaceController, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._controller = controller

    def load(self) -> bool:
        """Read the records. False if no profile is open."""

        def done(outcome: JobOutcome) -> None:
            if isinstance(outcome, JobSucceeded) and isinstance(outcome.value, InjuryList):
                self.loaded.emit(outcome.value)
            elif isinstance(outcome, JobFailed):
                self.failed.emit(self._controller.describe_failure(outcome.error))

        return self._controller.run_on_profile(_read, done)

    # --- Changing --------------------------------------------------------------------------

    def open_problem(self, problem: Problem, cell: OutcomeCell[ProblemOpened]) -> bool:
        """Open an injury, or report an illness; `cell` receives what was opened."""

        def change(access: ProfileAccess) -> None:
            cell.value = None
            session = access.session
            if isinstance(problem, Injury):
                cell.value = OpenInjury(session.injuries)(problem)
            else:
                cell.value = ReportIllness(session.activities, session.injuries)(problem)

        return self._change(change)

    def record_checkin(
        self, injury_id: InjuryId, checkin: CheckIn, cell: OutcomeCell[ProblemChanged]
    ) -> bool:
        """Save the day's answers; `cell` receives the injury before and after."""

        def change(access: ProfileAccess) -> None:
            cell.value = None
            session = access.session
            cell.value = RecordCheckIn(session.activities, session.injuries)(injury_id, checkin)

        return self._change(change)

    def confirm_review(
        self, injury_id: InjuryId, day: date, cell: OutcomeCell[ProblemChanged]
    ) -> bool:
        """Say a doctor has seen the warning signs; `cell` receives the injury before and after."""

        def change(access: ProfileAccess) -> None:
            cell.value = None
            session = access.session
            cell.value = ConfirmMedicalReview(session.activities, session.injuries)(injury_id, day)

        return self._change(change)

    def close_problem(
        self, injury_id: InjuryId, day: date, cell: OutcomeCell[ProblemChanged]
    ) -> bool:
        """Close an injury or say an illness is over; `cell` receives the record before/after."""

        def change(access: ProfileAccess) -> None:
            cell.value = None
            session = access.session
            cell.value = CloseProblem(session.activities, session.injuries)(injury_id, day)

        return self._change(change)

    def remove_problem(self, injury_id: InjuryId) -> bool:
        """Delete a record (the undo of opening one)."""
        return self._change(
            lambda access: RemoveProblem(access.session.activities, access.session.injuries)(
                injury_id
            )
        )

    def restore_problem(self, problem: Problem) -> bool:
        """Put a record back exactly as it was (the redo of opening one)."""
        return self._change(
            lambda access: RestoreProblem(access.session.activities, access.session.injuries)(
                problem
            )
        )

    def restore_before(self, cell: OutcomeCell[ProblemChanged]) -> bool:
        """Put back the record as it was before the change that `cell` holds (nothing if the change
        was refused)."""
        return self._restore_from(cell, lambda changed: changed.before)

    def restore_after(self, cell: OutcomeCell[ProblemChanged]) -> bool:
        """Put back the record as the change that `cell` holds left it (the redo)."""
        return self._restore_from(cell, lambda changed: changed.after)

    def _restore_from(
        self, cell: OutcomeCell[ProblemChanged], pick: Callable[[ProblemChanged], Problem]
    ) -> bool:
        def change(access: ProfileAccess) -> None:
            changed = cell.value
            if changed is not None:
                RestoreProblem(access.session.activities, access.session.injuries)(pick(changed))

        return self._change(change)

    def _change(self, change: Callable[[ProfileAccess], object]) -> bool:
        """Save a change and re-read the records, all in one job."""

        def work(access: ProfileAccess) -> InjuryList:
            change(access)
            return _read(access)

        def done(outcome: JobOutcome) -> None:
            if isinstance(outcome, JobSucceeded) and isinstance(outcome.value, InjuryList):
                self.changed.emit(outcome.value)
            elif isinstance(outcome, JobFailed):
                self.command_failed.emit(self._controller.describe_failure(outcome.error))

        return self._controller.run_on_profile(work, done)
