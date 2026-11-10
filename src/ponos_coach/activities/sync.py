# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Use case "synchronize with Garmin": paged list, journal, download, archive and index.

The shell (worker, Composition Root) builds `SyncPorts` and calls `synchronize_activities`.
Two phases, in this order, both resumable after an interruption (PLAN.md 4.4):

1. **List.** Complete the first time (`ListingKind.FULL`), then incremental (from the most recent
   until a whole page consists of known ids) or, on request, "full check" (which also rearms
   the entries failed for exhausted attempts or not provided by Garmin). The list ends with a
   short or empty page, never at the total declared by the first page (Garmin may receive new
   activities while listing and the oldest would slip past the total). Each page
   adds its entries to the journal and saves the cursor in the same transaction: after a kill
   the list restarts from the cursor, without losing or duplicating entries. The cursor is resumed
   even if
   it is from a previous run: activities that appeared in the meantime at the top enter at the
   next synchronization, they are not lost.
2. **Entries to process** (`PENDING` and `RETRYABLE`, from the most recent). First the match with
   an activity already imported from file (no download); otherwise download with pause, extraction
   from the ZIP, decoding, check that the file really is the one of the entry (start and duration),
   write to the archive and, **afterwards**, in a short transaction, record and entry state. A
   kill at any point leaves the entry as it was and, at most, a file in the archive without a
   record,
   which the resumption rewrites identically (the archive is idempotent): the database write
   lock lasts only as long as the record.

Errors according to how the caller reacts: authentication required, rate limit beyond
the caps, unreachable service, archive error or busy database **stop** the
synchronization with a reason (`StopReason`); unsupported format, corrupt or too large
file, or an original that is not the one of the entry **fail the entry** definitively and
it moves on. A round stopped by the network or the rate limit **does not consume attempts**.
An entry whose download gets no useful answer (5xx, no response) after all its attempts is held
back, not written: if Garmin answers a later download, or a probe request (the first list page)
when the round stops or ends, it was the entry and not the network, and the round counts as one
**unanswered round** of that entry (`mark_unanswered`); after
`SyncPolicy.max_unanswered_rounds` of them the entry fails as `REMOTE_NOT_PROVIDED` and no longer
holds up the next rounds. If the probe gets no answer either, the round stops with the service
unavailable and nothing is written. The attempts of the entry are not used. No log of names or data
(only the type of an unexpected error), no wait that cancellation cannot interrupt.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from types import MappingProxyType
from typing import Final, Protocol, TypeVar

from ponos_coach.activities.decoding import ActivityFileError, ActivityFileReason
from ponos_coach.activities.domain import (
    DEFAULT_POLICY,
    MAX_DOWNLOAD_BYTES,
    REARMABLE_REASONS,
    RETRYABLE_REASONS,
    Activity,
    ActivityOrigin,
    ActivityRecord,
    ArchivedFile,
    FailureReason,
    FileFormat,
    GarminActivityId,
    JournalEntry,
    ListingCursor,
    ListingKind,
    RandomSource,
    SyncPolicy,
    SyncStatus,
    advance_cursor,
    estimate_remaining_s,
    find_matching_import,
    mark_downloaded,
    mark_failed,
    mark_matched,
    mark_unanswered,
    original_matches_listing,
    pause_between_requests_s,
    rearm_exhausted,
    retry_wait_s,
    truncate_to_millisecond,
)
from ponos_coach.activities.ports import (
    ActivityArchive,
    ActivityIndex,
    ActivitySource,
    ActivitySourceError,
    ArchiveError,
    ArchiveTooLargeError,
    AuthenticationRequiredError,
    Clock,
    DownloadTooLargeError,
    DuplicateActivityError,
    RateLimitedError,
    StorageBusyError,
    SyncJournal,
    TransientSourceError,
)

_LOG = logging.getLogger(__name__)

