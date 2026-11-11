# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Sync domain: id, remote listing, journal, cursor, policy, token."""

from __future__ import annotations

import copy
import math
import pickle
import random
from collections.abc import Callable
from datetime import UTC, datetime, timedelta, timezone

import pytest
from hypothesis import given
from hypothesis import strategies as st

from ponos_coach.activities.domain import (
    ALLOWED_TRANSITIONS,
    DEFAULT_POLICY,
    MAX_GARMIN_ACTIVITY_ID,
    MAX_PAGE_SIZE,
    MAX_TOKEN_CHARS,
    REARMABLE_REASONS,
    RETRYABLE_REASONS,
    ActivityPage,
    FailureReason,
    GarminActivityId,
    InvalidTransitionError,
    JournalEntry,
    ListingCursor,
    RemoteActivity,
    SessionTokens,
    Sport,
    SyncPolicy,
    SyncStatus,
    advance_cursor,
    can_transition,
    estimate_remaining_s,
    find_matching_import,
    is_known_page,
    mark_downloaded,
    mark_failed,
    mark_matched,
    mark_unanswered,
    original_matches_listing,
    pause_between_requests_s,
    pending_entry,
    rearm_exhausted,
    retry_wait_s,
    sport_from_garmin_key,
)
from ponos_coach.activities.ports import IMPLAUSIBLE_RETRY_AFTER_S, RateLimitedError
from tests.builders import IMPORTED_AT, T0, fit_identity, make_record, remote_activity

NOW = IMPORTED_AT
RETRYABLE: list[FailureReason] = sorted(RETRYABLE_REASONS, key=lambda reason: reason.value)
DEFINITIVE: list[FailureReason] = [r for r in FailureReason if r not in RETRYABLE_REASONS]
LATER = NOW + timedelta(minutes=1)


class _Edge:
    """Random source that always gives one end of the interval (for edge cases)."""

    def __init__(self, *, high: bool) -> None:
        self._high = high

    def uniform(self, low: float, high: float) -> float:
        return high if self._high else low


NO_JITTER = _Edge(high=False)
MAX_JITTER = _Edge(high=True)


# --- Garmin id ---------------------------------------------------------------------------------


class TestGarminActivityId:
    @pytest.mark.parametrize("value", [1, 12_345_678_901, MAX_GARMIN_ACTIVITY_ID])
    def test_valid_ids(self, value: int) -> None:
        assert GarminActivityId(value).value == value

    @pytest.mark.parametrize("value", [0, -1, MAX_GARMIN_ACTIVITY_ID + 1, True, 1.0, "1"])
    def test_invalid_ids_are_refused(self, value: object) -> None:
        with pytest.raises(ValueError, match="Garmin id"):
            GarminActivityId(value)  # type: ignore[arg-type]

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [(123, 123), ("123", 123), ("9223372036854775807", MAX_GARMIN_ACTIVITY_ID)],
    )
    def test_parse_accepts_integers_and_ascii_digit_strings(
        self, raw: object, expected: int
    ) -> None:
        assert GarminActivityId.parse(raw) == GarminActivityId(expected)

    @pytest.mark.parametrize(
        "raw",
        [
            "",
            "0",
            "-1",
            " 1",
            "../1",
            "9223372036854775808",  # above the maximum
            "١٢٣",  # non-ASCII digits
            None,
            True,
        ],
    )
    def test_parse_refuses_everything_else(self, raw: object) -> None:
        with pytest.raises(ValueError, match="Garmin id"):
            GarminActivityId.parse(raw)

    @given(st.integers(min_value=1, max_value=MAX_GARMIN_ACTIVITY_ID))
    def test_parse_round_trips_the_text_form(self, value: int) -> None:
        identifier = GarminActivityId(value)

        assert GarminActivityId.parse(str(identifier)) == identifier


# --- Remote listing -----------------------------------------------------------------------------


