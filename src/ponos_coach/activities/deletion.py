# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Use cases "delete activities" and "restore activities" (ADR 018).

Deleting takes an activity out of the index and records a tombstone (`DeletedActivity`) in a
single transaction: the load cache goes with the row (it is derived data) and the original file
stays in the archive. Nothing is asked: the interface offers an undo, which is `RestoreActivities`.
Restoring reads the archived files again with the decoder of the import and puts the activities back
as they were (origin, import instant, Garmin id); their tombstones go away in the same transaction.
A file that is gone or damaged leaves its activity deleted and does not stop the others; a failure
of the database restores nothing. A restored activity is no longer a tombstone, so the next
synchronization finds it already there.

Both are idempotent per identity: an identity that is not in the index (delete) or not deleted
(restore) is left out of the result. No log: identities and dates are personal data.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum

from ponos_coach.activities.decoding import ActivityFileError
from ponos_coach.activities.domain import (
    MAX_ACTIVITY_FILE_BYTES,
    ActivityIdentity,
    ActivityRecord,
    DeletedActivity,
    truncate_to_millisecond,
)
from ponos_coach.activities.ports import (
    ActivityArchive,
    ActivityIndex,
    ArchiveError,
    ArchiveFileMissingError,
    Clock,
    DeletedActivities,
)
from ponos_coach.activities.sync import ActivityDecoder


class DeleteActivities:
    """Delete activities: out of the index, into the tombstones, in one transaction."""

    def __init__(self, index: ActivityIndex, deleted: DeletedActivities, clock: Clock) -> None:
        self._index = index
        self._deleted = deleted
        self._clock = clock

    def __call__(self, identities: Sequence[ActivityIdentity]) -> tuple[DeletedActivity, ...]:
        """Deletes the activities and returns their tombstones, in the order given.

        An identity that is not in the index (already deleted) is skipped. All or none: a failure
        leaves every activity where it was. The original files stay in the archive.

        Raises:
            ActivityIndexError: the database refused the change (nothing changed).
            StorageBusyError: another process holds the database (nothing changed).
        """
        now = truncate_to_millisecond(self._clock.now())
        tombstones: list[DeletedActivity] = []
        with self._index.transaction():
            for identity in dict.fromkeys(identities):
                record = self._index.get(identity)
                if record is None:
                    continue
                tombstone = DeletedActivity.of(record, now)
                self._index.remove(identity)
                self._deleted.add(tombstone)
                tombstones.append(tombstone)
        return tuple(tombstones)


class RestoreStatus(Enum):
    """Outcome of restoring one activity."""

    RESTORED = "restored"
    FILE_MISSING = "file_missing"  # the original is no longer in the archive
    FILE_DAMAGED = "file_damaged"  # altered, unreadable, or not the deleted activity


@dataclass(frozen=True, slots=True)
class RestoreResult:
    """What happened to one activity of a restore."""

    identity: ActivityIdentity
    status: RestoreStatus


@dataclass(frozen=True, slots=True)
class RestoreSummary:
    """The outcomes of a restore, in the order given; identities that are not deleted are not
    in it."""

    results: tuple[RestoreResult, ...]

    @property
    def restored(self) -> tuple[ActivityIdentity, ...]:
        """The activities that are back in the index."""
        return tuple(r.identity for r in self.results if r.status is RestoreStatus.RESTORED)


class RestoreActivities:
    """Restore deleted activities from their archived files."""

    def __init__(
        self,
        index: ActivityIndex,
        deleted: DeletedActivities,
        archive: ActivityArchive,
        decode: ActivityDecoder,
    ) -> None:
        self._index = index
        self._deleted = deleted
        self._archive = archive
        self._decode = decode

    def __call__(self, identities: Sequence[ActivityIdentity]) -> RestoreSummary:
        """Restores the activities whose file is still good, all in one transaction.

        Every file is read and decoded before the transaction opens, so the database write lock
        lasts only as long as the change; only the small records are kept in between. An activity
        whose file is gone or damaged stays deleted and says why in the summary, and does not
        stop the others. All or none for the database: a failure leaves every activity deleted.

        Raises:
            ActivityIndexError: the database refused the change (nothing restored).
            StorageBusyError: another process holds the database (nothing restored).
        """
        results: list[RestoreResult] = []
        records: list[ActivityRecord] = []
        for identity in dict.fromkeys(identities):
            tombstone = self._deleted.get(identity)
            if tombstone is None:
                continue
            read = self._read_back(tombstone)
            if isinstance(read, RestoreStatus):
                results.append(RestoreResult(identity, read))
            else:
                records.append(read)
                results.append(RestoreResult(identity, RestoreStatus.RESTORED))
        if records:
            with self._index.transaction():
                for record in records:
                    self._deleted.remove(record.identity)
                    self._index.add(record)
        return RestoreSummary(tuple(results))

    def _read_back(self, tombstone: DeletedActivity) -> ActivityRecord | RestoreStatus:
        """The record the archived file gives back, or why it cannot."""
        try:
            content = self._archive.read(tombstone.archived, MAX_ACTIVITY_FILE_BYTES)
            activity = self._decode(content, tombstone.file_format)
            return tombstone.to_record(activity, tombstone.archived)
        except ArchiveFileMissingError:
            return RestoreStatus.FILE_MISSING
        except (ArchiveError, ActivityFileError, ValueError):
            # ArchiveIntegrityError, too large, unreadable, undecodable or another activity.
            return RestoreStatus.FILE_DAMAGED