# Entries in a row failed for unreachable service (each after all its attempts) beyond
# which the synchronization stops instead of consuming retries on the whole history.
MAX_CONSECUTIVE_UNREACHABLE: Final = 3
# Cap on the entries listed in a single run: beyond this, the Garmin response is suspicious.
MAX_LISTED_ACTIVITIES: Final = 1_000_000
# Failed entries for exhausted attempts rearmed per transaction by the full check.
_REARM_BATCH: Final = 500

_T = TypeVar("_T")

_REASON_BY_FILE_ERROR: Final[Mapping[ActivityFileReason, FailureReason]] = {
    ActivityFileReason.EMPTY: FailureReason.CORRUPT_FILE,
    ActivityFileReason.TOO_LARGE: FailureReason.FILE_TOO_LARGE,
    ActivityFileReason.CORRUPT: FailureReason.CORRUPT_FILE,
    ActivityFileReason.NOT_AN_ACTIVITY: FailureReason.NOT_AN_ACTIVITY,
    ActivityFileReason.UNSUPPORTED_FORMAT: FailureReason.UNSUPPORTED_FORMAT,
    ActivityFileReason.TOO_MANY_RECORDS: FailureReason.FILE_TOO_LARGE,
    ActivityFileReason.TOO_LONG: FailureReason.CORRUPT_FILE,
}


# --- Injected dependencies ----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ExtractedOriginal:
    """The activity file extracted from the downloaded ZIP: its bytes and its format."""

    content: bytes
    file_format: FileFormat


class OriginalExtractionError(Exception):
    """The downloaded ZIP does not yield an activity file; `reason` is always a final cause
    (`ARCHIVE_INVALID` for an invalid or hostile ZIP, `UNSUPPORTED_FORMAT` if it contains only
    unsupported formats such as TCX, `FILE_TOO_LARGE`, `NOT_AN_ACTIVITY`...)."""

    def __init__(self, reason: FailureReason) -> None:
        if reason in RETRYABLE_REASONS or reason in REARMABLE_REASONS:
            raise ValueError("extraction always fails for a final cause")
        super().__init__(reason.value)
        self.reason = reason


OriginalExtractor = Callable[[bytes], ExtractedOriginal]
"""From the bytes of the downloaded ZIP to the extracted activity file, with the ZIP adapter limits.

Raises `OriginalExtractionError`; any other exception fails the entry as
`ARCHIVE_INVALID`."""

ActivityDecoder = Callable[[bytes, FileFormat], Activity]
"""Decodes an activity file of known format (`decode_stored_activity`).

Raises `ActivityFileError`; any other exception fails the entry as `CORRUPT_FILE`."""

InterruptibleWait = Callable[[float], bool]
"""Waits at most `seconds` seconds and returns True if cancellation was requested before
(`threading.Event.wait` has exactly this form)."""


class SyncClock(Clock, Protocol):
    """Synchronization clock: `now()` for instants (UTC), `monotonic()` for
    durations (seconds, never backwards)."""

    def monotonic(self) -> float:
        """Seconds of a monotonic clock."""
        ...


@dataclass(frozen=True, slots=True)
class SyncPorts:
    """Everything the synchronization receives from the Composition Root.

    `index`, `journal` and `archive` are those of the profile opened by the worker (same connection
    and same transaction for index and journal). `rng` gives the jitter.
    """

    source: ActivitySource
    index: ActivityIndex
    journal: SyncJournal
    archive: ActivityArchive
    extract_original: OriginalExtractor
    decode: ActivityDecoder
    clock: SyncClock
    wait: InterruptibleWait
    rng: RandomSource
    policy: SyncPolicy = DEFAULT_POLICY


# --- Request and outcome -------------------------------------------------------------------------


class SyncMode(Enum):
    """What is requested."""

    SYNC = "sync"  # complete the first time (or until finished), then incremental
    VERIFY = "verify"  # "Full check": lists the whole history again without re-downloading


class SyncPhase(Enum):
    """Which phase the synchronization is in."""

    LISTING = "listing"
    DOWNLOADING = "downloading"