class TestRemoteActivity:
    def test_a_valid_activity_exposes_its_sport(self) -> None:
        assert remote_activity(1, sport_key="trail_running").sport is Sport.TRAIL

    @pytest.mark.parametrize(
        "changes",
        [
            {"start": datetime(2026, 3, 2, 7, 0)},
            {"start": datetime(2026, 3, 2, 7, 0, tzinfo=timezone(timedelta(hours=1)))},
            {"start": T0 + timedelta(microseconds=1)},
            {"duration_s": -1.0},
            {"duration_s": math.nan},
            {"sport_key": "../running"},
            {"sport_key": "a" * 65},
        ],
        ids=lambda c: next(iter(c)) + str(next(iter(c.values())))[:12],
    )
    def test_invalid_summaries_are_refused(self, changes: dict[str, object]) -> None:
        with pytest.raises(ValueError, match=r"."):
            RemoteActivity(
                GarminActivityId(1),
                **{"start": T0, "duration_s": 60.0, "sport_key": "running"} | changes,  # type: ignore[arg-type]
            )

    def test_the_end_must_stay_in_the_plausible_window(self) -> None:
        with pytest.raises(ValueError, match="window"):
            RemoteActivity(
                GarminActivityId(1), datetime(2099, 12, 31, tzinfo=UTC), 10 * 86_400.0, "running"
            )

    @pytest.mark.parametrize(
        ("key", "sport"),
        [
            ("running", Sport.RUNNING),
            ("treadmill_running", Sport.RUNNING),
            ("trail_running", Sport.TRAIL),
            ("road_biking", Sport.CYCLING),
            ("indoor_cycling", Sport.CYCLING),
            ("strength_training", Sport.STRENGTH),
            ("hiking", Sport.HIKING),
            ("walking", Sport.WALKING),
            ("yoga", Sport.OTHER),
            ("qualcosa_di_nuovo", Sport.OTHER),
        ],
    )
    def test_garmin_keys_map_to_sports(self, key: str, sport: Sport) -> None:
        assert sport_from_garmin_key(key) is sport


class TestActivityPage:
    def test_skipped_count_is_the_difference(self) -> None:
        page = ActivityPage(0, (remote_activity(1),), 3, 10)

        assert page.skipped_count == 2

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"offset": -1, "items": (), "raw_count": 0},
            {"offset": 0, "items": (remote_activity(1),), "raw_count": 0},
            {"offset": 0, "items": (), "raw_count": MAX_PAGE_SIZE + 1},
            {"offset": 0, "items": (), "raw_count": 0, "declared_total": -1},
            {"offset": 0, "items": (remote_activity(1), remote_activity(1)), "raw_count": 2},
        ],
        ids=["offset", "more-items-than-raw", "too-big", "negative-total", "repeated-ids"],
    )
    def test_incoherent_pages_are_refused(self, kwargs: dict[str, object]) -> None:
        with pytest.raises(ValueError, match=r"."):
            ActivityPage(**kwargs)  # type: ignore[arg-type]


class TestFindMatchingImport:
    def test_same_sport_and_close_start_and_duration_match(self) -> None:
        record = make_record(start=T0 + timedelta(seconds=30), elapsed_s=3500.0)

        assert find_matching_import(remote_activity(1), [record]) == record

    def test_a_different_sport_does_not_match(self) -> None:
        record = make_record(sport=Sport.CYCLING)

        assert find_matching_import(remote_activity(1), [record]) is None

    def test_a_record_that_already_has_a_garmin_id_does_not_match(self) -> None:
        record = make_record(garmin_activity_id=5)

        assert find_matching_import(remote_activity(1), [record]) is None

    @pytest.mark.parametrize(
        ("offset_s", "elapsed_s", "matches"),
        [
            (60, 3600.0, True),  # on point: 60 s
            (61, 3600.0, False),  # off point
            (0, 3420.0, True),  # on point: 180 s is 5% of 3600
            (0, 3419.0, False),  # off point
        ],
    )
    def test_the_boundaries_are_those_of_is_possible_duplicate(
        self, offset_s: int, elapsed_s: float, matches: bool
    ) -> None:
        record = make_record(start=T0 + timedelta(seconds=offset_s), elapsed_s=elapsed_s)

        assert (find_matching_import(remote_activity(1), [record]) == record) is matches

    def test_the_closest_start_wins_then_the_smallest_identity(self) -> None:
        near = make_record(fit_identity(2), start=T0 + timedelta(seconds=5))
        far = make_record(fit_identity(1), start=T0 + timedelta(seconds=40))
        twin_a = make_record(fit_identity(3), start=T0 - timedelta(seconds=5))

        assert find_matching_import(remote_activity(1), [far, near]) == near
        assert (
            find_matching_import(remote_activity(1), [twin_a, near]) == near
        )  # tie: fit:..1:2 < :3

    @given(st.permutations([0, 1, 2, 3]))
    def test_the_result_does_not_depend_on_the_order_of_the_candidates(
        self, order: list[int]
    ) -> None:
        records = [
            make_record(fit_identity(1), start=T0 + timedelta(seconds=10)),
            make_record(fit_identity(2), start=T0 - timedelta(seconds=10)),
            make_record(fit_identity(4), start=T0 + timedelta(seconds=50)),
            make_record(fit_identity(3), start=T0 + timedelta(seconds=200)),
        ]

        found = find_matching_import(remote_activity(1), [records[i] for i in order])

        # The two nearest are both 10 s away: the smaller identity key wins.
        assert found == records[0]


