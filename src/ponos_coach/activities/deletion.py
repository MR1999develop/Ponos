# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Use cases "delete activities" and "restore activities" (ADR 018).

Deleting takes an activity out of the index and records a tombstone (`DeletedActivity`) in a
single transaction: the load cache goes with the row (it is derived data) and the original file
stays in the archive. Nothing is asked: the interface offers an undo, which is `RestoreActivities`.
Restoring reads the archived file again with the decoder of the import and puts the activity back as
it was (origin, import instant, Garmin id); the tombstone goes away in the same transaction. A
restored activity is no longer a tombstone, so the next synchronization finds it already there.

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
        """Restores the activities, each in its own transaction (a damaged file does not stop the
        others).

        The file is read and decoded before the transaction opens, so the database write lock lasts
        only as long as the change. An activity whose file is gone or damaged stays deleted and
        says why in the summary.

        Raises:
            ActivityIndexError: the database refused a change.
            StorageBusyError: another process holds the database.
        """
        results: list[RestoreResult] = []
        for identity in dict.fromkeys(identities):
            tombstone = self._deleted.get(identity)
            if tombstone is not None:
                results.append(RestoreResult(identity, self._restore(tombstone)))
        return RestoreSummary(tuple(results))

    def _restore(self, tombstone: DeletedActivity) -> RestoreStatus:
        try:
            content = self._archive.read(tombstone.archived, MAX_ACTIVITY_FILE_BYTES)
            activity = self._decode(content, tombstone.file_format)
            record = tombstone.to_record(activity, tombstone.archived)
        except ArchiveFileMissingError:
            return RestoreStatus.FILE_MISSING
        except (ArchiveError, ActivityFileError, ValueError):
            # ArchiveIntegrityError, too large, unreadable, undecodable or another activity.
            return RestoreStatus.FILE_DAMAGED
        with self._index.transaction():
            self._deleted.remove(tombstone.identity)
            self._index.add(record)
        return RestoreStatus.RESTORED
