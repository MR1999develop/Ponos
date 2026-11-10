# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Deleting and restoring activities: jobs on the data thread.

Every job builds its use case inside the data thread with the open profile's connection and hands
the outcome back as a signal on the UI thread; the UI never reads the profile. A change saves and
then reads the list of deleted activities again, so what the window shows is what is stored. Jobs
run in order on the one data thread, so an undo always lands after the change before it. What a
change did (the tombstones it wrote, the activities it restored) is left in an `OutcomeCell`, so the
command that takes it back acts on exactly that. No logs: identities and dates are personal data.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from PySide6.QtCore import QObject, Signal

from ponos_coach.activities.deletion import (
    DeleteActivities,
    RestoreActivities,
    RestoreStatus,
    RestoreSummary,
)
from ponos_coach.activities.domain import ActivityIdentity, DeletedActivity
from ponos_coach.ui.data_thread import JobFailed, JobOutcome, JobSucceeded
from ponos_coach.ui.plan_undo import OutcomeCell
from ponos_coach.ui.workspace import ProfileAccess, WorkspaceController


@dataclass(frozen=True, slots=True)
class DeletedList:
    """The deleted activities, the most recently deleted first."""

    deleted: tuple[DeletedActivity, ...]


@dataclass(frozen=True, slots=True)
class ActivityChange:
    """What a change did and the deleted activities after it.

    `deleted` are the tombstones the change wrote (their titles go in the status message);
    `restored` how many activities came back; `not_restored` how many could not (their file is
    missing or damaged: they stay deleted).
    """

    deleted: tuple[DeletedActivity, ...]
    restored: int
    not_restored: int
    after: DeletedList


def _read(access: ProfileAccess) -> DeletedList:
    return DeletedList(tuple(access.session.deleted_activities.list_latest_first()))


class ActivityDeletionLoader(QObject):
    """Runs the jobs on the deleted activities; outcomes arrive as signals on the UI thread."""

    loaded = Signal(object)  # DeletedList: a plain read
    changed = Signal(object)  # ActivityChange: a change went through, the list read again
    command_failed = Signal(object)  # ErrorMessage: a change did not go through, nothing changed
    failed = Signal(object)  # ErrorMessage: a read failed

    def __init__(self, controller: WorkspaceController, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._controller = controller

    def load(self) -> bool:
        """Read the deleted activities. False if no profile is open."""

        def done(outcome: JobOutcome) -> None:
            if isinstance(outcome, JobSucceeded) and isinstance(outcome.value, DeletedList):
                self.loaded.emit(outcome.value)
            elif isinstance(outcome, JobFailed):
                self.failed.emit(self._controller.describe_failure(outcome.error))

        return self._controller.run_on_profile(_read, done)

    # --- Changing --------------------------------------------------------------------------

    def delete(
        self,
        identities: Sequence[ActivityIdentity],
        cell: OutcomeCell[tuple[DeletedActivity, ...]],
    ) -> bool:
        """Delete the activities; `cell` receives the tombstones that were written."""

        def change(access: ProfileAccess) -> tuple[tuple[DeletedActivity, ...], int, int]:
            cell.value = None
            deleted = _delete(access, identities)
            cell.value = deleted
            return deleted, 0, 0

        return self._change(change)

    def restore_deleted_by(self, cell: OutcomeCell[tuple[DeletedActivity, ...]]) -> bool:
        """Restore what the delete that `cell` holds deleted (the undo of a deletion)."""

        def change(access: ProfileAccess) -> tuple[tuple[DeletedActivity, ...], int, int]:
            deleted = cell.value or ()
            summary = _restore(access, [d.identity for d in deleted])
            return (), *_counts(summary)

        return self._change(change)

    def restore(
        self, identities: Sequence[ActivityIdentity], cell: OutcomeCell[RestoreSummary]
    ) -> bool:
        """Restore the activities; `cell` receives what happened to each."""

        def change(access: ProfileAccess) -> tuple[tuple[DeletedActivity, ...], int, int]:
            cell.value = None
            summary = _restore(access, identities)
            cell.value = summary
            return (), *_counts(summary)

        return self._change(change)

    def delete_restored_by(self, cell: OutcomeCell[RestoreSummary]) -> bool:
        """Delete again what the restore that `cell` holds brought back (its undo)."""

        def change(access: ProfileAccess) -> tuple[tuple[DeletedActivity, ...], int, int]:
            summary = cell.value
            return _delete(access, summary.restored if summary else ()), 0, 0

        return self._change(change)

    def _change(
        self, change: Callable[[ProfileAccess], tuple[tuple[DeletedActivity, ...], int, int]]
    ) -> bool:
        """Save a change and read the deleted activities again, all in one job."""

        def work(access: ProfileAccess) -> ActivityChange:
            deleted, restored, not_restored = change(access)
            return ActivityChange(deleted, restored, not_restored, _read(access))

        def done(outcome: JobOutcome) -> None:
            if isinstance(outcome, JobSucceeded) and isinstance(outcome.value, ActivityChange):
                self.changed.emit(outcome.value)
            elif isinstance(outcome, JobFailed):
                self.command_failed.emit(self._controller.describe_failure(outcome.error))

        return self._controller.run_on_profile(work, done)


def _delete(
    access: ProfileAccess, identities: Sequence[ActivityIdentity]
) -> tuple[DeletedActivity, ...]:
    session = access.session
    return DeleteActivities(session.activities, session.deleted_activities, access.clock)(
        identities
    )


def _restore(access: ProfileAccess, identities: Sequence[ActivityIdentity]) -> RestoreSummary:
    session = access.session
    return RestoreActivities(
        session.activities, session.deleted_activities, access.archive, access.decode
    )(identities)


def _counts(summary: RestoreSummary) -> tuple[int, int]:
    restored = len(summary.restored)
    failed = sum(1 for r in summary.results if r.status is not RestoreStatus.RESTORED)
    return restored, failed