# --- Journal ----------------------------------------------------------------------------------


def _entry(status: SyncStatus = SyncStatus.PENDING, attempts: int = 0) -> JournalEntry:
    remote = remote_activity(1)
    match status:
        case SyncStatus.PENDING:
            return pending_entry(remote, NOW)
        case SyncStatus.RETRYABLE:
            return JournalEntry(remote, status, NOW, attempts or 1, FailureReason.TRANSIENT_ERROR)
        case SyncStatus.FAILED:
            return JournalEntry(remote, status, NOW, attempts or 1, FailureReason.CORRUPT_FILE)
        case _:
            return JournalEntry(remote, status, NOW, attempts)


class TestJournalEntry:
    @pytest.mark.parametrize(
        "kwargs",
        [
            {"status": SyncStatus.RETRYABLE, "failed_attempts": 1},  # no cause
            {"status": SyncStatus.FAILED, "failed_attempts": 1},
            {"status": SyncStatus.PENDING, "failure": FailureReason.CORRUPT_FILE},
            {"status": SyncStatus.DOWNLOADED, "failure": FailureReason.CORRUPT_FILE},
            {
                "status": SyncStatus.RETRYABLE,
                "failed_attempts": 1,
                "failure": FailureReason.CORRUPT_FILE,
            },
            {
                "status": SyncStatus.FAILED,
                "failed_attempts": 1,
                "failure": FailureReason.RATE_LIMITED,
            },
            {"status": SyncStatus.PENDING, "failed_attempts": 1},
            {
                "status": SyncStatus.FAILED,
                "failed_attempts": 0,
                "failure": FailureReason.CORRUPT_FILE,
            },
            {"status": SyncStatus.DOWNLOADED, "failed_attempts": -1},
        ],
        ids=lambda k: f"{k['status'].value}-{k.get('failure')}-{k.get('failed_attempts')}",
    )
    def test_incoherent_entries_are_refused(self, kwargs: dict[str, object]) -> None:
        with pytest.raises(ValueError, match=r"."):
            JournalEntry(remote_activity(1), updated_at=NOW, **kwargs)  # type: ignore[arg-type]

    def test_the_instant_must_be_utc_and_at_the_millisecond(self) -> None:
        with pytest.raises(ValueError, match="UTC"):
            JournalEntry(remote_activity(1), SyncStatus.PENDING, datetime(2026, 1, 1))
        with pytest.raises(ValueError, match="millisecond"):
            JournalEntry(remote_activity(1), SyncStatus.PENDING, NOW + timedelta(microseconds=1))

    def test_a_downloaded_entry_after_failures_keeps_its_attempts(self) -> None:
        done = mark_downloaded(_entry(SyncStatus.RETRYABLE, attempts=2), LATER)

        assert (done.status, done.failed_attempts, done.failure) == (SyncStatus.DOWNLOADED, 2, None)


class TestRearm:
    def _exhausted(self) -> JournalEntry:
        return JournalEntry(
            remote_activity(1), SyncStatus.FAILED, NOW, 3, FailureReason.ATTEMPTS_EXHAUSTED
        )

    def test_an_exhausted_entry_goes_back_to_pending_without_attempts(self) -> None:
        rearmed = rearm_exhausted(self._exhausted(), LATER)

        assert rearmed == pending_entry(remote_activity(1), LATER)
        assert (rearmed.failed_attempts, rearmed.failure) == (0, None)

    def test_an_entry_that_garmin_does_not_provide_goes_back_to_pending_without_rounds(
        self,
    ) -> None:
        stuck = JournalEntry(
            remote_activity(1),
            SyncStatus.FAILED,
            NOW,
            0,
            FailureReason.REMOTE_NOT_PROVIDED,
            3,
        )

        rearmed = rearm_exhausted(stuck, LATER)

        assert rearmed == pending_entry(remote_activity(1), LATER)
        assert rearmed.unanswered_rounds == 0

    @pytest.mark.parametrize("status", list(SyncStatus))
    def test_any_other_entry_cannot_be_rearmed(self, status: SyncStatus) -> None:
        entry = _entry(status)

        with pytest.raises(InvalidTransitionError):
            rearm_exhausted(entry, LATER)

    @pytest.mark.parametrize("reason", [r for r in DEFINITIVE if r not in REARMABLE_REASONS])
    def test_a_definitive_failure_is_not_rearmed(self, reason: FailureReason) -> None:
        entry = JournalEntry(remote_activity(1), SyncStatus.FAILED, NOW, 1, reason)

        with pytest.raises(InvalidTransitionError):
            rearm_exhausted(entry, LATER)