@dataclass(frozen=True, slots=True)
class SyncProgress:
    """Progress: `done` out of `total` (None if unknown) of the phase, and the estimated time to
    finish it (None until measurements exist)."""

    phase: SyncPhase
    done: int
    total: int | None
    remaining_s: float | None = None


class StopReason(Enum):
    """Why the synchronization did not reach the end. The value is stable."""

    CANCELLED = "cancelled"
    AUTHENTICATION_REQUIRED = "authentication_required"  # a new login is needed
    RATE_LIMITED = "rate_limited"  # Garmin limits the rate beyond the caps
    SERVICE_UNAVAILABLE = "service_unavailable"  # Garmin does not respond
    REMOTE_ERROR = "remote_error"  # the list was rejected or is not interpretable
    STORAGE_ERROR = "storage_error"  # the profile archive cannot write (disk full...)
    LISTING_LIMIT = "listing_limit"  # more than `MAX_LISTED_ACTIVITIES` entries


@dataclass(frozen=True, slots=True)
class EntryFailure:
    """An entry failed in this run and its cause (retryable if the cause is in
    `RETRYABLE_REASONS`)."""

    garmin_id: GarminActivityId
    reason: FailureReason


@dataclass(frozen=True, slots=True)
class SyncOutcome:
    """Outcome of a synchronization.

    `stop` is None if it reached the end (only then was the end recorded in
    `last_completed_at`). `new_entries` are the entries added to the journal; `downloaded` counts
    only the **new** activities downloaded and archived in this run; `matched` those
    matched to an already imported activity (in another format); `already_present` those whose
    file was already in the profile (same identity, imported earlier or downloaded under another
    id): linked, but not downloaded again. `failures` are the failed entries (even if
    retryable) and written to the journal; `status_counts` the whole journal at the end of the run.
    """

    kind: ListingKind
    stop: StopReason | None
    new_entries: int
    downloaded: int
    matched: int
    failures: tuple[EntryFailure, ...]
    status_counts: Mapping[SyncStatus, int]
    already_present: int = 0

    @property
    def completed(self) -> bool:
        """True if the synchronization reached the end."""
        return self.stop is None

    @property
    def cancelled(self) -> bool:
        """True if whoever started it interrupted it."""
        return self.stop is StopReason.CANCELLED

    def failures_by_reason(self) -> Mapping[FailureReason, int]:
        """How many entries failed for each cause (only the causes present)."""
        counts: dict[FailureReason, int] = {}
        for failure in self.failures:
            counts[failure.reason] = counts.get(failure.reason, 0) + 1
        return MappingProxyType(counts)


def _ignore_progress(_progress: SyncProgress) -> None:
    """Progress not requested."""


def _never_cancelled() -> bool:
    return False


def listing_kind_for(mode: SyncMode, full_listing: ListingCursor | None) -> ListingKind:
    """Which list to walk: the verification, the complete one until finished, then the
    incremental."""
    if mode is SyncMode.VERIFY:
        return ListingKind.VERIFY
    if full_listing is None or not full_listing.completed:
        return ListingKind.FULL
    return ListingKind.INCREMENTAL


def synchronize_activities(
    ports: SyncPorts,
    mode: SyncMode = SyncMode.SYNC,
    *,
    report: Callable[[SyncProgress], None] = _ignore_progress,
    is_cancelled: Callable[[], bool] = _never_cancelled,
) -> SyncOutcome:
    """Synchronizes the profile with Garmin.

    First the list (complete, incremental or verification, see `listing_kind_for`), then the
    entries to
    process from the most recent. `report` receives the progress of each phase;
    `is_cancelled()` is checked before every request, entry and page, and `ports.wait`
    interrupts the pauses: cancellation stops within one call and leaves journal and index
    consistent. An interrupted or stopped run is resumed by running again.

    It does not raise for expected failures (see `StopReason` and `EntryFailure`): they are in the
    outcome.
    Programming errors and database failures propagate; the transaction in progress
    is rolled back.
    """
    kind = listing_kind_for(mode, ports.journal.load_cursor(ListingKind.FULL))
    return _Run(ports, kind, report, is_cancelled).execute()


