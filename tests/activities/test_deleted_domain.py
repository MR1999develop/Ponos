# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Domain of the deleted activities: the tombstone, the match with a remote activity, the journal
state `skipped`."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from hypothesis import given
from hypothesis import strategies as st

from ponos_coach.activities.domain import (
    ALLOWED_TRANSITIONS,
    Activity,
    ActivityOrigin,
    DeletedActivity,
    FailureReason,
    GarminActivityId,
    InvalidTransitionError,
    RemoteActivity,
    SyncStatus,
    can_transition,
    find_matching_deleted,
    is_possible_duplicate,
    mark_failed,
    mark_skipped,
    pending_entry,
)
from tests.builders import IMPORTED_AT, T0, fit_identity, make_deleted, make_record

DELETED_AT = datetime(2026, 4, 2, 9, 30, tzinfo=UTC)
LATER = IMPORTED_AT + timedelta(minutes=5)


def remote(
    number: int = 1, *, start: datetime = T0, duration_s: float = 3600.0, sport_key: str = "running"
) -> RemoteActivity:
    return RemoteActivity(GarminActivityId(1000 + number), start, duration_s, sport_key)


# --- The tombstone ------------------------------------------------------------------------


def test_a_tombstone_keeps_what_the_list_shows_and_the_reference_to_the_file() -> None:
    record = make_record(fit_identity(4), elapsed_s=1800.0)

    tombstone = DeletedActivity.of(record, DELETED_AT)

    assert tombstone.identity == record.identity
    assert (tombstone.sport, tombstone.start, tombstone.elapsed_s) == (
        record.sport,
        record.start,
        1800.0,
    )
    assert tombstone.distance_m == record.summary.distance_m
    assert tombstone.archived == record.archived
    assert (tombstone.origin, tombstone.imported_at) == (record.origin, record.imported_at)
    assert tombstone.deleted_at == DELETED_AT


@pytest.mark.parametrize(
    "change",
    [
        {"start": datetime(2026, 3, 2, 7, 0)},
        {"deleted_at": datetime(2026, 4, 2, 9, 30, 0, 500, tzinfo=UTC)},  # below the millisecond
        {"elapsed_s": 0.0},
        {"elapsed_s": float("nan")},
        {"distance_m": -1.0},
        {"distance_m": float("inf")},
        {"garmin_activity_id": 0},
        {"origin": ActivityOrigin.GARMIN, "garmin_activity_id": None},
    ],
)
def test_a_tombstone_refuses_invalid_values(change: dict[str, object]) -> None:
    with pytest.raises(ValueError, match=r"."):
        replace(make_deleted(), **change)  # type: ignore[arg-type]


def test_a_tombstone_gives_back_the_record_as_it_was_from_the_file_read_again() -> None:
    record = make_record(
        fit_identity(2), origin=ActivityOrigin.GARMIN, garmin_activity_id=55, elapsed_s=900.0
    )
    tombstone = DeletedActivity.of(record, DELETED_AT)
    # What decoding the archived file gives: the same activity as the record was made from.
    activity = Activity(
        record.identity,
        record.file_format,
        record.sport,
        record.start,
        record.elapsed_s,
        record.timer_s,
        None,
        record.summary,
    )

    assert tombstone.to_record(activity, record.archived) == record


def test_a_tombstone_refuses_the_file_of_another_activity() -> None:
    tombstone = make_deleted(make_record(fit_identity(1)))
    other = make_record(fit_identity(2))
    activity = Activity(
        other.identity,
        other.file_format,
        other.sport,
        other.start,
        other.elapsed_s,
        other.timer_s,
        None,
        other.summary,
    )

    with pytest.raises(ValueError, match="not the deleted activity"):
        tombstone.to_record(activity, other.archived)


# --- Matching a remote activity ----------------------------------------------------------


def test_no_candidates_no_match() -> None:
    assert find_matching_deleted(remote(), []) is None


def test_the_same_garmin_id_matches_whatever_the_times() -> None:
    record = make_record(
        fit_identity(1),
        start=T0 + timedelta(hours=9),
        elapsed_s=100.0,
        origin=ActivityOrigin.GARMIN,
        garmin_activity_id=1001,
    )
    tombstone = make_deleted(record)

    assert find_matching_deleted(remote(1), [tombstone]) == tombstone


