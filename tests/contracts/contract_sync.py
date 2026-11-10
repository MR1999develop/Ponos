# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Contracts of the sync journal and of the links to the index's Garmin id.

Like the other storage contracts, they are tried through a profile created by the
`ProfileStorage` of the `harness` fixture.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import closing
from datetime import timedelta

import pytest

from ponos_coach.activities.domain import (
    ActivityOrigin,
    FailureReason,
    GarminActivityId,
    InvalidTransitionError,
    JournalEntry,
    ListingCursor,
    ListingKind,
    SyncStatus,
    mark_downloaded,
    mark_failed,
    mark_matched,
    mark_unanswered,
    pending_entry,
    rearm_exhausted,
)
from ponos_coach.activities.ports import (
    ActivityNotFoundError,
    DuplicateActivityError,
    UnknownJournalEntryError,
)
from tests.builders import (
    IMPORTED_AT,
    T0,
    fit_identity,
    make_record,
    remote_activity,
)
from tests.contracts.contract_storage import NAME, PROFILE_ID, create
from tests.contracts.harness import StorageHarness

LATER = IMPORTED_AT + timedelta(minutes=5)


def _id(number: int) -> GarminActivityId:
    return GarminActivityId(1000 + number)


class ActivityIndexGarminIdContract:
    """Find an activity by Garmin id and link it (matching with a file import)."""

    def test_activity_is_found_by_its_garmin_id(self, harness: StorageHarness) -> None:
        record = make_record(origin=ActivityOrigin.GARMIN, garmin_activity_id=77)
        with closing(create(harness)) as profile:
            profile.activities.add(make_record(fit_identity(2)))
            profile.activities.add(record)

            assert profile.activities.find_by_garmin_id(GarminActivityId(77)) == record
            assert profile.activities.find_by_garmin_id(GarminActivityId(78)) is None

    def test_linking_makes_the_activity_findable_and_keeps_its_origin(
        self, harness: StorageHarness
    ) -> None:
        record = make_record(fit_identity(1))
        with closing(create(harness)) as profile:
            profile.activities.add(record)

            profile.activities.link_garmin_id(record.identity, GarminActivityId(55))

            linked = profile.activities.get(record.identity)
            assert linked is not None
            assert linked.garmin_activity_id == 55
            assert linked.origin is ActivityOrigin.IMPORT
            assert linked == profile.activities.find_by_garmin_id(GarminActivityId(55))

    def test_linking_the_same_id_again_changes_nothing(self, harness: StorageHarness) -> None:
        record = make_record(fit_identity(1))
        with closing(create(harness)) as profile:
            profile.activities.add(record)
            profile.activities.link_garmin_id(record.identity, GarminActivityId(55))
            before = profile.activities.list_by_start()

            profile.activities.link_garmin_id(record.identity, GarminActivityId(55))

            assert profile.activities.list_by_start() == before

    def test_linking_an_id_used_by_another_record_is_refused(self, harness: StorageHarness) -> None:
        with closing(create(harness)) as profile:
            profile.activities.add(make_record(fit_identity(1), garmin_activity_id=55))
            profile.activities.add(make_record(fit_identity(2)))

            with pytest.raises(DuplicateActivityError):
                profile.activities.link_garmin_id(fit_identity(2), GarminActivityId(55))

            second = profile.activities.get(fit_identity(2))
            assert second is not None
            assert second.garmin_activity_id is None

    def test_linking_a_different_id_to_a_record_that_has_one_is_refused(
        self, harness: StorageHarness
    ) -> None:
        with closing(create(harness)) as profile:
            profile.activities.add(make_record(fit_identity(1), garmin_activity_id=55))

            with pytest.raises(DuplicateActivityError):
                profile.activities.link_garmin_id(fit_identity(1), GarminActivityId(56))

            kept = profile.activities.get(fit_identity(1))
            assert kept is not None
            assert kept.garmin_activity_id == 55

    def test_linking_an_unknown_activity_is_refused(self, harness: StorageHarness) -> None:
        with closing(create(harness)) as profile, pytest.raises(ActivityNotFoundError):
            profile.activities.link_garmin_id(fit_identity(9), GarminActivityId(55))

    def test_a_linked_id_survives_closing_and_reopening(self, harness: StorageHarness) -> None:
        folder = harness.new_folder()
        profile = harness.storage.create(folder, PROFILE_ID, NAME, "Europe/Rome")
        profile.activities.add(make_record(fit_identity(1)))
        profile.activities.link_garmin_id(fit_identity(1), GarminActivityId(55))
        profile.close()

        with closing(harness.storage.open(folder)) as reopened:
            assert reopened.activities.find_by_garmin_id(GarminActivityId(55)) is not None

    def test_a_link_rolls_back_with_its_transaction(self, harness: StorageHarness) -> None:
        with closing(create(harness)) as profile:
            profile.activities.add(make_record(fit_identity(1)))

            with pytest.raises(RuntimeError, match="boom"), profile.activities.transaction():
                profile.activities.link_garmin_id(fit_identity(1), GarminActivityId(55))
                raise RuntimeError("boom")

            assert profile.activities.find_by_garmin_id(GarminActivityId(55)) is None


