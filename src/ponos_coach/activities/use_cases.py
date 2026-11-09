# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Use case "import activities": chosen FIT files and folders into the open profile.

The shell (interface, worker) creates the source and closes it; here, for each listed file, the
file is
read, decoded, checked that the activity is not already there, and saved: the original file
in the archive first, the record in the index after, in the same transaction. The import is
idempotent (same files, same profile: all "already present") and cancellable between one file and
the other. No log: names and positions are personal data.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

from ponos_coach.activities.decoding import ActivityFileError, decode_activity_file
from ponos_coach.activities.domain import (
    MAX_ACTIVITY_FILE_BYTES,
    Activity,
    ActivityIdentity,
    ActivityOrigin,
    ActivityRecord,
    ArchivedFile,
    truncate_to_millisecond,
)
from ponos_coach.activities.ports import (
    ActivityArchive,
    ActivityIndex,
    ArchiveError,
    ArchiveFileMissingError,
    ArchiveIntegrityError,
    ArchiveTooLargeError,
    Clock,
    DuplicateActivityError,
    ImportEntry,
    ImportEntryError,
    ImportIssue,
    ImportScanCancelledError,
    ImportSource,
)

_LOG = logging.getLogger(__name__)


class ImportStatus(Enum):
    """Outcome of importing a file."""

    IMPORTED = "imported"
    ALREADY_PRESENT = "already_present"  # same identity already in the index
    REPAIRED = (
        "repaired"  # already in the index, but its file in the archive was missing or altered
    )
    IGNORED = "ignored"  # the file is not an activity (e.g. a settings FIT)
    FAILED = "failed"  # unreadable, rejected or not saved: `issue` says why


@dataclass(frozen=True, slots=True)
class FileImportResult:
    """Per-file outcome. `issue` is present if and only if the outcome is IGNORED or FAILED;
    `matches` is
    the identity of the activity already present for ALREADY_PRESENT and REPAIRED."""

    name: str
    status: ImportStatus
    issue: ImportIssue | None = None
    matches: ActivityIdentity | None = None

    def __post_init__(self) -> None:
        has_issue = self.status in (ImportStatus.IGNORED, ImportStatus.FAILED)
        has_match = self.status in (ImportStatus.ALREADY_PRESENT, ImportStatus.REPAIRED)
        if has_issue != (self.issue is not None) or has_match != (self.matches is not None):
            raise ValueError("reason and matching activity inconsistent with the outcome")


@dataclass(frozen=True, slots=True)
class ImportSummary:
    """Final summary: one outcome per file (first those skipped by the source, then the others, in
    name order). With `cancelled` the files not yet processed are missing."""

    results: tuple[FileImportResult, ...]
    cancelled: bool

    def count(self, status: ImportStatus) -> int:
        """How many files have this outcome."""
        return sum(1 for result in self.results if result.status is status)

    def named(self, status: ImportStatus) -> tuple[FileImportResult, ...]:
        """The outcomes with this status, in the same order as the summary."""
        return tuple(result for result in self.results if result.status is status)


def _ignore_progress(_done: int, _total: int) -> None:
    """Progress not requested."""


def _never_cancelled() -> bool:
    return False


def _is_the_archived_content(archived: ArchivedFile, data: bytes) -> bool:
    return (
        len(data) == archived.size_bytes and hashlib.sha256(data).hexdigest() == archived.sha256_hex
    )


def _check_archived_file(
    record: ActivityRecord, data: bytes, name: str, archive: ActivityArchive
) -> FileImportResult:
    """Activity already in the index: its file in the archive must be there and intact.

    If it is missing or altered and the imported file has exactly the recorded bytes (same SHA-256
    and
    same size), it is put back in the archive: outcome `REPAIRED`. If the bytes are different (the
    same identity, different content) nothing is repaired: `ALREADY_PRESENT`.
    """
    try:
        archive.verify(record.archived)
    except (ArchiveFileMissingError, ArchiveIntegrityError):
        if _is_the_archived_content(record.archived, data):
            archive.store(data, record.file_format, record.start)
            return FileImportResult(name, ImportStatus.REPAIRED, matches=record.identity)
    return FileImportResult(name, ImportStatus.ALREADY_PRESENT, matches=record.identity)


def _add_if_new(
    activity: Activity,
    data: bytes,
    name: str,
    archive: ActivityArchive,
    index: ActivityIndex,
    clock: Clock,
) -> FileImportResult:
    existing = index.get(activity.identity)
    if existing is not None:
        return _check_archived_file(existing, data, name, archive)
    archived = archive.store(data, activity.file_format, activity.start)
    record = ActivityRecord.from_activity(
        activity,
        archived,
        ActivityOrigin.IMPORT,
        truncate_to_millisecond(clock.now()),
    )
    index.add(record)
    return FileImportResult(name, ImportStatus.IMPORTED)