class TestOriginalMatchesListing:
    def test_the_same_start_and_duration_match(self) -> None:
        remote = remote_activity(1)

        assert original_matches_listing(remote, remote.start, remote.duration_s)

    def test_a_start_a_minute_off_still_matches_but_a_day_off_does_not(self) -> None:
        remote = remote_activity(1)

        assert original_matches_listing(
            remote, remote.start + timedelta(seconds=60), remote.duration_s
        )
        assert not original_matches_listing(
            remote, remote.start + timedelta(days=1), remote.duration_s
        )

    def test_a_much_longer_file_does_not_match(self) -> None:
        remote = remote_activity(1)

        assert not original_matches_listing(remote, remote.start, remote.duration_s * 2)


class TestRateLimitedErrorHint:
    @pytest.mark.parametrize("value", [None, 0.0, 5.0, 3600.0])
    def test_a_sensible_hint_is_kept(self, value: float | None) -> None:
        assert RateLimitedError(value).retry_after_s == value

    @pytest.mark.parametrize(
        "value", [-1.0, float("-inf"), float("nan"), float("inf"), 1e12, 86_401.0]
    )
    def test_an_unusable_hint_becomes_an_implausible_wait_so_the_caller_stops(
        self, value: float
    ) -> None:
        error = RateLimitedError(value)

        assert error.retry_after_s == IMPLAUSIBLE_RETRY_AFTER_S
        assert retry_wait_s(DEFAULT_POLICY, 1, NO_JITTER, error.retry_after_s) is None


class TestTransitions:
    @pytest.mark.parametrize("old", list(SyncStatus))
    @pytest.mark.parametrize("new", list(SyncStatus))
    def test_the_allowed_transitions_are_exactly_the_table(
        self, old: SyncStatus, new: SyncStatus
    ) -> None:
        expected = (
            old in (SyncStatus.PENDING, SyncStatus.RETRYABLE) and new is not SyncStatus.PENDING
        ) or (old is SyncStatus.FAILED and new is SyncStatus.PENDING)  # only for rearm_exhausted

        assert can_transition(old, new) is expected
        assert (new in ALLOWED_TRANSITIONS[old]) is expected

    @pytest.mark.parametrize("old", [SyncStatus.PENDING, SyncStatus.RETRYABLE])
    def test_workable_entries_can_be_downloaded_matched_or_failed(self, old: SyncStatus) -> None:
        entry = _entry(old)

        assert mark_downloaded(entry, LATER).status is SyncStatus.DOWNLOADED
        assert mark_matched(entry, LATER).status is SyncStatus.MATCHED
        assert (
            mark_failed(entry, FailureReason.NOT_AN_ACTIVITY, LATER, 3).status is SyncStatus.FAILED
        )

    @pytest.mark.parametrize("old", [SyncStatus.DOWNLOADED, SyncStatus.MATCHED, SyncStatus.FAILED])
    def test_finished_entries_do_not_move(self, old: SyncStatus) -> None:
        entry = _entry(old)

        moves: list[Callable[[], JournalEntry]] = [
            lambda: mark_downloaded(entry, LATER),
            lambda: mark_matched(entry, LATER),
            lambda: mark_failed(entry, FailureReason.TRANSIENT_ERROR, LATER, 3),
        ]
        for move in moves:
            with pytest.raises(InvalidTransitionError):
                move()

    @pytest.mark.parametrize("reason", DEFINITIVE)
    def test_a_definitive_cause_fails_at_once_with_that_cause(self, reason: FailureReason) -> None:
        failed = mark_failed(_entry(), reason, LATER, 3)

        assert (failed.status, failed.failure, failed.failed_attempts) == (
            SyncStatus.FAILED,
            reason,
            1,
        )

    @pytest.mark.parametrize("reason", RETRYABLE)
    def test_a_retryable_cause_retries_until_the_limit_then_fails_for_good(
        self, reason: FailureReason
    ) -> None:
        entry = _entry()
        steps = []
        for _ in range(3):
            entry = mark_failed(entry, reason, LATER, 3)
            steps.append((entry.status, entry.failure, entry.failed_attempts))

        assert steps == [
            (SyncStatus.RETRYABLE, reason, 1),
            (SyncStatus.RETRYABLE, reason, 2),
            (SyncStatus.FAILED, FailureReason.ATTEMPTS_EXHAUSTED, 3),
        ]

    def test_with_a_single_attempt_the_first_transient_failure_is_final(self) -> None:
        failed = mark_failed(_entry(), FailureReason.TRANSIENT_ERROR, LATER, 1)

        assert failed.failure is FailureReason.ATTEMPTS_EXHAUSTED

    def test_zero_attempts_is_refused(self) -> None:
        with pytest.raises(ValueError, match="attempt"):
            mark_failed(_entry(), FailureReason.TRANSIENT_ERROR, LATER, 0)

    @given(
        limit=st.integers(min_value=1, max_value=8),
        reasons=st.lists(
            st.sampled_from(RETRYABLE),
            min_size=1,
            max_size=12,
        ),
    )
    def test_transient_failures_end_exactly_at_the_limit(
        self, limit: int, reasons: list[FailureReason]
    ) -> None:
        entry = _entry()
        for count, reason in enumerate(reasons, start=1):
            if entry.status is SyncStatus.FAILED:
                break
            entry = mark_failed(entry, reason, LATER, limit)
            assert (entry.status is SyncStatus.FAILED) == (count >= limit)
        assert entry.failed_attempts == min(len(reasons), limit)