class SyncJournalContract:
    """The sync journal: cursors, entries and state transitions."""

    def test_new_journal_is_empty(self, harness: StorageHarness) -> None:
        with closing(create(harness)) as profile:
            journal = profile.sync_journal
            assert all(journal.load_cursor(kind) is None for kind in ListingKind)
            assert journal.next_to_process(10) == []
            assert journal.known_ids([_id(1)]) == frozenset()
            assert journal.get(_id(1)) is None
            assert journal.last_completed_at() is None
            assert dict(journal.counts()) == dict.fromkeys(SyncStatus, 0)

    def test_cursors_are_independent_per_kind_and_replaced_on_save(
        self, harness: StorageHarness
    ) -> None:
        with closing(create(harness)) as profile:
            journal = profile.sync_journal
            journal.save_cursor(ListingKind.FULL, ListingCursor(300, 5000, False))
            journal.save_cursor(ListingKind.VERIFY, ListingCursor(0, None, False))
            journal.save_cursor(ListingKind.FULL, ListingCursor(5000, 5000, True))

            assert journal.load_cursor(ListingKind.FULL) == ListingCursor(5000, 5000, True)
            assert journal.load_cursor(ListingKind.VERIFY) == ListingCursor(0, None, False)
            assert journal.load_cursor(ListingKind.INCREMENTAL) is None

    def test_added_entries_are_pending_and_returned_unchanged(
        self, harness: StorageHarness
    ) -> None:
        remote = remote_activity(1, sport_key="trail_running", has_heart_rate=None)
        with closing(create(harness)) as profile:
            added = profile.sync_journal.add_pending([remote], IMPORTED_AT)

            entry = profile.sync_journal.get(remote.garmin_id)
            assert added == 1
            assert entry == pending_entry(remote, IMPORTED_AT)

    def test_adding_the_same_activity_again_is_idempotent_and_keeps_its_state(
        self, harness: StorageHarness
    ) -> None:
        remote = remote_activity(1)
        with closing(create(harness)) as profile:
            journal = profile.sync_journal
            journal.add_pending([remote], IMPORTED_AT)
            entry = journal.get(remote.garmin_id)
            assert entry is not None
            journal.update(mark_downloaded(entry, LATER))

            added = journal.add_pending([remote, remote_activity(2)], LATER)

            kept = journal.get(remote.garmin_id)
            assert added == 1
            assert kept is not None
            assert kept.status is SyncStatus.DOWNLOADED

    def test_work_comes_newest_first_then_highest_id_and_respects_the_limit(
        self, harness: StorageHarness
    ) -> None:
        oldest = remote_activity(1, start=T0)
        newest = remote_activity(2, start=T0 + timedelta(days=2))
        tie_low = remote_activity(3, start=T0 + timedelta(days=1))
        tie_high = remote_activity(4, start=T0 + timedelta(days=1))
        with closing(create(harness)) as profile:
            journal = profile.sync_journal
            journal.add_pending([oldest, tie_low, newest, tie_high], IMPORTED_AT)

            listed = [e.remote for e in journal.next_to_process(10)]
            limited = [e.remote for e in journal.next_to_process(2)]

            assert listed == [newest, tie_high, tie_low, oldest]
            assert limited == [newest, tie_high]

    def test_work_excludes_finished_entries_and_includes_retryable_ones(
        self, harness: StorageHarness
    ) -> None:
        with closing(create(harness)) as profile:
            journal = profile.sync_journal
            journal.add_pending([remote_activity(n) for n in range(1, 6)], IMPORTED_AT)
            outcomes: dict[int, Callable[[JournalEntry], JournalEntry]] = {
                1: lambda e: mark_downloaded(e, LATER),
                2: lambda e: mark_matched(e, LATER),
                3: lambda e: mark_failed(e, FailureReason.CORRUPT_FILE, LATER, 3),
                4: lambda e: mark_failed(e, FailureReason.RATE_LIMITED, LATER, 3),
            }
            for number, outcome in outcomes.items():
                entry = journal.get(_id(number))
                assert entry is not None
                journal.update(outcome(entry))

            todo = {e.garmin_id for e in journal.next_to_process(10)}

            assert todo == {_id(4), _id(5)}

    def test_update_stores_status_attempts_failure_and_instant(
        self, harness: StorageHarness
    ) -> None:
        remote = remote_activity(1)
        with closing(create(harness)) as profile:
            journal = profile.sync_journal
            journal.add_pending([remote], IMPORTED_AT)
            entry = journal.get(remote.garmin_id)
            assert entry is not None

            failed = mark_failed(entry, FailureReason.TRANSIENT_ERROR, LATER, 3)
            journal.update(failed)

            assert journal.get(remote.garmin_id) == failed
            assert failed.status is SyncStatus.RETRYABLE
            assert failed.failed_attempts == 1

    def test_a_retryable_entry_can_fail_again_and_finally_succeed(
        self, harness: StorageHarness
    ) -> None:
        with closing(create(harness)) as profile:
            journal = profile.sync_journal
            journal.add_pending([remote_activity(1)], IMPORTED_AT)
            entry = journal.get(_id(1))
            assert entry is not None
            for _ in range(2):
                entry = mark_failed(entry, FailureReason.TRANSIENT_ERROR, IMPORTED_AT, 5)
                journal.update(entry)
            done = mark_downloaded(entry, LATER)
            journal.update(done)

            stored = journal.get(_id(1))
            assert stored == done
            assert stored is not None
            assert stored.failed_attempts == 2

    def test_a_finished_entry_cannot_move_and_nothing_changes(
        self, harness: StorageHarness
    ) -> None:
        with closing(create(harness)) as profile:
            journal = profile.sync_journal
            journal.add_pending([remote_activity(1)], IMPORTED_AT)
            entry = journal.get(_id(1))
            assert entry is not None
            failed = mark_failed(entry, FailureReason.UNSUPPORTED_FORMAT, IMPORTED_AT, 3)
            journal.update(failed)

            with pytest.raises(InvalidTransitionError):
                journal.update(mark_downloaded(entry, LATER))

            assert journal.get(_id(1)) == failed

    def test_updating_an_unknown_entry_is_refused(self, harness: StorageHarness) -> None:
        stray = mark_downloaded(pending_entry(remote_activity(9), IMPORTED_AT), LATER)
        with closing(create(harness)) as profile, pytest.raises(UnknownJournalEntryError):
            profile.sync_journal.update(stray)

    def test_counts_cover_every_status(self, harness: StorageHarness) -> None:
        with closing(create(harness)) as profile:
            journal = profile.sync_journal
            journal.add_pending([remote_activity(n) for n in range(1, 5)], IMPORTED_AT)
            first = journal.get(_id(1))
            second = journal.get(_id(2))
            assert first is not None
            assert second is not None
            journal.update(mark_downloaded(first, LATER))
            journal.update(mark_matched(second, LATER))

            assert dict(journal.counts()) == {
                SyncStatus.PENDING: 2,
                SyncStatus.DOWNLOADED: 1,
                SyncStatus.MATCHED: 1,
                SyncStatus.RETRYABLE: 0,
                SyncStatus.FAILED: 0,
            }

    def test_known_ids_include_every_status_and_ignore_the_unknown(
        self, harness: StorageHarness
    ) -> None:
        with closing(create(harness)) as profile:
            journal = profile.sync_journal
            journal.add_pending([remote_activity(1), remote_activity(2)], IMPORTED_AT)
            entry = journal.get(_id(2))
            assert entry is not None
            journal.update(mark_downloaded(entry, LATER))

            assert journal.known_ids([_id(1), _id(2), _id(3)]) == frozenset({_id(1), _id(2)})
            assert journal.known_ids([]) == frozenset()

    def test_known_ids_handle_many_ids_at_once(self, harness: StorageHarness) -> None:
        remotes = [remote_activity(n) for n in range(1, 1201)]
        with closing(create(harness)) as profile:
            journal = profile.sync_journal
            journal.add_pending(remotes, IMPORTED_AT)

            asked = [r.garmin_id for r in remotes] + [_id(5000)]

            assert journal.known_ids(asked) == frozenset(r.garmin_id for r in remotes)

    def test_next_to_process_needs_a_positive_limit(self, harness: StorageHarness) -> None:
        with closing(create(harness)) as profile, pytest.raises(ValueError, match="limit"):
            profile.sync_journal.next_to_process(0)

    def test_exhausted_entries_are_listed_newest_first_and_rearmed_to_pending(
        self, harness: StorageHarness
    ) -> None:
        with closing(create(harness)) as profile:
            journal = profile.sync_journal
            journal.add_pending([remote_activity(n) for n in range(1, 5)], IMPORTED_AT)
            for number, reason in (
                (1, FailureReason.CORRUPT_FILE),
                (2, FailureReason.TRANSIENT_ERROR),
                (3, FailureReason.TRANSIENT_ERROR),
            ):
                entry = journal.get(_id(number))
                assert entry is not None
                attempts = 3 if number != 1 else 1
                for _ in range(attempts):
                    entry = mark_failed(entry, reason, IMPORTED_AT, attempts)
                    if entry.status is SyncStatus.FAILED:
                        break
                journal.update(entry)

            exhausted = journal.next_exhausted(10)

            assert [e.garmin_id for e in exhausted] == [_id(3), _id(2)]
            assert all(e.failure is FailureReason.ATTEMPTS_EXHAUSTED for e in exhausted)
            journal.update(rearm_exhausted(exhausted[0], LATER))
            assert journal.get(_id(3)) == pending_entry(exhausted[0].remote, LATER)
            assert [e.garmin_id for e in journal.next_exhausted(10)] == [_id(2)]
            assert [e.garmin_id for e in journal.next_exhausted(1)] == [_id(2)]

    def test_unanswered_rounds_are_kept_and_the_entry_that_garmin_does_not_provide_is_rearmed(
        self, harness: StorageHarness
    ) -> None:
        with closing(create(harness)) as profile:
            journal = profile.sync_journal
            journal.add_pending([remote_activity(1), remote_activity(2)], IMPORTED_AT)
            first = journal.get(_id(1))
            assert first is not None
            first = mark_unanswered(first, IMPORTED_AT, 3)
            journal.update(first)
            assert journal.get(_id(1)) == first
            assert (first.failed_attempts, first.unanswered_rounds) == (0, 1)
            for _ in range(2):
                first = mark_unanswered(first, IMPORTED_AT, 3)
                journal.update(first)
            second = journal.get(_id(2))
            assert second is not None
            journal.update(mark_downloaded(mark_unanswered(second, IMPORTED_AT, 3), LATER))

            stuck = journal.next_exhausted(10)

            assert [e.garmin_id for e in stuck] == [_id(1)]
            assert stuck[0].failure is FailureReason.REMOTE_NOT_PROVIDED
            done = journal.get(_id(2))
            assert done is not None
            assert (done.status, done.unanswered_rounds) == (SyncStatus.DOWNLOADED, 0)
            journal.update(rearm_exhausted(stuck[0], LATER))
            assert journal.get(_id(1)) == pending_entry(stuck[0].remote, LATER)

    def test_next_exhausted_needs_a_positive_limit(self, harness: StorageHarness) -> None:
        with closing(create(harness)) as profile, pytest.raises(ValueError, match="limit"):
            profile.sync_journal.next_exhausted(0)

    def test_last_completed_instant_is_kept_and_replaced(self, harness: StorageHarness) -> None:
        with closing(create(harness)) as profile:
            profile.sync_journal.record_completed(IMPORTED_AT)
            profile.sync_journal.record_completed(LATER)

            assert profile.sync_journal.last_completed_at() == LATER

    def test_everything_survives_closing_and_reopening(self, harness: StorageHarness) -> None:
        folder = harness.new_folder()
        profile = harness.storage.create(folder, PROFILE_ID, NAME, "Europe/Rome")
        profile.sync_journal.add_pending([remote_activity(1)], IMPORTED_AT)
        entry = profile.sync_journal.get(_id(1))
        assert entry is not None
        failed = mark_failed(entry, FailureReason.RATE_LIMITED, IMPORTED_AT, 3)
        profile.sync_journal.update(failed)
        profile.sync_journal.save_cursor(ListingKind.FULL, ListingCursor(100, 900, False))
        profile.sync_journal.record_completed(IMPORTED_AT)
        profile.close()

        with closing(harness.storage.open(folder)) as reopened:
            assert reopened.sync_journal.get(_id(1)) == failed
            assert reopened.sync_journal.load_cursor(ListingKind.FULL) == ListingCursor(
                100, 900, False
            )
            assert reopened.sync_journal.last_completed_at() == IMPORTED_AT

    def test_a_journal_update_and_its_activity_commit_or_roll_back_together(
        self, harness: StorageHarness
    ) -> None:
        with closing(create(harness)) as profile:
            journal = profile.sync_journal
            journal.add_pending([remote_activity(1), remote_activity(2)], IMPORTED_AT)
            first = journal.get(_id(1))
            second = journal.get(_id(2))
            assert first is not None
            assert second is not None

            with profile.activities.transaction():
                profile.activities.add(make_record(fit_identity(1), garmin_activity_id=1001))
                journal.update(mark_downloaded(first, LATER))
            with pytest.raises(RuntimeError, match="boom"), profile.activities.transaction():
                profile.activities.add(make_record(fit_identity(2), garmin_activity_id=1002))
                journal.update(mark_downloaded(second, LATER))
                raise RuntimeError("boom")

            kept = journal.get(_id(1))
            undone = journal.get(_id(2))
            assert kept is not None
            assert undone is not None
            assert kept.status is SyncStatus.DOWNLOADED
            assert undone.status is SyncStatus.PENDING
            assert profile.activities.find_by_garmin_id(_id(2)) is None
