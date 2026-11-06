# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Sincronizzazione: errori, ritentativi, autenticazione e annullamento.

T-SYNC-03 (429 con e senza attesa suggerita, transitori, risposte malformate: backoff entro i
tetti, nessun ciclo infinito), T-SYNC-04 (autenticazione richiesta a metà), T-SYNC-06 (annullo),
più un test a proprietà sugli invarianti con interruzioni a caso. Orologio e attesa finti.
"""

from __future__ import annotations

from collections import Counter

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
    AuthenticationRequiredError,
    DefinitiveSourceError,
    RateLimitedError,
    ServiceUnavailableError,
    TransientSourceError,
)
from ponos_coach.activities.sync import EntryFailure, StopReason, SyncPhase, SyncProgress
from tests.fakes.garmin import Call, OriginalKind
from tests.fixtures.generators.sync_harness import SyncRig, in_memory_rig, remote_id

PAUSE_S = 1.375  # pausa minima 1 s più metà del jitter massimo
BACKOFF_S = [5.375, 10.375, 20.375, 40.375]  # 5 s che raddoppia, più metà del jitter
CAP_S = 120.0


def attempts_on(rig: SyncRig, number: int) -> int:
    return Counter(rig.fake.downloads)[remote_id(number)]


def list_offsets(rig: SyncRig) -> list[int | None]:
    return [c.offset for c in rig.fake.calls if c.call is Call.LIST_PAGE]


# --- T-SYNC-03: limite di frequenza e transitori -----------------------------------------------


def test_t_sync_03_a_429_without_a_suggested_wait_backs_off_exponentially_then_succeeds() -> None:
    rig = in_memory_rig(2)
    rig.fake.fail_download_of(remote_id(1), RateLimitedError(), times=2)

    outcome = rig.run()

    assert outcome.completed
    assert outcome.downloaded == 2
    # pausa prima del primo download, due backoff, pausa prima del download seguente
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
    assert outcome.failures == (EntryFailure(remote_id(2), FailureReason.RATE_LIMITED),)
    assert rig.status_of(2) is SyncStatus.RETRYABLE  # si riprova alla prossima volta
    assert rig.status_of(1) is SyncStatus.PENDING
    assert rig.wait.waits == [PAUSE_S]  # nessuna attesa lunga
    assert rig.journal.last_completed_at() is None


def test_t_sync_03_a_permanent_429_gives_up_after_the_attempt_limit_within_the_cap() -> None:
    rig = in_memory_rig(3)
    rig.fake.fail_download_of(remote_id(2), RateLimitedError())

    outcome = rig.run()

    assert outcome.stop is StopReason.RATE_LIMITED
    assert attempts_on(rig, 2) == 5  # max_attempts_per_request
    assert rig.wait.waits == [PAUSE_S, *BACKOFF_S]
    assert max(rig.wait.waits) <= CAP_S
    assert rig.status_of(2) is SyncStatus.RETRYABLE
    assert len(rig.fake.downloads) == 5  # le altre voci non sono state toccate


def test_t_sync_03_a_transient_error_on_one_entry_does_not_stop_the_others() -> None:
    rig = in_memory_rig(3)
    rig.fake.fail_download_of(remote_id(1), TransientSourceError("timeout"))

    outcome = rig.run()

    assert outcome.completed  # tutte le voci sono state tentate
    assert outcome.downloaded == 2
    assert outcome.failures == (EntryFailure(remote_id(1), FailureReason.TRANSIENT_ERROR),)
    assert rig.status_of(1) is SyncStatus.RETRYABLE
    assert rig.journal.last_completed_at() is not None
    assert attempts_on(rig, 1) == 5


def test_t_sync_03_a_retryable_entry_is_retried_on_the_next_sync_and_keeps_its_attempts() -> None:
    rig = in_memory_rig(2)
    rig.fake.fail_download_of(remote_id(1), TransientSourceError("timeout"), times=5)
    rig.run()

    outcome = rig.run()

    assert (outcome.completed, outcome.downloaded) == (True, 1)
    entry = rig.journal.get(remote_id(1))
    assert entry is not None
    assert (entry.status, entry.failed_attempts) == (SyncStatus.DOWNLOADED, 1)


def test_t_sync_03_an_entry_failing_in_three_syncs_fails_for_good() -> None:
    rig = in_memory_rig(2)
    rig.fake.fail_download_of(remote_id(1), TransientSourceError("timeout"))

    for _ in range(3):
        rig.run()

    entry = rig.journal.get(remote_id(1))
    assert entry is not None
    assert (entry.status, entry.failure) == (SyncStatus.FAILED, FailureReason.ATTEMPTS_EXHAUSTED)
    rig.run()
    assert attempts_on(rig, 1) == 15  # tre sincronizzazioni da cinque tentativi, poi basta


def test_t_sync_03_a_service_that_stays_down_stops_after_three_entries() -> None:
    rig = in_memory_rig(10)
    for number in range(10):
        rig.fake.fail_download_of(remote_id(number), ServiceUnavailableError("non risponde"))

    outcome = rig.run()

    assert outcome.stop is StopReason.SERVICE_UNAVAILABLE
    assert len(rig.fake.downloads) == 15  # tre voci per cinque tentativi, non dieci voci
    assert outcome.failures_by_reason() == {FailureReason.TRANSIENT_ERROR: 3}
    assert outcome.status_counts[SyncStatus.PENDING] == 7
    assert max(rig.wait.waits) <= CAP_S
    assert rig.journal.last_completed_at() is None


def test_t_sync_03_a_transient_error_while_listing_is_retried_then_stops_clearly() -> None:
    rig = in_memory_rig(5)
    for call in range(1, 6):
        rig.fake.fail_call(Call.LIST_PAGE, call, TransientSourceError("troncata"))

    outcome = rig.run()

    assert outcome.stop is StopReason.SERVICE_UNAVAILABLE
    assert list_offsets(rig) == [0] * 5
    assert rig.wait.waits == BACKOFF_S  # nessuna pausa prima della prima richiesta
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
    rig.fake.fail_call(Call.LIST_PAGE, 1, DefinitiveSourceError("risposta non interpretabile"))

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
            raise AssertionError("non si scarica nulla")

    rig = in_memory_rig(0)
    rig.source = WrongOffsetSource()

    outcome = rig.run()

    assert outcome.stop is StopReason.REMOTE_ERROR
    assert WrongOffsetSource.calls == 1


def test_t_sync_03_malformed_entries_are_skipped_and_the_cursor_still_advances() -> None:
    rig = in_memory_rig(10, policy=SyncPolicy(page_size=5))
    rig.fake.malformed([remote_id(n) for n in range(5, 10)])  # tutta la prima pagina

    outcome = rig.run()

    assert (outcome.completed, outcome.downloaded) == (True, 5)
    assert list_offsets(rig) == [0, 5]
    assert rig.journal.get(remote_id(7)) is None


def test_t_sync_03_a_history_made_only_of_malformed_entries_still_terminates() -> None:
    rig = in_memory_rig(10, policy=SyncPolicy(page_size=5))
    rig.fake.declares_total = False
    rig.fake.malformed([remote_id(n) for n in range(10)])

    outcome = rig.run()

    assert outcome.completed
    assert (outcome.new_entries, outcome.downloaded) == (0, 0)
    assert list_offsets(rig) == [0, 5, 10]  # l'ultima pagina, vuota, chiude l'elenco


# --- T-SYNC-04: autenticazione richiesta ---------------------------------------------------------


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

    resumed = rig.run()  # dopo il nuovo login

    assert (resumed.completed, resumed.downloaded) == (True, 3)
    assert len(rig.records()) == 6
    assert all(count == 1 for count in Counter(rig.fake.downloads).values())


def test_t_sync_04_authentication_required_while_listing_resumes_from_the_cursor() -> None:
    rig = in_memory_rig(25, policy=SyncPolicy(page_size=10))
    rig.fake.fail_call(Call.LIST_PAGE, 2, AuthenticationRequiredError("token scaduto"))

    outcome = rig.run()

    assert outcome.stop is StopReason.AUTHENTICATION_REQUIRED
    assert outcome.new_entries == 10
    assert rig.fake.downloads == []  # prima l'elenco
    assert rig.journal.load_cursor(ListingKind.FULL) == ListingCursor(10, 25, False)

    resumed = rig.run()

    assert list_offsets(rig) == [0, 10, 10, 20]  # riparte dal cursore, non da capo
    assert (resumed.completed, resumed.new_entries, resumed.downloaded) == (True, 15, 25)


# --- T-SYNC-06: annullamento ---------------------------------------------------------------------


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
            cancelled = True  # l'utente preme Annulla mentre si lavora la quarta voce

    calls_when_cancelled = 0

    def is_cancelled() -> bool:
        nonlocal calls_when_cancelled
        if cancelled and calls_when_cancelled == 0:
            calls_when_cancelled = len(rig.fake.calls)
        return cancelled

    outcome = rig.run(report=on_progress, is_cancelled=is_cancelled)

    assert outcome.cancelled
    assert not outcome.completed
    assert len(rig.fake.calls) == calls_when_cancelled  # nessuna chiamata dopo l'annullamento
    assert outcome.downloaded == 3
    assert len(rig.records()) == 3  # indice e giornale concordano
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
    rig.wait.cancel_on_wait(2)  # la prima è la pausa, la seconda il backoff

    outcome = rig.run()

    assert outcome.cancelled
    assert outcome.failures == ()
    assert rig.status_of(1) is SyncStatus.PENDING
    assert attempts_on(rig, 1) == 1


def test_t_sync_06_cancelling_during_the_listing_keeps_the_pages_already_read() -> None:
    rig = in_memory_rig(25, policy=SyncPolicy(page_size=10))

    def is_cancelled() -> bool:
        return len(rig.fake.calls) >= 1  # dopo la prima pagina

    outcome = rig.run(is_cancelled=is_cancelled)

    assert outcome.cancelled
    assert outcome.new_entries == 10
    assert rig.journal.load_cursor(ListingKind.FULL) == ListingCursor(10, 25, False)
    assert rig.fake.downloads == []

    resumed = rig.run()
    assert (resumed.completed, resumed.downloaded) == (True, 25)
    assert list_offsets(rig) == [0, 10, 20]


# --- Invarianti con interruzioni a caso ----------------------------------------------------------


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
    # Un annullamento non cade mai tra download e conferma: ogni originale si scarica una volta.
    assert all(times == 1 for times in Counter(rig.fake.downloads).values())