class TestUnansweredRounds:
    def test_rounds_go_up_by_one_without_using_the_failed_attempts(self) -> None:
        entry = _entry()
        steps = []
        for _ in range(3):
            entry = mark_unanswered(entry, LATER, 3)
            steps.append(
                (entry.status, entry.failure, entry.failed_attempts, entry.unanswered_rounds)
            )

        assert steps == [
            (SyncStatus.RETRYABLE, FailureReason.TRANSIENT_ERROR, 0, 1),
            (SyncStatus.RETRYABLE, FailureReason.TRANSIENT_ERROR, 0, 2),
            (SyncStatus.FAILED, FailureReason.REMOTE_NOT_PROVIDED, 0, 3),
        ]

    def test_with_a_single_round_the_first_one_is_final(self) -> None:
        failed = mark_unanswered(_entry(), LATER, 1)

        assert failed.failure is FailureReason.REMOTE_NOT_PROVIDED

    def test_zero_rounds_is_refused(self) -> None:
        with pytest.raises(ValueError, match="round"):
            mark_unanswered(_entry(), LATER, 0)

    @pytest.mark.parametrize(
        "status", [SyncStatus.DOWNLOADED, SyncStatus.MATCHED, SyncStatus.FAILED]
    )
    def test_a_final_entry_cannot_have_another_round(self, status: SyncStatus) -> None:
        with pytest.raises(InvalidTransitionError):
            mark_unanswered(_entry(status), LATER, 3)

    def test_a_download_resets_the_count_and_keeps_the_attempts(self) -> None:
        entry = JournalEntry(
            remote_activity(1), SyncStatus.RETRYABLE, NOW, 1, FailureReason.TRANSIENT_ERROR, 2
        )

        done = mark_downloaded(entry, LATER)

        assert (done.failed_attempts, done.unanswered_rounds) == (1, 0)

    def test_a_retryable_failure_keeps_the_rounds(self) -> None:
        entry = mark_unanswered(_entry(), LATER, 3)

        failed = mark_failed(entry, FailureReason.TRANSIENT_ERROR, LATER, 3)

        assert (failed.failed_attempts, failed.unanswered_rounds) == (1, 1)

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"status": SyncStatus.PENDING, "unanswered_rounds": 1},
            {"status": SyncStatus.DOWNLOADED, "unanswered_rounds": -1},
            {"status": SyncStatus.RETRYABLE, "failure": FailureReason.TRANSIENT_ERROR},
        ],
        ids=["pending-with-rounds", "negative", "retryable-without-anything"],
    )
    def test_incoherent_counters_are_refused(self, kwargs: dict[str, object]) -> None:
        with pytest.raises(ValueError, match=r"."):
            JournalEntry(remote_activity(1), updated_at=NOW, **kwargs)  # type: ignore[arg-type]

    @given(
        limit=st.integers(min_value=1, max_value=8), rounds=st.integers(min_value=1, max_value=12)
    )
    def test_rounds_end_exactly_at_the_limit(self, limit: int, rounds: int) -> None:
        entry = _entry()
        for count in range(1, min(rounds, limit) + 1):
            entry = mark_unanswered(entry, LATER, limit)
            assert (entry.status is SyncStatus.FAILED) == (count >= limit)
        assert entry.unanswered_rounds == min(rounds, limit)
        assert entry.failed_attempts == 0