def test_another_garmin_id_never_matches_even_with_the_same_times() -> None:
    record = make_record(fit_identity(1), origin=ActivityOrigin.GARMIN, garmin_activity_id=2002)

    assert find_matching_deleted(remote(1), [make_deleted(record)]) is None


@pytest.mark.parametrize(
    ("offset_s", "matches"),
    [(0, True), (60, True), (61, False), (-60, True), (-61, False)],  # on and off point of 60 s
)
def test_an_imported_activity_matches_by_start_within_sixty_seconds(
    offset_s: int, matches: bool
) -> None:
    tombstone = make_deleted(make_record(fit_identity(1), start=T0))

    found = find_matching_deleted(remote(start=T0 + timedelta(seconds=offset_s)), [tombstone])

    assert (found is not None) is matches


@pytest.mark.parametrize(
    ("duration_s", "matches"),
    [(3600.0, True), (3789.0, True), (3790.0, False), (3420.0, True), (3419.0, False)],
)
def test_an_imported_activity_matches_by_duration_within_five_percent(
    duration_s: float, matches: bool
) -> None:
    tombstone = make_deleted(make_record(fit_identity(1), elapsed_s=3600.0))

    found = find_matching_deleted(remote(duration_s=duration_s), [tombstone])

    assert (found is not None) is matches


def test_a_different_sport_does_not_match() -> None:
    tombstone = make_deleted(make_record(fit_identity(1)))

    assert find_matching_deleted(remote(sport_key="cycling"), [tombstone]) is None


def test_with_several_candidates_the_closest_start_wins_then_the_smallest_key() -> None:
    near = make_deleted(make_record(fit_identity(5), start=T0 + timedelta(seconds=5)))
    far = make_deleted(make_record(fit_identity(1), start=T0 + timedelta(seconds=30)))
    tie_a = make_deleted(make_record(fit_identity(8), start=T0 + timedelta(seconds=20)))
    tie_b = make_deleted(make_record(fit_identity(7), start=T0 - timedelta(seconds=20)))

    assert find_matching_deleted(remote(), [far, near]) == near
    assert find_matching_deleted(remote(), [tie_a, tie_b]) == tie_b


@given(
    offset_s=st.integers(min_value=-120, max_value=120),
    elapsed_s=st.integers(min_value=600, max_value=20_000),
    remote_s=st.integers(min_value=600, max_value=20_000),
)
def test_matching_a_tombstone_without_garmin_id_is_the_duplicate_rule(
    offset_s: int, elapsed_s: int, remote_s: int
) -> None:
    """Compared with the naive rule used for every other duplicate in the project."""
    start = T0 + timedelta(seconds=offset_s)
    tombstone = make_deleted(make_record(fit_identity(1), start=T0, elapsed_s=float(elapsed_s)))

    found = find_matching_deleted(remote(start=start, duration_s=float(remote_s)), [tombstone])

    assert (found is not None) == is_possible_duplicate(
        start, float(remote_s), T0, float(elapsed_s)
    )


# --- The journal state `skipped` ----------------------------------------------------------


def test_a_pending_or_retryable_entry_can_be_skipped_and_then_never_moves() -> None:
    pending = pending_entry(remote(), IMPORTED_AT)
    retryable = mark_failed(pending, FailureReason.TRANSIENT_ERROR, IMPORTED_AT, 5)

    for entry in (pending, retryable):
        skipped = mark_skipped(entry, LATER)
        assert (skipped.status, skipped.failure, skipped.updated_at) == (
            SyncStatus.SKIPPED,
            None,
            LATER,
        )
        assert ALLOWED_TRANSITIONS[SyncStatus.SKIPPED] == frozenset()
        with pytest.raises(InvalidTransitionError):
            mark_skipped(skipped, LATER)


@pytest.mark.parametrize("status", [SyncStatus.DOWNLOADED, SyncStatus.MATCHED, SyncStatus.FAILED])
def test_an_entry_in_a_final_state_cannot_be_skipped(status: SyncStatus) -> None:
    assert not can_transition(status, SyncStatus.SKIPPED)