def _store(
    activity: Activity,
    data: bytes,
    name: str,
    archive: ActivityArchive,
    index: ActivityIndex,
    clock: Clock,
) -> FileImportResult:
    """Checks duplicates, archives the file and writes the record in a single transaction."""
    try:
        with index.transaction():
            return _add_if_new(activity, data, name, archive, index, clock)
    except DuplicateActivityError:
        return FileImportResult(name, ImportStatus.ALREADY_PRESENT, matches=activity.identity)
    except ArchiveTooLargeError:
        return FileImportResult(name, ImportStatus.FAILED, ImportIssue.TOO_LARGE)
    except ArchiveError:
        return FileImportResult(name, ImportStatus.FAILED, ImportIssue.STORAGE_FAILED)
    except Exception as error:  # unexpected failure: this file fails, the others go on
        _log_unexpected(error)
        return FileImportResult(name, ImportStatus.FAILED, ImportIssue.STORAGE_FAILED)


def _log_unexpected(error: Exception) -> None:
    """One single record per unexpected error, with the type only: the message may contain names,
    paths or personal data."""
    _LOG.error("import: unexpected error on a file (%s)", type(error).__name__)


def _import_entry(
    entry: ImportEntry,
    source: ImportSource,
    archive: ActivityArchive,
    index: ActivityIndex,
    clock: Clock,
) -> FileImportResult:
    try:
        data = source.read(entry, MAX_ACTIVITY_FILE_BYTES)
    except ImportEntryError as error:
        return FileImportResult(entry.name, ImportStatus.FAILED, error.issue)
    except Exception as error:  # unexpected failure: this file fails, the others go on
        _log_unexpected(error)
        return FileImportResult(entry.name, ImportStatus.FAILED, ImportIssue.UNREADABLE)
    try:
        activity = decode_activity_file(data, entry.name)
    except ActivityFileError as error:
        issue = ImportIssue(error.reason.value)
        status = (
            ImportStatus.IGNORED if issue is ImportIssue.NOT_AN_ACTIVITY else ImportStatus.FAILED
        )
        return FileImportResult(entry.name, status, issue)
    except Exception as error:  # a reader that should not raise anything else: the file is rejected
        _log_unexpected(error)
        return FileImportResult(entry.name, ImportStatus.FAILED, ImportIssue.CORRUPT)
    return _store(activity, data, entry.name, archive, index, clock)


def import_activities(
    source: ImportSource,
    archive: ActivityArchive,
    index: ActivityIndex,
    clock: Clock,
    *,
    report: Callable[[int, int], None] = _ignore_progress,
    is_cancelled: Callable[[], bool] = _never_cancelled,
) -> ImportSummary:
    """Imports the activity files of the source into the profile.

    First it examines the source (`source.scan()`): if it exceeds a cap nothing is imported. Then,
    for each file in name order, it saves the original file in the archive and the record
    in the index in one transaction; an activity already present (same identity) or already arrived
    in another format (same start and duration) is not saved twice. A file with an error does not
    stop the others. `report(done, total)` is called before the first file and after each one;
    `is_cancelled()` is checked before every file and, if true, the summary is partial
    (`cancelled`) and running the import again resumes without duplicates.

    The examination of the source can also be cancelled: the summary is then empty and `cancelled`.
    An unexpected error on a single file becomes the FAILED outcome of that file (recorded
    once, with the exception type only) and the import continues. A file already present whose
    original in the archive is missing or altered is repaired (outcome REPAIRED) if the imported
    file has the
    recorded bytes.

    Raises:
        ImportLimitExceededError: the source exceeds a cap (activity files, examined entries
            or levels): nothing was imported.
        ImportSourceUnreadableError: the source cannot be read.
    """
    try:
        scan = source.scan(is_cancelled)
    except ImportScanCancelledError:
        return ImportSummary((), cancelled=True)
    results = [
        FileImportResult(item.name, ImportStatus.FAILED, item.issue) for item in scan.skipped
    ]
    total = len(scan.entries)
    report(0, total)
    for done, entry in enumerate(scan.entries):
        if is_cancelled():
            return ImportSummary(tuple(results), cancelled=True)
        results.append(_import_entry(entry, source, archive, index, clock))
        report(done + 1, total)
    return ImportSummary(tuple(results), cancelled=False)