# --- Listing cursor -----------------------------------------------------------------------


def _page(
    offset: int, count: int, *, total: int | None = None, raw: int | None = None
) -> ActivityPage:
    items = tuple(remote_activity(offset + i + 1) for i in range(count))
    return ActivityPage(offset, items, count if raw is None else raw, total)


class TestListingCursor:
    def test_start_is_at_zero_and_not_completed(self) -> None:
        assert ListingCursor.start() == ListingCursor(0, None, False)

    @pytest.mark.parametrize("args", [(-1, None, False), (0, -1, False)])
    def test_negative_values_are_refused(self, args: tuple[int, int | None, bool]) -> None:
        with pytest.raises(ValueError, match="negative"):
            ListingCursor(*args)

    def test_a_full_page_moves_the_cursor_on(self) -> None:
        cursor = advance_cursor(ListingCursor.start(), _page(0, 100, total=250), 100)

        assert cursor == ListingCursor(100, 250, False)

    def test_the_total_is_remembered_when_a_later_page_does_not_repeat_it(self) -> None:
        first = advance_cursor(ListingCursor.start(), _page(0, 100, total=250), 100)

        second = advance_cursor(first, _page(100, 100), 100)

        assert second == ListingCursor(200, 250, False)

    def test_reaching_the_declared_total_does_not_complete_the_listing(self) -> None:
        # D-F2-04: the total is fixed at the first page and may be stale.
        cursor = advance_cursor(ListingCursor(100, 200, False), _page(100, 100, total=200), 100)

        assert cursor == ListingCursor(200, 200, False)

    def test_a_page_past_the_declared_total_is_still_asked_for_and_completes_when_short(
        self,
    ) -> None:
        cursor = advance_cursor(ListingCursor(200, 200, False), _page(200, 1), 100)

        assert cursor == ListingCursor(201, 200, True)

    def test_a_short_page_completes_the_listing_even_without_a_total(self) -> None:
        assert advance_cursor(ListingCursor(200, None, False), _page(200, 7), 100).completed

    def test_an_empty_page_completes_the_listing(self) -> None:
        assert advance_cursor(ListingCursor(300, None, False), _page(300, 0), 100).completed

    def test_skipped_entries_count_for_the_position_but_not_as_items(self) -> None:
        cursor = advance_cursor(ListingCursor.start(), _page(0, 90, raw=100), 100)

        assert cursor == ListingCursor(100, None, False)

    @pytest.mark.parametrize(
        ("cursor", "page", "limit"),
        [
            (ListingCursor(5, None, True), _page(5, 1), 100),  # already completed
            (ListingCursor(5, None, False), _page(0, 1), 100),  # different position
            (ListingCursor(0, None, False), _page(0, 1), 0),  # invalid limit
            (ListingCursor(0, None, False), _page(0, 1), MAX_PAGE_SIZE + 1),
            (ListingCursor(0, None, False), _page(0, 5), 4),  # page larger than the limit
        ],
        ids=["completed", "offset", "limit-zero", "limit-too-big", "page-over-limit"],
    )
    def test_inconsistent_calls_are_refused(
        self, cursor: ListingCursor, page: ActivityPage, limit: int
    ) -> None:
        with pytest.raises(ValueError, match=r"."):
            advance_cursor(cursor, page, limit)

    @given(
        total=st.integers(min_value=0, max_value=2500),
        limit=st.integers(min_value=1, max_value=MAX_PAGE_SIZE),
        declares_total=st.booleans(),
    )
    def test_walking_a_listing_reads_every_item_once_and_stops(
        self, total: int, limit: int, declares_total: bool
    ) -> None:
        cursor = ListingCursor.start()
        seen = 0
        pages = 0
        while not cursor.completed:
            raw = min(limit, total - cursor.next_offset)
            page = ActivityPage(cursor.next_offset, (), raw, total if declares_total else None)
            cursor = advance_cursor(cursor, page, limit)
            seen += raw
            pages += 1
            assert pages <= total // limit + 2
        assert seen == total