# --- Execution ----------------------------------------------------------------------------


class _StopError(Exception):
    """Early end of a run (internal flow control)."""

    def __init__(self, reason: StopReason) -> None:
        super().__init__(reason.value)
        self.reason = reason


class _Saved(Enum):
    """What the saving of a downloaded original produced."""

    NEW = "new"  # new activity: archive and record
    ALREADY_PRESENT = "already_present"  # same file already in the profile: id linked


class _RequestFailedError(Exception):
    """A request exhausted its attempts (or the wait Garmin asks for is beyond the cap)."""

    def __init__(self, reason: FailureReason) -> None:
        super().__init__(reason.value)
        self.reason = reason


class _Run:
    """State of a single run: counters, measured durations, last request."""

    def __init__(
        self,
        ports: SyncPorts,
        kind: ListingKind,
        report: Callable[[SyncProgress], None],
        is_cancelled: Callable[[], bool],
    ) -> None:
        self._p = ports
        self._kind = kind
        self._report = report
        self._is_cancelled = is_cancelled
        self._new_entries = 0
        self._downloaded = 0
        self._matched = 0
        self._already_present = 0
        self._failures: list[EntryFailure] = []
        # Entries failed because of the network in this round, not yet written (see
        # `_network_answered`).
        self._held: list[JournalEntry] = []
        self._durations_s: list[float] = []
        self._requested_before = False
        self._unreachable_in_a_row = 0

    def execute(self) -> SyncOutcome:
        stop: StopReason | None = None
        try:
            self._rearm_exhausted()
            self._list()
            self._work_through_entries()
            self._settle_held_after_probe()  # the round is over: the entries or the network?
        except _StopError as stopped:
            stop = stopped.reason
        except StorageBusyError:  # database busy beyond the wait: nothing was touched
            stop = StopReason.STORAGE_ERROR
        if stop is None:
            self._p.journal.record_completed(self._now())
        return SyncOutcome(
            self._kind,
            stop,
            self._new_entries,
            self._downloaded,
            self._matched,
            tuple(self._failures),
            MappingProxyType(dict(self._p.journal.counts())),
            self._already_present,
        )

    # --- Full check: exhausted attempts ----------------------------------------------------

    def _rearm_exhausted(self) -> None:
        """Full check only: entries failed for exhausted attempts or because Garmin did not provide
        them go back to download."""
        if self._kind is not ListingKind.VERIFY:
            return
        journal = self._p.journal
        while True:
            self._check_cancelled()
            batch = journal.next_exhausted(_REARM_BATCH)
            if not batch:
                return
            now = self._now()
            with self._p.index.transaction():
                for entry in batch:
                    journal.update(rearm_exhausted(entry, now))

    # --- List -----------------------------------------------------------------------------

    def _list(self) -> None:
        journal = self._p.journal
        limit = self._p.policy.page_size
        cursor = journal.load_cursor(self._kind)
        if cursor is None or cursor.completed:
            cursor = ListingCursor.start()
        while not cursor.completed:
            self._check_cancelled()
            if cursor.next_offset >= MAX_LISTED_ACTIVITIES:
                raise _StopError(StopReason.LISTING_LIMIT)
            self._report(SyncProgress(SyncPhase.LISTING, cursor.next_offset, _total_of(cursor)))
            cursor = self._list_one_page(cursor, limit)
        self._report(SyncProgress(SyncPhase.LISTING, cursor.next_offset, cursor.next_offset))

    def _list_one_page(self, cursor: ListingCursor, limit: int) -> ListingCursor:
        offset = cursor.next_offset
        try:
            page = self._request(lambda: self._p.source.list_page(offset, limit))
        except _RequestFailedError as failed:
            raise _StopError(_stop_for(failed.reason)) from failed
        except ActivitySourceError as error:  # list rejected or not interpretable
            raise _StopError(StopReason.REMOTE_ERROR) from error
        known: frozenset[GarminActivityId] | None = None
        if self._kind is ListingKind.INCREMENTAL:
            known = self._p.journal.known_ids([item.garmin_id for item in page.items])
        try:
            following = advance_cursor(cursor, page, limit, known)
        except ValueError as error:  # page outside the port's contract
            raise _StopError(StopReason.REMOTE_ERROR) from error
        with self._p.index.transaction():
            added = self._p.journal.add_pending(page.items, self._now())
            self._p.journal.save_cursor(self._kind, following)
        self._new_entries += added
        return following

    # --- Entries to process -----------------------------------------------------------------

    def _work_through_entries(self) -> None:
        counts = self._p.journal.counts()
        total = counts[SyncStatus.PENDING] + counts[SyncStatus.RETRYABLE]
        entries = self._p.journal.next_to_process(total) if total else ()
        for done, entry in enumerate(entries):
            self._check_cancelled()
            remaining = estimate_remaining_s(self._durations_s, total - done)
            self._report(SyncProgress(SyncPhase.DOWNLOADING, done, total, remaining))
            started = self._p.clock.monotonic()
            self._work_on(entry)
            self._durations_s.append(max(self._p.clock.monotonic() - started, 0.0))
        self._report(SyncProgress(SyncPhase.DOWNLOADING, total, total, 0.0))

    def _work_on(self, entry: JournalEntry) -> None:
        if self._match_with_import(entry):
            return
        zipped = self._download(entry)
        if zipped is None:
            return
        extracted = self._extract(entry, zipped)
        if extracted is None:
            return
        activity = self._decode(entry, extracted)
        if activity is None:
            return
        self._save(entry, extracted, activity)

    def _match_with_import(self, entry: JournalEntry) -> bool:
        """Matches the entry to an activity already imported from file: no download. True if
        done."""
        remote = entry.remote
        candidates = self._p.index.find_possible_duplicates(remote.start, remote.duration_s)
        match = find_matching_import(remote, candidates)
        if match is None:
            return False
        try:
            with self._p.index.transaction():
                self._p.index.link_garmin_id(match.identity, entry.garmin_id)
                self._p.journal.update(mark_matched(entry, self._now()))
        except (
            DuplicateActivityError
        ):  # the id already belongs to another record: it is downloaded and seen
            return False
        self._matched += 1
        return True

    def _download(self, entry: JournalEntry) -> bytes | None:
        garmin_id = entry.garmin_id
        try:
            content = self._request(
                lambda: self._p.source.download_original(garmin_id, MAX_DOWNLOAD_BYTES)
            )
        except _RequestFailedError as failed:
            self._hold_or_stop(entry, failed.reason)
            return None
        except DownloadTooLargeError:
            self._network_answered()
            self._fail(entry, FailureReason.FILE_TOO_LARGE)
        except ActivitySourceError:  # original absent, rejected or not interpretable
            self._network_answered()
            self._fail(entry, FailureReason.REMOTE_REJECTED)
        else:
            self._network_answered()
            return content
        return None

    def _hold_or_stop(self, entry: JournalEntry, reason: FailureReason) -> None:
        """A request exhausted its attempts because of network or limit, without a useful answer.

        With the rate limit it stops immediately, the entry as it was. For the network the entry is
        held back (not written): if Garmin answers for a later entry it was a defect
        of the entry and the round counts for it. If the service stays silent for
        `MAX_CONSECUTIVE_UNREACHABLE` entries in a row the round stops, after a probe that
        tells the entries (counted) from the network (nothing counted).
        """
        if reason is FailureReason.RATE_LIMITED:
            raise _StopError(StopReason.RATE_LIMITED)
        self._held.append(entry)
        self._unreachable_in_a_row += 1
        if self._unreachable_in_a_row >= MAX_CONSECUTIVE_UNREACHABLE:
            self._settle_held_after_probe()
            raise _StopError(StopReason.SERVICE_UNAVAILABLE)

    def _network_answered(self) -> None:
        """Garmin answered (even with a rejection): the network is there, the held-back failures
        were their entries' own and are written."""
        self._unreachable_in_a_row = 0
        self._write_held_failures()

    def _write_held_failures(self) -> None:
        """Garmin answered: each held-back entry has one more unanswered round."""
        held, self._held = self._held, []
        for entry in held:
            unanswered = mark_unanswered(entry, self._now(), self._p.policy.max_unanswered_rounds)
            self._p.journal.update(unanswered)
            if unanswered.failure is not None:
                self._failures.append(EntryFailure(entry.garmin_id, unanswered.failure))

    def _settle_held_after_probe(self) -> None:
        """No download has answered since the entries were held back: ask Garmin something else.

        The probe is the first list page (one entry). Any answer from Garmin, even a rejection,
        shows that the network and the service are there, so the held-back entries are the
        ones with the problem and the round counts for them. No answer at all (the usual
        attempts and backoff exhausted): the network is down, nothing is written, and the round
        stops. Authentication and cancellation stop as for any request.
        """
        if not self._held:
            return
        try:
            self._request(lambda: self._p.source.list_page(0, 1))
        except _RequestFailedError as failed:
            raise _StopError(_stop_for(failed.reason)) from failed
        except ActivitySourceError:
            pass  # a rejection is still an answer
        self._write_held_failures()

    def _extract(self, entry: JournalEntry, zipped: bytes) -> ExtractedOriginal | None:
        try:
            return self._p.extract_original(zipped)
        except OriginalExtractionError as error:
            self._fail(entry, error.reason)
        except Exception as error:  # an extractor that should not raise anything else
            _log_unexpected("extraction", error)
            self._fail(entry, FailureReason.ARCHIVE_INVALID)
        return None

    def _decode(self, entry: JournalEntry, extracted: ExtractedOriginal) -> Activity | None:
        try:
            return self._p.decode(extracted.content, extracted.file_format)
        except ActivityFileError as error:
            self._fail(entry, _REASON_BY_FILE_ERROR[error.reason])
        except Exception as error:  # a reader that should not raise anything else
            _log_unexpected("decoding", error)
            self._fail(entry, FailureReason.CORRUPT_FILE)
        return None

    def _save(self, entry: JournalEntry, extracted: ExtractedOriginal, activity: Activity) -> None:
        """Checks the file, archives it and then, in a short transaction, writes record and entry.

        The archive (idempotent) is written **before** opening the transaction, so the
        database write lock lasts only as long as the record.
        """
        remote = entry.remote
        if not original_matches_listing(remote, activity.start, activity.elapsed_s):
            self._fail(
                entry, FailureReason.REMOTE_REJECTED
            )  # it is another activity: never recorded
            return
        try:
            archived = self._store_if_new(extracted, activity)
            with self._p.index.transaction():
                saved = self._record_activity(entry, extracted, activity, archived)
        except DuplicateActivityError:
            # The file, or the Garmin id, already belongs to another record: the activity is
            # already in the profile.
            self._p.journal.update(mark_downloaded(entry, self._now()))
            saved = _Saved.ALREADY_PRESENT
        except ArchiveTooLargeError:
            self._fail(entry, FailureReason.FILE_TOO_LARGE)
            return
        except ArchiveError as error:  # not the entry's fault: it stops, the entry stays as it was
            raise _StopError(StopReason.STORAGE_ERROR) from error
        if saved is _Saved.ALREADY_PRESENT:
            self._already_present += 1
        else:
            self._downloaded += 1

    def _store_if_new(
        self, extracted: ExtractedOriginal, activity: Activity
    ) -> ArchivedFile | None:
        """Archives the file only if the activity is new (the same file is not there yet);
        otherwise None. Reads without a transaction: the decision is repeated,
        identical, inside the transaction that writes."""
        index = self._p.index
        if index.get(activity.identity) is not None:
            return None
        return self._p.archive.store(extracted.content, activity.file_format, activity.start)

    def _record_activity(
        self,
        entry: JournalEntry,
        extracted: ExtractedOriginal,
        activity: Activity,
        archived: ArchivedFile | None,
    ) -> _Saved:
        index = self._p.index
        garmin_id = entry.garmin_id
        now = self._now()
        existing = index.get(activity.identity)
        if existing is not None:  # same file already imported: the id is linked
            if existing.garmin_activity_id is None:
                index.link_garmin_id(existing.identity, garmin_id)
            self._p.journal.update(mark_downloaded(entry, now))
            return _Saved.ALREADY_PRESENT
        if archived is None:  # the state changed after the read: it is archived now
            archived = self._p.archive.store(
                extracted.content, activity.file_format, activity.start
            )
        index.add(
            ActivityRecord.from_activity(
                activity, archived, ActivityOrigin.GARMIN, now, garmin_id.value
            )
        )
        self._p.journal.update(mark_downloaded(entry, now))
        return _Saved.NEW

    def _fail(self, entry: JournalEntry, reason: FailureReason) -> None:
        failed = mark_failed(entry, reason, self._now(), self._p.policy.max_syncs_per_activity)
        self._p.journal.update(failed)
        if failed.failure is not None:
            self._failures.append(EntryFailure(entry.garmin_id, failed.failure))

    # --- Requests: pause, backoff, cancellation -------------------------------------------

    def _request(self, call: Callable[[], _T]) -> _T:
        """A request to Garmin with a pause before and retries on transient errors.

        Raises:
            _RequestFailedError: attempts exhausted, or suggested wait beyond the backoff cap.
            _StopError: authentication required or cancellation.
        Errors that are final for the source pass to the caller.
        """
        self._check_cancelled()
        if self._requested_before:
            self._sleep(pause_between_requests_s(self._p.policy, self._p.rng))
        self._requested_before = True
        failures = 0
        while True:
            self._check_cancelled()
            try:
                return call()
            except AuthenticationRequiredError as error:
                raise _StopError(StopReason.AUTHENTICATION_REQUIRED) from error
            except RateLimitedError as error:
                failures += 1
                self._back_off(failures, FailureReason.RATE_LIMITED, error.retry_after_s)
            except TransientSourceError:
                failures += 1
                self._back_off(failures, FailureReason.TRANSIENT_ERROR, None)

    def _back_off(self, failures: int, reason: FailureReason, suggested_s: float | None) -> None:
        wait_s = retry_wait_s(self._p.policy, failures, self._p.rng, suggested_s)
        if wait_s is None:
            raise _RequestFailedError(reason)
        self._sleep(wait_s)

    def _sleep(self, seconds: float) -> None:
        if self._p.wait(seconds):
            raise _StopError(StopReason.CANCELLED)

    def _check_cancelled(self) -> None:
        if self._is_cancelled():
            raise _StopError(StopReason.CANCELLED)

    def _now(self) -> datetime:
        return truncate_to_millisecond(self._p.clock.now())


def _total_of(cursor: ListingCursor) -> int | None:
    """Total for progress: the declared one, but never below the position reached (Garmin
    may receive new activities during the list)."""
    if cursor.declared_total is None:
        return None
    return max(cursor.declared_total, cursor.next_offset)


def _stop_for(reason: FailureReason) -> StopReason:
    return (
        StopReason.RATE_LIMITED
        if reason is FailureReason.RATE_LIMITED
        else StopReason.SERVICE_UNAVAILABLE
    )


def _log_unexpected(step: str, error: Exception) -> None:
    """One single record per unexpected error, with the type only: the message may contain data."""
    _LOG.error("synchronization: unexpected error in %s (%s)", step, type(error).__name__)
