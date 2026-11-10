# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Sync: errors, retries, authentication and cancellation.

T-SYNC-03 (429 with and without suggested wait, transient errors, malformed responses: backoff
within the caps, no endless loop), T-SYNC-04 (authentication required midway), T-SYNC-06
(cancel), plus a property test on the invariants with random interruptions. Fake clock and wait.
"""

from __future__ import annotations

from collections import Counter
from contextlib import AbstractContextManager

from hypothesis import given, settings
from hypothesis import strategies as st

from ponos_coach.activities.domain import (
    ActivityPage,
    FailureReason,
    ListingCursor,
    ListingKind,
    SyncPolicy,
    SyncStatus,
)
from ponos_coach.activities.ports import (
    ActivityIndex,
    AuthenticationRequiredError,
    DefinitiveSourceError,
    RateLimitedError,
    ServiceUnavailableError,
    StorageBusyError,
    TransientSourceError,
)
from ponos_coach.activities.sync import (
    EntryFailure,
    StopReason,
    SyncMode,
    SyncPhase,
    SyncProgress,
)
from tests.activities.import_fakes import ObservedIndex, OrderLog
from tests.fakes.garmin import Call, OriginalKind, original_zip
from tests.fixtures.generators.sync_harness import SyncRig, in_memory_rig, remote_id

PAUSE_S = 1.375  # minimum pause 1 s plus half the maximum jitter
BACKOFF_S = [5.375, 10.375, 20.375, 40.375]  # 5 s doubling, plus half the jitter
CAP_S = 120.0


def attempts_on(rig: SyncRig, number: int) -> int:
    return Counter(rig.fake.downloads)[remote_id(number)]


def list_offsets(rig: SyncRig) -> list[int | None]:
    return [c.offset for c in rig.fake.calls if c.call is Call.LIST_PAGE]


# --- T-SYNC-03: rate limit and transient errors -----------------------------------------------


def test_t_sync_03_a_429_without_a_suggested_wait_backs_off_exponentially_then_succeeds() -> None:
    rig = in_memory_rig(2)
    rig.fake.fail_download_of(remote_id(1), RateLimitedError(), times=2)

    outcome = rig.run()

    assert outcome.completed
    assert outcome.downloaded == 2
    # pause before the first download, two backoffs, pause before the next download
    assert rig.wait.waits == [PAUSE_S, *BACKOFF_S[:2], PAUSE_S]
    assert attempts_on(rig, 1) == 3


def test_t_sync_03_a_suggested_wait_raises_the_minimum_of_the_backoff() -> None:
    rig = in_memory_rig(2)
    rig.fake.fail_download_of(remote_id(1), RateLimitedError(30.0), times=1)

    outcome = rig.run()

    assert outcome.completed
    assert rig.wait.waits == [PAUSE_S, 30.375, PAUSE_S]


def test_t_sync_03_a_suggested_wait_over_the_cap_stops_at_once_with_a_clear_outcome() -> None:
    rig = in_memory_rig(3)
    rig.fake.fail_download_of(remote_id(2), RateLimitedError(500.0))

    outcome = rig.run()

    assert outcome.stop is StopReason.RATE_LIMITED
    assert not outcome.completed
    assert outcome.failures == ()  # a round stopped by the limit consumes no attempts (R1)
    assert rig.status_of(2) is SyncStatus.PENDING  # retried next time
    assert rig.status_of(1) is SyncStatus.PENDING
    assert rig.wait.waits == [PAUSE_S]  # no long wait
    assert rig.journal.last_completed_at() is None


def test_t_sync_03_a_permanent_429_gives_up_after_the_attempt_limit_within_the_cap() -> None:
    rig = in_memory_rig(3)
    rig.fake.fail_download_of(remote_id(2), RateLimitedError())

    outcome = rig.run()

    assert outcome.stop is StopReason.RATE_LIMITED
    assert attempts_on(rig, 2) == 5  # max_attempts_per_request
    assert rig.wait.waits == [PAUSE_S, *BACKOFF_S]
    assert max(rig.wait.waits) <= CAP_S
    assert rig.status_of(2) is SyncStatus.PENDING  # no attempt consumed (R1)
    assert len(rig.fake.downloads) == 5  # the other entries were not touched


def test_t_sync_03_a_transient_error_on_one_entry_does_not_stop_the_others() -> None:
    rig = in_memory_rig(3)
    rig.fake.fail_download_of(remote_id(1), TransientSourceError("timeout"))

    outcome = rig.run()

    assert outcome.completed  # all entries were attempted
    assert outcome.downloaded == 2
    assert outcome.failures == (EntryFailure(remote_id(1), FailureReason.TRANSIENT_ERROR),)
    assert rig.status_of(1) is SyncStatus.RETRYABLE
    assert rig.journal.last_completed_at() is not None
    assert attempts_on(rig, 1) == 5


def test_t_sync_03_a_retryable_entry_is_retried_on_the_next_sync_and_its_rounds_reset() -> None:
    rig = in_memory_rig(2)
    rig.fake.fail_download_of(remote_id(1), TransientSourceError("timeout"), times=5)
    rig.run()

    outcome = rig.run()

    assert (outcome.completed, outcome.downloaded) == (True, 1)
    entry = rig.journal.get(remote_id(1))
    assert entry is not None
    # the silent round did not use an attempt, and the download cleared the count
    assert (entry.status, entry.failed_attempts, entry.unanswered_rounds) == (
        SyncStatus.DOWNLOADED,
        0,
        0,
    )


def test_t_sync_03_an_entry_garmin_does_not_serve_in_three_syncs_fails_for_good() -> None:
    rig = in_memory_rig(2)
    rig.fake.fail_download_of(remote_id(1), TransientSourceError("timeout"))

    for _ in range(3):
        rig.run()

    entry = rig.journal.get(remote_id(1))
    assert entry is not None
    assert (entry.status, entry.failure, entry.unanswered_rounds) == (
        SyncStatus.FAILED,
        FailureReason.REMOTE_NOT_PROVIDED,
        3,
    )
    assert entry.failed_attempts == 0  # the attempts of the entry are not used
    rig.run()
    assert attempts_on(rig, 1) == 15  # three syncs of five attempts, then stop


def test_t_sync_03_a_service_that_stays_down_stops_after_three_entries() -> None:
    rig = in_memory_rig(10)
    for number in range(10):
        rig.fake.fail_download_of(remote_id(number), ServiceUnavailableError("non risponde"))

    outcome = rig.run()

    assert outcome.stop is StopReason.SERVICE_UNAVAILABLE
    assert len(rig.fake.downloads) == 15  # three entries for five attempts, not ten entries
    # Garmin still answers the list (the probe): the three entries that stayed silent have one
    # unanswered round each, the other seven were not touched.
    assert outcome.failures_by_reason() == {FailureReason.TRANSIENT_ERROR: 3}
    assert outcome.status_counts[SyncStatus.PENDING] == 7
    assert outcome.status_counts[SyncStatus.RETRYABLE] == 3
    assert max(rig.wait.waits) <= CAP_S
    assert rig.journal.last_completed_at() is None


def test_t_sync_03_a_transient_error_while_listing_is_retried_then_stops_clearly() -> None:
    rig = in_memory_rig(5)
    for call in range(1, 6):
        rig.fake.fail_call(Call.LIST_PAGE, call, TransientSourceError("troncata"))

    outcome = rig.run()

    assert outcome.stop is StopReason.SERVICE_UNAVAILABLE
    assert list_offsets(rig) == [0] * 5
    assert rig.wait.waits == BACKOFF_S  # no pause before the first request
    assert sum(outcome.status_counts.values()) == 0
    assert rig.journal.load_cursor(ListingKind.FULL) is None


def test_t_sync_03_a_listing_error_that_passes_is_absorbed() -> None:
    rig = in_memory_rig(5)
    rig.fake.fail_call(Call.LIST_PAGE, 1, TransientSourceError("troncata"))

    outcome = rig.run()

    assert (outcome.completed, outcome.downloaded) == (True, 5)
    assert list_offsets(rig) == [0, 0]


def test_t_sync_03_a_429_while_listing_with_a_long_wait_stops_with_the_rate_limit() -> None:
    rig = in_memory_rig(5)
    rig.fake.fail_call(Call.LIST_PAGE, 1, RateLimitedError(900.0))

    outcome = rig.run()

    assert outcome.stop is StopReason.RATE_LIMITED
    assert rig.wait.waits == []
    assert rig.fake.downloads == []


def test_t_sync_03_a_rejected_listing_stops_with_a_remote_error() -> None:
    rig = in_memory_rig(5)
    rig.fake.fail_call(Call.LIST_PAGE, 1, DefinitiveSourceError("response not interpretable"))

    outcome = rig.run()

    assert outcome.stop is StopReason.REMOTE_ERROR
    assert rig.fake.downloads == []


def test_t_sync_03_a_page_outside_the_contract_is_a_remote_error_not_a_loop() -> None:
    class WrongOffsetSource:
        calls = 0

        def list_page(self, offset: int, _limit: int) -> ActivityPage:
            WrongOffsetSource.calls += 1
            return ActivityPage(offset + 1, (), 0)

        def download_original(self, _activity_id: object, _max_bytes: int) -> bytes:
            raise AssertionError("nothing must be downloaded")

    rig = in_memory_rig(0)
    rig.source = WrongOffsetSource()

    outcome = rig.run()

    assert outcome.stop is StopReason.REMOTE_ERROR
    assert WrongOffsetSource.calls == 1


def test_t_sync_03_malformed_entries_are_skipped_and_the_cursor_still_advances() -> None:
    rig = in_memory_rig(10, policy=SyncPolicy(page_size=5))
    rig.fake.malformed([remote_id(n) for n in range(5, 10)])  # the whole first page

    outcome = rig.run()

    assert (outcome.completed, outcome.downloaded) == (True, 5)
    assert list_offsets(rig) == [0, 5, 10]  # an empty page closes the listing
    assert rig.journal.get(remote_id(7)) is None


def test_t_sync_03_a_history_made_only_of_malformed_entries_still_terminates() -> None:
    rig = in_memory_rig(10, policy=SyncPolicy(page_size=5))
    rig.fake.declares_total = False
    rig.fake.malformed([remote_id(n) for n in range(10)])

    outcome = rig.run()

    assert outcome.completed
    assert (outcome.new_entries, outcome.downloaded) == (0, 0)
    assert list_offsets(rig) == [0, 5, 10]  # the last page, empty, closes the listing


# --- T-SYNC-04: authentication required ---------------------------------------------------------


def test_t_sync_04_authentication_required_mid_download_stops_without_failing_entries() -> None:
    rig = in_memory_rig(6)
    rig.fake.fail_call(Call.DOWNLOAD, 4, AuthenticationRequiredError("token scaduto"))

    outcome = rig.run()

    assert outcome.stop is StopReason.AUTHENTICATION_REQUIRED
    assert outcome.failures == ()
    assert outcome.downloaded == 3
    assert [rig.status_of(n) for n in (5, 4, 3)] == [SyncStatus.DOWNLOADED] * 3
    assert [rig.status_of(n) for n in (2, 1, 0)] == [SyncStatus.PENDING] * 3
    assert rig.journal.last_completed_at() is None

    resumed = rig.run()  # after the new login

    assert (resumed.completed, resumed.downloaded) == (True, 3)
    assert len(rig.records()) == 6
    assert all(count == 1 for count in Counter(rig.fake.downloads).values())


def test_t_sync_04_authentication_required_while_listing_resumes_from_the_cursor() -> None:
    rig = in_memory_rig(25, policy=SyncPolicy(page_size=10))
    rig.fake.fail_call(Call.LIST_PAGE, 2, AuthenticationRequiredError("token scaduto"))

    outcome = rig.run()

    assert outcome.stop is StopReason.AUTHENTICATION_REQUIRED
    assert outcome.new_entries == 10
    assert rig.fake.downloads == []  # the listing first
    assert rig.journal.load_cursor(ListingKind.FULL) == ListingCursor(10, 25, False)

    resumed = rig.run()

    assert list_offsets(rig) == [0, 10, 10, 20]  # restarts from the cursor, not from scratch
    assert (resumed.completed, resumed.new_entries, resumed.downloaded) == (True, 15, 25)


# --- T-SYNC-06: cancellation ---------------------------------------------------------------------


def test_t_sync_06_a_sync_cancelled_before_it_starts_makes_no_call() -> None:
    rig = in_memory_rig(5)

    outcome = rig.run(is_cancelled=lambda: True)

    assert outcome.cancelled
    assert rig.fake.calls == []
    assert sum(outcome.status_counts.values()) == 0
    assert rig.journal.last_completed_at() is None


def test_t_sync_06_cancelling_stops_before_the_next_call_and_leaves_a_coherent_state() -> None:
    rig = in_memory_rig(8)
    cancelled = False

    def on_progress(progress: SyncProgress) -> None:
        nonlocal cancelled
        if progress.phase is SyncPhase.DOWNLOADING and progress.done == 3:
            cancelled = True  # the user presses Cancel while the fourth entry is being processed

    calls_when_cancelled = 0

    def is_cancelled() -> bool:
        nonlocal calls_when_cancelled
        if cancelled and calls_when_cancelled == 0:
            calls_when_cancelled = len(rig.fake.calls)
        return cancelled

    outcome = rig.run(report=on_progress, is_cancelled=is_cancelled)

    assert outcome.cancelled
    assert not outcome.completed
    assert len(rig.fake.calls) == calls_when_cancelled  # no call after the cancellation
    assert outcome.downloaded == 3
    assert len(rig.records()) == 3  # index and journal agree
    assert outcome.status_counts[SyncStatus.DOWNLOADED] == 3
    assert outcome.status_counts[SyncStatus.PENDING] == 5
    assert rig.journal.last_completed_at() is None

    resumed = rig.run()

    assert (resumed.completed, resumed.downloaded) == (True, 5)
    assert len(rig.records()) == 8
    assert all(count == 1 for count in Counter(rig.fake.downloads).values())


def test_t_sync_06_cancelling_during_a_backoff_wait_leaves_the_entry_untouched() -> None:
    rig = in_memory_rig(2)
    rig.fake.fail_download_of(remote_id(1), RateLimitedError(), times=2)
    rig.wait.cancel_on_wait(2)  # the first is the pause, the second the backoff

    outcome = rig.run()

    assert outcome.cancelled
    assert outcome.failures == ()
    assert rig.status_of(1) is SyncStatus.PENDING
    assert attempts_on(rig, 1) == 1


def test_t_sync_06_cancelling_during_the_listing_keeps_the_pages_already_read() -> None:
    rig = in_memory_rig(25, policy=SyncPolicy(page_size=10))

    def is_cancelled() -> bool:
        return len(rig.fake.calls) >= 1  # after the first page

    outcome = rig.run(is_cancelled=is_cancelled)

    assert outcome.cancelled
    assert outcome.new_entries == 10
    assert rig.journal.load_cursor(ListingKind.FULL) == ListingCursor(10, 25, False)
    assert rig.fake.downloads == []

    resumed = rig.run()
    assert (resumed.completed, resumed.downloaded) == (True, 25)
    assert list_offsets(rig) == [0, 10, 20]


# --- Invariants with random interruptions ---------------------------------------------------------


@settings(max_examples=60, deadline=None)
@given(
    count=st.integers(min_value=1, max_value=12),
    page_size=st.integers(min_value=1, max_value=6),
    bad=st.sets(st.integers(min_value=0, max_value=11)),
    interruptions=st.lists(st.integers(min_value=0, max_value=60), max_size=4),
)
def test_however_the_runs_are_interrupted_every_good_activity_is_recorded_exactly_once(
    count: int, page_size: int, bad: set[int], interruptions: list[int]
) -> None:
    rig = in_memory_rig(count, policy=SyncPolicy(page_size=page_size))
    bad = {n for n in bad if n < count}
    for number in bad:
        rig.fake.set_kind(remote_id(number), OriginalKind.TCX)

    for allowed_checks in interruptions:
        checks = 0

        def cancelled_after(limit: int = allowed_checks) -> bool:
            nonlocal checks
            checks += 1
            return checks > limit

        rig.run(is_cancelled=cancelled_after)
    final = rig.run()

    assert final.completed
    good = count - len(bad)
    records = rig.records()
    assert len(records) == good
    assert len({r.garmin_activity_id for r in records}) == good
    assert len(rig.memory.files) == good
    assert final.status_counts[SyncStatus.DOWNLOADED] == good
    assert final.status_counts[SyncStatus.FAILED] == len(bad)
    assert final.status_counts[SyncStatus.PENDING] == 0
    # A cancellation never falls between download and confirmation: each original downloads once.
    assert all(times == 1 for times in Counter(rig.fake.downloads).values())


# --- R1: a round stopped by the network consumes no attempts --------------------------------------


def test_r1_runs_stopped_by_the_rate_limit_never_exhaust_the_attempts_of_a_valid_entry() -> None:
    rig = in_memory_rig(2)
    rig.fake.fail_download_of(remote_id(1), RateLimitedError(), times=20)  # four rounds of five

    for _ in range(4):
        outcome = rig.run()
        assert outcome.stop is StopReason.RATE_LIMITED

    entry = rig.journal.get(remote_id(1))
    assert entry is not None
    assert (entry.status, entry.failed_attempts) == (SyncStatus.PENDING, 0)
    assert rig.run().completed
    assert rig.status_of(1) is SyncStatus.DOWNLOADED


def test_r1_failures_held_back_by_an_unreachable_service_are_dropped_not_written() -> None:
    rig = in_memory_rig(6)
    for number in (5, 4, 3):  # the three most recent entries: the service is silent
        rig.fake.fail_download_of(
            remote_id(number), ServiceUnavailableError("non risponde"), times=5
        )
    for call in range(2, 7):  # and the probe after them (call 1 is the list) gets no answer
        rig.fake.fail_call(Call.LIST_PAGE, call, ServiceUnavailableError("non risponde"))

    outcome = rig.run()

    assert outcome.stop is StopReason.SERVICE_UNAVAILABLE
    assert outcome.failures == ()
    assert all(rig.status_of(n) is SyncStatus.PENDING for n in range(6))
    assert rig.run().completed and rig.status_of(5) is SyncStatus.DOWNLOADED


def test_r1_silent_entries_count_a_round_when_the_probe_shows_garmin_answers() -> None:
    rig = in_memory_rig(6)
    for number in (5, 4, 3):
        rig.fake.fail_download_of(remote_id(number), ServiceUnavailableError("no answer"), times=5)

    outcome = rig.run()

    assert outcome.stop is StopReason.SERVICE_UNAVAILABLE
    assert [rig.fake.calls[-1].call, rig.fake.calls[-1].offset] == [Call.LIST_PAGE, 0]  # the probe
    for number in (5, 4, 3):
        entry = rig.journal.get(remote_id(number))
        assert entry is not None
        assert (entry.status, entry.failed_attempts, entry.unanswered_rounds) == (
            SyncStatus.RETRYABLE,
            0,
            1,
        )
    assert all(rig.status_of(n) is SyncStatus.PENDING for n in range(3))


def test_r1_a_429_does_not_count_a_round_nor_an_attempt() -> None:
    rig = in_memory_rig(3)
    rig.fake.fail_download_of(remote_id(2), RateLimitedError())

    for _ in range(4):
        assert rig.run().stop is StopReason.RATE_LIMITED

    entry = rig.journal.get(remote_id(2))
    assert entry is not None
    assert (entry.status, entry.failed_attempts, entry.unanswered_rounds) == (
        SyncStatus.PENDING,
        0,
        0,
    )


def test_r1_a_failure_is_written_once_garmin_answers_for_a_later_entry() -> None:
    rig = in_memory_rig(3)
    rig.fake.fail_download_of(remote_id(2), TransientSourceError("timeout"))

    outcome = rig.run()

    assert outcome.completed
    assert outcome.failures == (EntryFailure(remote_id(2), FailureReason.TRANSIENT_ERROR),)
    assert rig.status_of(2) is SyncStatus.RETRYABLE


def test_r1_the_full_verification_rearms_entries_that_garmin_did_not_provide() -> None:
    rig = in_memory_rig(2)
    rig.fake.fail_download_of(remote_id(1), TransientSourceError("timeout"), times=15)
    for _ in range(3):
        rig.run()
    entry = rig.journal.get(remote_id(1))
    assert entry is not None and entry.failure is FailureReason.REMOTE_NOT_PROVIDED
    assert rig.run().downloaded == 0  # a normal sync does not retry it

    outcome = rig.run(SyncMode.VERIFY)

    assert (outcome.completed, outcome.downloaded) == (True, 1)
    rearmed = rig.journal.get(remote_id(1))
    assert rearmed is not None
    assert (rearmed.status, rearmed.failed_attempts, rearmed.unanswered_rounds) == (
        SyncStatus.DOWNLOADED,
        0,
        0,
    )


def test_r1_the_verification_leaves_definitive_failures_alone() -> None:
    rig = in_memory_rig(2)
    rig.fake.set_kind(remote_id(1), OriginalKind.CORRUPT_FIT)
    rig.run()

    outcome = rig.run(SyncMode.VERIFY)

    assert outcome.downloaded == 0
    entry = rig.journal.get(remote_id(1))
    assert entry is not None and entry.status is SyncStatus.FAILED


# --- R2: busy database ------------------------------------------------------------------------


class _BusyAfter(ObservedIndex):
    """An index whose `transaction` gives `StorageBusyError` from the `busy_from`-th on."""

    def __init__(self, inner: ActivityIndex, busy_from: int) -> None:
        super().__init__(inner, OrderLog())
        self._busy_from = busy_from
        self._opened = 0

    def transaction(self) -> AbstractContextManager[None]:
        self._opened += 1
        if self._opened >= self._busy_from:
            raise StorageBusyError("database occupato")
        return super().transaction()


def test_r2_a_busy_database_stops_the_sync_with_a_storage_error_and_touches_no_entry() -> None:
    rig = in_memory_rig(3)
    real_index = rig.index
    rig.index = _BusyAfter(
        real_index, busy_from=2
    )  # the listing page goes through, the entry does not

    outcome = rig.run()

    assert outcome.stop is StopReason.STORAGE_ERROR
    assert not outcome.completed
    assert outcome.failures == ()
    assert all(rig.status_of(n) is SyncStatus.PENDING for n in range(3))
    rig.index = real_index
    assert rig.run().completed
    assert len(rig.records()) == 3
    assert len(rig.memory.files) == 3  # the file already written is not duplicated


# --- D-F2-03: the downloaded file must be the entry's one ---------------------------------


def test_d_f2_03_a_download_of_another_activity_fails_the_entry_for_good_and_is_not_recorded() -> (
    None
):
    rig = in_memory_rig(3)
    rig.fake.set_original(remote_id(1), original_zip(2))  # Garmin gives the file of run 2

    outcome = rig.run()

    assert outcome.completed
    assert outcome.failures == (EntryFailure(remote_id(1), FailureReason.REMOTE_REJECTED),)
    assert rig.status_of(1) is SyncStatus.FAILED
    assert rig.status_of(0) is SyncStatus.DOWNLOADED
    assert outcome.downloaded == 2
    assert len(rig.records()) == 2