class TestIncrementalStop:
    def test_a_page_of_only_known_ids_stops_the_listing(self) -> None:
        page = _page(0, 3)
        known = {item.garmin_id for item in page.items}

        assert is_known_page(page, known)
        assert advance_cursor(ListingCursor.start(), page, 3, known).completed

    def test_a_single_new_id_keeps_the_listing_going(self) -> None:
        page = _page(0, 3)
        known = {item.garmin_id for item in page.items[:2]}

        assert not is_known_page(page, known)
        assert not advance_cursor(ListingCursor.start(), page, 3, known).completed

    def test_a_page_without_valid_items_is_not_a_known_page(self) -> None:
        page = ActivityPage(0, (), 3)

        assert not is_known_page(page, set())
        assert not advance_cursor(ListingCursor.start(), page, 3, set()).completed

    def test_without_known_ids_the_rule_does_not_apply(self) -> None:
        page = _page(0, 3)

        assert not advance_cursor(ListingCursor.start(), page, 3).completed


# --- Pause and retry policy -----------------------------------------------------------


class TestSyncPolicy:
    def test_the_defaults_are_the_project_values(self) -> None:
        policy = DEFAULT_POLICY

        assert (policy.min_pause_s, policy.max_jitter_s) == (1.0, 0.75)
        assert (policy.backoff_base_s, policy.backoff_cap_s) == (5.0, 120.0)
        assert (policy.max_attempts_per_request, policy.max_syncs_per_activity) == (5, 3)

    @pytest.mark.parametrize(
        "changes",
        [
            {"min_pause_s": 0.0},
            {"min_pause_s": math.nan},
            {"backoff_base_s": -1.0},
            {"backoff_cap_s": math.inf},
            {"max_jitter_s": -0.1},
            {"backoff_base_s": 10.0, "backoff_cap_s": 5.0},
            {"max_attempts_per_request": 0},
            {"max_syncs_per_activity": 0},
            {"page_size": 0},
            {"page_size": MAX_PAGE_SIZE + 1},
        ],
        ids=str,
    )
    def test_invalid_policies_are_refused(self, changes: dict[str, float]) -> None:
        with pytest.raises(ValueError, match=r"."):
            SyncPolicy(**changes)  # type: ignore[arg-type]

    def test_the_pause_is_the_minimum_plus_a_jitter_in_range(self) -> None:
        assert pause_between_requests_s(DEFAULT_POLICY, NO_JITTER) == 1.0
        assert pause_between_requests_s(DEFAULT_POLICY, MAX_JITTER) == 1.75

    @given(st.integers())
    def test_the_pause_with_a_real_random_source_stays_in_range(self, seed: int) -> None:
        pause = pause_between_requests_s(DEFAULT_POLICY, random.Random(seed))

        assert 1.0 <= pause <= 1.75

    def test_a_misbehaving_source_is_clamped(self) -> None:
        class Wild:
            def uniform(self, low: float, high: float) -> float:
                return max(low, high * 100)

        assert pause_between_requests_s(DEFAULT_POLICY, Wild()) == 1.75

    def test_the_backoff_doubles_from_five_seconds_and_stops_after_five_attempts(self) -> None:
        waits = [retry_wait_s(DEFAULT_POLICY, n, NO_JITTER) for n in range(1, 7)]

        assert waits == [5.0, 10.0, 20.0, 40.0, None, None]

    def test_the_backoff_is_capped(self) -> None:
        policy = SyncPolicy(max_attempts_per_request=20)

        waits = [retry_wait_s(policy, n, NO_JITTER) for n in range(1, 8)]

        assert waits == [5.0, 10.0, 20.0, 40.0, 80.0, 120.0, 120.0]
        assert retry_wait_s(policy, 19, MAX_JITTER) == 120.0  # cap even with jitter

    def test_the_jitter_adds_to_the_wait(self) -> None:
        assert retry_wait_s(DEFAULT_POLICY, 1, MAX_JITTER) == 5.75

    def test_a_suggested_wait_raises_the_floor(self) -> None:
        assert retry_wait_s(DEFAULT_POLICY, 1, NO_JITTER, suggested_s=30.0) == 30.0
        assert retry_wait_s(DEFAULT_POLICY, 3, NO_JITTER, suggested_s=2.0) == 20.0

    def test_a_suggested_wait_beyond_the_cap_means_give_up(self) -> None:
        assert retry_wait_s(DEFAULT_POLICY, 1, NO_JITTER, suggested_s=120.0) == 120.0
        assert retry_wait_s(DEFAULT_POLICY, 1, NO_JITTER, suggested_s=121.0) is None

    @pytest.mark.parametrize(
        ("failures", "suggested"),
        [(0, None), (-1, None), (1, -1.0), (1, math.nan), (1, math.inf)],
    )
    def test_invalid_arguments_are_refused(self, failures: int, suggested: float | None) -> None:
        with pytest.raises(ValueError, match=r"."):
            retry_wait_s(DEFAULT_POLICY, failures, NO_JITTER, suggested)

    @given(
        failures=st.integers(min_value=1, max_value=10_000),
        suggested=st.one_of(st.none(), st.floats(min_value=0, max_value=1000)),
        seed=st.integers(),
    )
    def test_the_wait_is_never_above_the_cap_and_ends_exactly_at_the_limits(
        self, failures: int, suggested: float | None, seed: int
    ) -> None:
        wait = retry_wait_s(DEFAULT_POLICY, failures, random.Random(seed), suggested)

        gives_up = failures >= 5 or (suggested is not None and suggested > 120.0)
        assert (wait is None) is gives_up
        if wait is not None:
            assert 5.0 <= wait <= 120.0


