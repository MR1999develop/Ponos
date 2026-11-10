# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Builders of domain values for the tests (activities, records, parameters, profiles)."""

from __future__ import annotations

import hashlib
import tempfile
from datetime import UTC, date, datetime
from pathlib import Path, PurePosixPath

from ponos_coach.activities.domain import (
    ActivityIdentity,
    ActivityOrigin,
    ActivityRecord,
    ActivitySummary,
    ArchivedFile,
    DeletedActivity,
    FileId,
    FitIdentity,
    GarminActivityId,
    RemoteActivity,
    Sport,
    format_of,
)
from ponos_coach.load.domain import (
    HeartRateLimits,
    Physiology,
    PhysiologyEntry,
    Sex,
    ThresholdHeartRate,
)
from ponos_coach.profiles.domain import ProfileId, ProfileName, RegistryEntry

T0 = datetime(2026, 3, 2, 7, 0, tzinfo=UTC)
IMPORTED_AT = datetime(2026, 4, 1, 12, 0, tzinfo=UTC)


def fit_identity(number: int = 1) -> FitIdentity:
    """Distinct FIT identity for each `number` (serial number)."""
    return FitIdentity(FileId(255, 1, number, T0))


def archived_file(identity: ActivityIdentity) -> ArchivedFile:
    """Plausible archive reference for the identity (there is no real file)."""
    digest = hashlib.sha256(f"file-{identity}".encode()).hexdigest()
    extension = format_of(identity).value
    return ArchivedFile(PurePosixPath("2026") / f"{digest}.{extension}", digest, 1234)


def make_record(
    identity: ActivityIdentity | None = None,
    *,
    start: datetime = T0,
    elapsed_s: float = 3600.0,
    timer_s: float | None = None,
    sport: Sport = Sport.RUNNING,
    summary: ActivitySummary | None = None,
    origin: ActivityOrigin = ActivityOrigin.IMPORT,
    garmin_activity_id: int | None = None,
) -> ActivityRecord:
    """Index record with default values; every argument can be changed."""
    identity = identity or fit_identity()
    return ActivityRecord(
        identity=identity,
        file_format=format_of(identity),
        sport=sport,
        start=start,
        elapsed_s=elapsed_s,
        timer_s=elapsed_s if timer_s is None else timer_s,
        summary=summary or ActivitySummary(distance_m=10_000.0),
        archived=archived_file(identity),
        origin=origin,
        imported_at=IMPORTED_AT,
        garmin_activity_id=garmin_activity_id,
    )


DELETED_AT = datetime(2026, 4, 2, 9, 30, tzinfo=UTC)


def make_deleted(
    record: ActivityRecord | None = None, *, at: datetime = DELETED_AT
) -> DeletedActivity:
    """Tombstone of `record` (a default one if none is given) deleted at `at`."""
    return DeletedActivity.of(record or make_record(), at)


def physiology(threshold_bpm: int = 170) -> Physiology:
    """Example parameters: HR 50-190, variable threshold, male."""
    return Physiology(HeartRateLimits(50, 190), ThresholdHeartRate(threshold_bpm), Sex.MALE)


def physiology_entry(valid_from: date, threshold_bpm: int = 170) -> PhysiologyEntry:
    """Valid physiological parameters entry from a date."""
    return PhysiologyEntry(valid_from, physiology(threshold_bpm))


def absolute_path(*parts: str) -> Path:
    """Absolute path under the temporary folder, without touching the disk."""
    return Path(tempfile.gettempdir(), "ponos-test-paths", *parts)


def registry_entry(
    number: int = 1, *, last_used: datetime = IMPORTED_AT, folder: Path | None = None
) -> RegistryEntry:
    """Registry entry with id (UUID4 fixed from the number), name and folder derived."""
    profile_id = ProfileId.parse(f"00000000-0000-4000-8000-{number:012d}")
    return RegistryEntry(
        profile_id,
        folder or absolute_path(f"profile-{number}"),
        ProfileName.parse(f"Profilo {number}"),
        last_used,
    )


def remote_activity(
    number: int = 1,
    *,
    start: datetime = T0,
    duration_s: float = 3600.0,
    sport_key: str = "running",
    has_heart_rate: bool | None = True,
) -> RemoteActivity:
    """Remote listing activity with Garmin id `1000 + number` (distinct for each `number`)."""
    return RemoteActivity(
        GarminActivityId(1000 + number), start, duration_s, sport_key, has_heart_rate
    )
