# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""A deleted activity (the "tombstone"): what remains of an activity the user took out of the list.

The original file stays in the archive; the tombstone keeps the identity (the FIT `file_id`, plus
the Garmin id if the activity had one) so that no synchronization and no folder import brings the
activity back, and what the "Deleted activities" list shows (sport, start, duration, distance) so
that list never reads a file. It keeps no position and no heart rate. Restoring an activity reads
the archived file again.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from ponos_coach.activities.domain.activity import Activity
from ponos_coach.activities.domain.duplicates import is_possible_duplicate
from ponos_coach.activities.domain.identity import ActivityIdentity, FileFormat, identity_key
from ponos_coach.activities.domain.record import ActivityOrigin, ActivityRecord, ArchivedFile
from ponos_coach.activities.domain.remote import RemoteActivity
from ponos_coach.activities.domain.sport import Sport
from ponos_coach.activities.domain.timing import check_utc


@dataclass(frozen=True, slots=True)
class DeletedActivity:
    """An activity deleted by the user.

    Invariants: start, `imported_at` and `deleted_at` are UTC, the last to the millisecond; the
    elapsed time is finite and positive; the distance, if there is one, is finite and not
    negative; the Garmin id, if there is one, is positive and an activity with origin `GARMIN` has
    it. `origin`, `imported_at` and the Garmin id are those of the record that was deleted, so a
    restored activity is as it was.
    """

    identity: ActivityIdentity
    file_format: FileFormat
    sport: Sport
    start: datetime
    elapsed_s: float
    distance_m: float | None
    archived: ArchivedFile
    origin: ActivityOrigin
    imported_at: datetime
    garmin_activity_id: int | None
    deleted_at: datetime

    def __post_init__(self) -> None:
        for moment in (self.start, self.imported_at, self.deleted_at):
            check_utc(moment)
        if self.deleted_at.microsecond % 1000:
            raise ValueError("the deletion instant must be at millisecond precision")
        if not math.isfinite(self.elapsed_s) or self.elapsed_s <= 0:
            raise ValueError("the elapsed time must be finite and positive")
        if self.distance_m is not None and (
            not math.isfinite(self.distance_m) or self.distance_m < 0
        ):
            raise ValueError("the distance must be finite and not negative")
        if self.garmin_activity_id is not None and self.garmin_activity_id <= 0:
            raise ValueError("the Garmin id must be positive")
        if self.origin is ActivityOrigin.GARMIN and self.garmin_activity_id is None:
            raise ValueError("an activity downloaded from Garmin has its Garmin id")

    @classmethod
    def of(cls, record: ActivityRecord, deleted_at: datetime) -> DeletedActivity:
        """The tombstone of an index record deleted at `deleted_at` (UTC, to the millisecond)."""
        return cls(
            record.identity,
            record.file_format,
            record.sport,
            record.start,
            record.elapsed_s,
            record.summary.distance_m,
            record.archived,
            record.origin,
            record.imported_at,
            record.garmin_activity_id,
            deleted_at,
        )

    def to_record(self, activity: Activity, archived: ArchivedFile) -> ActivityRecord:
        """The index record of the restored activity: read again from its file (`activity`, whose
        identity must be this one) and as it was before the deletion (origin, import instant and
        Garmin id of the tombstone).

        Raises:
            ValueError: `activity` is not the one that was deleted.
        """
        if activity.identity != self.identity:
            raise ValueError("the file is not the deleted activity")
        return ActivityRecord.from_activity(
            activity, archived, self.origin, self.imported_at, self.garmin_activity_id
        )


def find_matching_deleted(
    remote: RemoteActivity, candidates: Sequence[DeletedActivity]
) -> DeletedActivity | None:
    """The deleted activity that the remote one is, or None.

    A tombstone with the same Garmin id is it, whatever the times. Otherwise, a tombstone that has
    no Garmin id (it was imported from a file) is it if it has the same sport and
    `is_possible_duplicate` (starts within 60 s, durations within 5%): the same rule that matches
    a remote activity to an imported one. A tombstone with another Garmin id is never it. With
    several candidates the closest in start wins, then the smallest identity key: deterministic.
    `candidates` are usually `DeletedActivities.find_possible_duplicates`.
    """
    by_id = [c for c in candidates if c.garmin_activity_id == remote.garmin_id.value]
    matching = by_id or [
        c
        for c in candidates
        if c.garmin_activity_id is None
        and c.sport is remote.sport
        and is_possible_duplicate(remote.start, remote.duration_s, c.start, c.elapsed_s)
    ]
    if not matching:
        return None
    return min(matching, key=lambda c: (abs(c.start - remote.start), identity_key(c.identity)))