class TestRemainingTime:
    def test_nothing_left_takes_no_time_even_without_measures(self) -> None:
        assert estimate_remaining_s([], 0) == 0.0

    def test_no_measure_yet_means_no_estimate(self) -> None:
        assert estimate_remaining_s([], 10) is None

    def test_the_estimate_is_the_mean_of_the_last_durations_times_the_items_left(self) -> None:
        assert estimate_remaining_s([1.0, 2.0, 3.0], 10) == 20.0

    def test_only_the_last_window_counts(self) -> None:
        durations = [100.0, 100.0, 1.0, 3.0]

        assert estimate_remaining_s(durations, 5, window=2) == 10.0

    @pytest.mark.parametrize(
        ("durations", "left", "window"),
        [([1.0], -1, 20), ([1.0], 1, 0), ([-1.0], 1, 20), ([math.nan], 1, 20), ([math.inf], 1, 20)],
    )
    def test_invalid_arguments_are_refused(
        self, durations: list[float], left: int, window: int
    ) -> None:
        with pytest.raises(ValueError, match=r"."):
            estimate_remaining_s(durations, left, window)

    @given(
        durations=st.lists(st.floats(min_value=0, max_value=3600), min_size=1, max_size=60),
        left=st.integers(min_value=1, max_value=10_000),
        window=st.integers(min_value=1, max_value=30),
    )
    def test_it_matches_a_naive_moving_average(
        self, durations: list[float], left: int, window: int
    ) -> None:
        recent = durations[-window:]
        naive = sum(recent) / len(recent) * left

        estimate = estimate_remaining_s(durations, left, window)

        assert estimate is not None
        assert estimate == pytest.approx(naive, rel=1e-9, abs=1e-9)


# --- Token -------------------------------------------------------------------------------------

SECRET = "TOKEN-SEGNAPOSTO-0123456789"  # noqa: S105 - placeholder


class TestSessionTokens:
    def test_the_content_comes_out_only_from_reveal(self) -> None:
        tokens = SessionTokens(SECRET)

        assert tokens.reveal() == SECRET
        for text in (repr(tokens), str(tokens), f"{tokens}", f"{tokens!r}", "%s" % tokens):  # noqa: UP031
            assert SECRET not in text
            assert "hidden" in text

    def test_it_is_not_in_the_repr_of_containers_or_exceptions(self) -> None:
        tokens = SessionTokens(SECRET)

        assert SECRET not in repr([tokens]) + repr({"t": tokens}) + repr(ValueError(tokens))

    def test_it_cannot_be_serialized_or_copied_by_value(self) -> None:
        tokens = SessionTokens(SECRET)

        with pytest.raises(TypeError):
            pickle.dumps(tokens)
        with pytest.raises(TypeError):
            copy.deepcopy(tokens)

    def test_equality_is_by_content_and_it_is_not_hashable(self) -> None:
        assert SessionTokens(SECRET) == SessionTokens(SECRET)
        assert SessionTokens(SECRET) != SessionTokens(SECRET + "x")
        assert SessionTokens(SECRET) != SECRET
        with pytest.raises(TypeError):
            hash(SessionTokens(SECRET))

    @pytest.mark.parametrize("value", ["", "x" * (MAX_TOKEN_CHARS + 1), None, b"abc", 7])
    def test_invalid_values_are_refused_without_echoing_them(self, value: object) -> None:
        with pytest.raises(ValueError, match="invalid token") as error:
            SessionTokens(value)  # type: ignore[arg-type]
        assert "xxxx" not in str(error.value)

    def test_the_largest_token_is_accepted(self) -> None:
        assert len(SessionTokens("x" * MAX_TOKEN_CHARS).reveal()) == MAX_TOKEN_CHARS
