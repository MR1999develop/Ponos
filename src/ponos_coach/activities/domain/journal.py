# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Synchronization journal: state of each remote activity, list cursor.

The journal makes synchronization resumable (PLAN.md 4.4): each remote id is an entry with
a state. The allowed transitions are pure functions; there is no "in progress" state, because
download, archive and confirmation are in a single transaction: an interruption leaves the entry
as it was.
"""

from __future__ import annotations

from collections.abc import Mapping, Set
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Final

from ponos_coach.activities.domain.garmin_id import GarminActivityId
from ponos_coach.activities.domain.remote import MAX_PAGE_SIZE, ActivityPage, RemoteActivity
from ponos_coach.activities.domain.timing import check_utc


class SyncStatus(Enum):
    """State of a journal entry. The value is stable (saved data)."""

    PENDING = "pending"  # to download
    DOWNLOADED = "downloaded"  # downloaded, archived and indexed
    MATCHED = "matched"  # matched to an activity already imported from file: no download
    RETRYABLE = "retryable"  # failed for a transient cause: retried next time
    FAILED = "failed"  # failed definitively: not retried


class FailureReason(Enum):
    """Why an entry failed. The value is stable (saved data)."""

    # Retryable: the cause may pass.
    TRANSIENT_ERROR = "transient_error"
    RATE_LIMITED = "rate_limited"
    # Final: retrying would give the same outcome.
    UNSUPPORTED_FORMAT = "unsupported_format"  # e.g. a TCX
    CORRUPT_FILE = "corrupt_file"
    FILE_TOO_LARGE = "file_too_large"
    NOT_AN_ACTIVITY = "not_an_activity"
    ARCHIVE_INVALID = "archive_invalid"  # the downloaded ZIP is invalid or hostile
    REMOTE_REJECTED = "remote_rejected"  # Garmin says the original does not exist or is not given
    ATTEMPTS_EXHAUSTED = "attempts_exhausted"  # too many failed synchronizations


RETRYABLE_REASONS: Final = frozenset({FailureReason.TRANSIENT_ERROR, FailureReason.RATE_LIMITED})

WORKABLE_STATUSES: Final = frozenset({SyncStatus.PENDING, SyncStatus.RETRYABLE})
_FAILURE_STATUSES: Final = frozenset({SyncStatus.RETRYABLE, SyncStatus.FAILED})

# From -> to. Entries to process go to any of the other states (RETRYABLE included,
# if it fails again); DOWNLOADED and MATCHED do not move; FAILED goes back only to PENDING and only
# for `rearm_exhausted` (the "Full check" rearms exhausted attempts).
ALLOWED_TRANSITIONS: Final[Mapping[SyncStatus, frozenset[SyncStatus]]] = {
    SyncStatus.PENDING: frozenset(
        {SyncStatus.DOWNLOADED, SyncStatus.MATCHED, SyncStatus.RETRYABLE, SyncStatus.FAILED}
    ),
    SyncStatus.RETRYABLE: frozenset(
        {SyncStatus.DOWNLOADED, SyncStatus.MATCHED, SyncStatus.RETRYABLE, SyncStatus.FAILED}
    ),
    SyncStatus.DOWNLOADED: frozenset(),
    SyncStatus.MATCHED: frozenset(),
    SyncStatus.FAILED: frozenset({SyncStatus.PENDING}),
}


class InvalidTransitionError(ValueError):
    """The state transition is not allowed (the entry is already in a final state)."""


def can_transition(old: SyncStatus, new: SyncStatus) -> bool:
    """True if an entry in state `old` can move to `new`."""
    return new in ALLOWED_TRANSITIONS[old]


@dataclass(frozen=True, slots=True)
class JournalEntry:
    """A journal entry: the remote activity and where it stands.

    Invariants: `RETRYABLE` has a retryable cause and `FAILED` a final one, every other
    state none; `PENDING` has no failed attempts, `RETRYABLE` and `FAILED` have at least
    one; `updated_at` is UTC to the millisecond. `failed_attempts` counts the synchronizations in
    which
    the entry failed.
    """

    remote: RemoteActivity
    status: SyncStatus
    updated_at: datetime
    failed_attempts: int = 0
    failure: FailureReason | None = None

    def __post_init__(self) -> None:
        check_utc(self.updated_at)
        if self.updated_at.microsecond % 1000:
            raise ValueError("the instant must be at millisecond precision")
        if self.failed_attempts < 0:
            raise ValueError("failed attempts cannot be negative")
        if (self.status in _FAILURE_STATUSES) != (self.failure is not None):
            raise ValueError("only failure states have a cause")
        if self.failure is not None and (self.failure in RETRYABLE_REASONS) != (
            self.status is SyncStatus.RETRYABLE
        ):
            raise ValueError("cause and state inconsistent: retryable only if RETRYABLE")
        if self.status is SyncStatus.PENDING and self.failed_attempts:
            raise ValueError("an entry to download has no failed attempts")
        if self.status in _FAILURE_STATUSES and self.failed_attempts < 1:
            raise ValueError("a failed entry has at least one failed attempt")

    @property
    def garmin_id(self) -> GarminActivityId:
        """The Garmin id of the entry."""
        return self.remote.garmin_id


def pending_entry(remote: RemoteActivity, now: datetime) -> JournalEntry:
    """New entry, to download."""
    return JournalEntry(remote, SyncStatus.PENDING, now)


def _check_move(entry: JournalEntry, new: SyncStatus) -> None:
    if not can_transition(entry.status, new):
        raise InvalidTransitionError(f"{entry.status.value} -> {new.value}")


def mark_downloaded(entry: JournalEntry, now: datetime) -> JournalEntry:
    """The entry is downloaded, archived and indexed (keeps the failed attempts).

    Raises:
        InvalidTransitionError: the entry is already in a final state.
    """
    _check_move(entry, SyncStatus.DOWNLOADED)
    return JournalEntry(entry.remote, SyncStatus.DOWNLOADED, now, entry.failed_attempts)


def mark_matched(entry: JournalEntry, now: datetime) -> JournalEntry:
    """The entry is matched to an activity already imported from file: no download.

    Raises:
        InvalidTransitionError: the entry is already in a final state.
    """
    _check_move(entry, SyncStatus.MATCHED)
    return JournalEntry(entry.remote, SyncStatus.MATCHED, now, entry.failed_attempts)


def mark_failed(
    entry: JournalEntry, reason: FailureReason, now: datetime, max_attempts: int
) -> JournalEntry:
    """The entry failed in this synchronization.

    A final cause gives `FAILED`. A retryable cause gives `RETRYABLE`, until the failed attempts
    (this one included) reach `max_attempts`: then `FAILED` with
    `ATTEMPTS_EXHAUSTED`.

    Raises:
        InvalidTransitionError: the entry is already in a final state.
        ValueError: `max_attempts` less than 1.
    """
    if max_attempts < 1:
        raise ValueError("at least one attempt is required")
    attempts = entry.failed_attempts + 1
    if reason not in RETRYABLE_REASONS:
        status, cause = SyncStatus.FAILED, reason
    elif attempts < max_attempts:
        status, cause = SyncStatus.RETRYABLE, reason
    else:
        status, cause = SyncStatus.FAILED, FailureReason.ATTEMPTS_EXHAUSTED
    _check_move(entry, status)
    return JournalEntry(entry.remote, status, now, attempts, cause)


def rearm_exhausted(entry: JournalEntry, now: datetime) -> JournalEntry:
    """Rearms an entry that failed for exhausted attempts: back to download, with no attempts.

    Used by the "Full check": attempts consumed earlier (even because of the network)
    do not condemn a valid activity forever. The other final causes (format not
    supported, corrupt file...) stay as they are.

    Raises:
        InvalidTransitionError: the entry is not `FAILED` with `ATTEMPTS_EXHAUSTED`.
    """
    if (
        entry.status is not SyncStatus.FAILED
        or entry.failure is not FailureReason.ATTEMPTS_EXHAUSTED
    ):
        raise InvalidTransitionError(f"{entry.status.value} -> {SyncStatus.PENDING.value}")
    return JournalEntry(entry.remote, SyncStatus.PENDING, now)


# --- List cursor -------------------------------------------------------------------------


class ListingKind(Enum):
    """Which remote list a cursor is walking. The value is stable (saved data)."""

    FULL = "full"  # first synchronization: the whole history
    VERIFY = "verify"  # "Full check": lists everything again, without re-downloading
    INCREMENTAL = "incremental"  # later synchronizations: from the most recent


@dataclass(frozen=True, slots=True)
class ListingCursor:
    """Point reached in a remote list: the next position and the declared total.

    The total is only for progress (it may be old: Garmin receives new activities
    while listing); it does not decide when the list ends. `completed` says the list is
    finished: it no longer advances, to restart use `ListingCursor.start()`.
    """

    next_offset: int
    declared_total: int | None
    completed: bool

    def __post_init__(self) -> None:
        if self.next_offset < 0 or (self.declared_total is not None and self.declared_total < 0):
            raise ValueError("negative position or total")

    @classmethod
    def start(cls) -> ListingCursor:
        """Cursor at the start of the list."""
        return cls(0, None, False)


def is_known_page(page: ActivityPage, known_ids: Set[GarminActivityId]) -> bool:
    """Stop rule of the incremental list: a whole page of already known ids.

    True if the page has at least one valid entry and all are in `known_ids`. A page without
    valid entries is not "known": either the list is finished (`raw_count` says so) or they were all
    malformed.
    """
    return bool(page.items) and all(item.garmin_id in known_ids for item in page.items)


def advance_cursor(
    cursor: ListingCursor,
    page: ActivityPage,
    limit: int,
    known_ids: Set[GarminActivityId] | None = None,
) -> ListingCursor:
    """Cursor after processing `page`, requested with `limit` entries.

    The list is finished if Garmin returned fewer than `limit` entries (none included) or,
    only if `known_ids` is given (incremental list), the page is entirely of already known ids
    (`is_known_page`). The declared total does **not** stop it: it is fixed at the first page and,
    if
    Garmin receives a new activity during listing, the oldest would slip past; a short
    or empty page is enough (at most one extra request when the total is an exact
    multiple of `limit`). The cap on pages is `MAX_LISTED_ACTIVITIES` of the use case.

    Raises:
        ValueError: cursor already completed, page not at the cursor position, `limit`
            outside 1..`MAX_PAGE_SIZE` or page larger than `limit`.
    """
    if cursor.completed:
        raise ValueError("list already completed")
    if page.offset != cursor.next_offset:
        raise ValueError("the page is not at the cursor position")
    if not 1 <= limit <= MAX_PAGE_SIZE or page.raw_count > limit:
        raise ValueError("invalid or exceeded page limit")
    total = page.declared_total if page.declared_total is not None else cursor.declared_total
    next_offset = cursor.next_offset + page.raw_count
    done = page.raw_count < limit or (known_ids is not None and is_known_page(page, known_ids))
    return ListingCursor(next_offset, total, done)
