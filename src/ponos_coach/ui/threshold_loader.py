# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Threshold test jobs on the data thread: evaluate an activity, apply a test, take it back, read
the history.

Every job builds its use cases inside the data thread with the open profile's connection and
hands the outcome back as a signal on the UI thread; the UI never reads the profile. An evaluation
superseded by another delivers nothing. Applying and taking back are jobs that run in order on the
one data thread, so a redo or an undo always lands after the one before it. Health data: never in
logs or error messages.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QObject, Signal

from ponos_coach.activities.domain import ActivityIdentity
from ponos_coach.assessment.domain import ThresholdTestResult
from ponos_coach.assessment.use_cases import EvaluateThresholdTest, ListThresholdTests, NextRetest
from ponos_coach.load.domain import physiology_for_day
from ponos_coach.profiles.measurements import UpdateMeasurements
from ponos_coach.profiles.threshold_tests import (
    AppliedThresholdTest,
    ApplyThresholdTest,
    RemoveThresholdTest,
)
from ponos_coach.ui.data_thread import JobFailed, JobOutcome, JobSucceeded
from ponos_coach.ui.threshold_presentation import (
    ActivityBrief,
    HistoryEntry,
    ThresholdHistory,
    ThresholdReview,
    ThresholdVerdict,
)
from ponos_coach.ui.workspace import ProfileAccess, WorkspaceController


class AppliedCell:
    """Where the data thread keeps what an apply did, for the job that takes it back.

    Only jobs on the data thread write and read it, one after the other, so it needs no lock. It
    is empty until the apply has run, and again if an apply failed.
    """

    def __init__(self) -> None:
        self.value: AppliedThresholdTest | None = None


@dataclass(frozen=True, slots=True)
class ThresholdApplied:
    """The first apply went through: the test and the cell that holds what to undo."""

    result: ThresholdTestResult
    cell: AppliedCell


def _evaluate(identity: ActivityIdentity, access: ProfileAccess) -> ThresholdVerdict:
    session = access.session
    evaluation = EvaluateThresholdTest(
        session.activities,
        access.archive,
        session.physiology,
        access.decode,
        timezone=access.zone,
    )(identity)
    if not isinstance(evaluation, ThresholdTestResult):
        return evaluation
    in_force = physiology_for_day(session.physiology.list_entries(), access.today())
    return ThresholdReview(evaluation, in_force.threshold.bpm)


def _apply(result: ThresholdTestResult, cell: AppliedCell, access: ProfileAccess) -> None:
    session = access.session
    cell.value = None
    measurements = UpdateMeasurements(
        session.activities,
        session.physiology,
        session.weights,
        access.today,
        session.fixed_data,
    )
    cell.value = ApplyThresholdTest(
        session.activities, session.threshold_tests, session.physiology, measurements
    )(result)


def _remove(cell: AppliedCell, access: ProfileAccess) -> None:
    applied = cell.value
    if applied is None:  # the apply before it failed: there is nothing to take back
        return
    session = access.session
    RemoveThresholdTest(session.activities, session.threshold_tests, session.physiology)(applied)
    cell.value = None


def _history(access: ProfileAccess) -> ThresholdHistory:
    session = access.session
    entries = []
    for test in ListThresholdTests(session.threshold_tests)():
        record = session.activities.get(test.identity)
        brief = None if record is None else ActivityBrief(record.sport, record.start)
        entries.append(HistoryEntry(test, brief))
    retest = NextRetest(session.threshold_tests, access.today)()
    return ThresholdHistory(tuple(entries), retest)


class ThresholdLoader(QObject):
    """Runs the threshold test jobs; outcomes arrive as signals on the UI thread."""

    evaluated = Signal(object)  # ThresholdVerdict
    applied = Signal(object)  # ThresholdApplied: the first apply of a test went through
    apply_failed = Signal(object)  # ErrorMessage: the first apply did not go through
    changed = Signal()  # an apply, redo or undo went through: the data changed
    command_failed = Signal(object)  # ErrorMessage: a redo or an undo did not go through
    history_loaded = Signal(object)  # ThresholdHistory
    failed = Signal(object)  # ErrorMessage: a read failed

    def __init__(self, controller: WorkspaceController, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._controller = controller
        self._evaluation = 0

    # --- Evaluate -------------------------------------------------------------------------

    def evaluate(self, identity: ActivityIdentity) -> bool:
        """Start evaluating an activity as the test. False if no profile is open."""
        self._evaluation += 1
        request = self._evaluation

        def work(access: ProfileAccess) -> ThresholdVerdict:
            return _evaluate(identity, access)

        def done(outcome: JobOutcome) -> None:
            if request != self._evaluation:
                return
            if isinstance(outcome, JobSucceeded):
                self.evaluated.emit(outcome.value)
            elif isinstance(outcome, JobFailed):
                self.failed.emit(self._controller.describe_failure(outcome.error))

        return self._controller.run_on_profile(work, done)

    def cancel_evaluation(self) -> None:
        """The dialog was closed: the incoming outcome no longer matters."""
        self._evaluation += 1

    # --- Apply and take back ----------------------------------------------------------------

    def apply(self, result: ThresholdTestResult) -> bool:
        """Apply an accepted test. False if no profile is open."""
        cell = AppliedCell()

        def work(access: ProfileAccess) -> None:
            _apply(result, cell, access)

        def done(outcome: JobOutcome) -> None:
            if isinstance(outcome, JobSucceeded):
                self.applied.emit(ThresholdApplied(result, cell))
                self.changed.emit()
            elif isinstance(outcome, JobFailed):
                self.apply_failed.emit(self._controller.describe_failure(outcome.error))

        return self._controller.run_on_profile(work, done)

    def reapply(self, result: ThresholdTestResult, cell: AppliedCell) -> bool:
        """Apply a test again (redo); `cell` receives what is needed to take it back again."""

        def work(access: ProfileAccess) -> None:
            _apply(result, cell, access)

        return self._controller.run_on_profile(work, self._after_command)

    def remove(self, cell: AppliedCell) -> bool:
        """Take an applied test back (undo)."""

        def work(access: ProfileAccess) -> None:
            _remove(cell, access)

        return self._controller.run_on_profile(work, self._after_command)

    def _after_command(self, outcome: JobOutcome) -> None:
        if isinstance(outcome, JobSucceeded):
            self.changed.emit()
        elif isinstance(outcome, JobFailed):
            self.command_failed.emit(self._controller.describe_failure(outcome.error))

    # --- History ----------------------------------------------------------------------------

    def load_history(self) -> bool:
        """Read the tests and the retest status. False if no profile is open."""

        def done(outcome: JobOutcome) -> None:
            if isinstance(outcome, JobSucceeded) and isinstance(outcome.value, ThresholdHistory):
                self.history_loaded.emit(outcome.value)
            elif isinstance(outcome, JobFailed):
                self.failed.emit(self._controller.describe_failure(outcome.error))

        return self._controller.run_on_profile(_history, done)
