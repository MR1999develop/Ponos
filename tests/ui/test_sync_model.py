# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Modello di presentazione della sincronizzazione: comandi, esiti e tempo stimato."""

from __future__ import annotations

import math

import pytest

from ponos_coach.activities.domain import FailureReason, GarminActivityId, ListingKind
from ponos_coach.activities.sync import EntryFailure, StopReason, SyncMode, SyncOutcome
from ponos_coach.ui.sync_model import (
    AccountStatus,
    CommandInputs,
    DisabledReason,
    Operation,
    RemainingUnit,
    SyncFinished,
    SyncNotStarted,
    SyncProblem,
    account_status_after,
    commands_for,
    failure_counts,
    has_definitive_failures,
    has_retryable_failures,
    needs_attention,
    needs_reconnect,
    new_activity_count,
    round_remaining,
)


def finished(
    *,
    stop: StopReason | None = None,
    downloaded: int = 0,
    matched: int = 0,
    already_present: int = 0,
    failures: tuple[EntryFailure, ...] = (),
) -> SyncFinished:
    outcome = SyncOutcome(
        ListingKind.FULL, stop, downloaded, downloaded, matched, failures, {}, already_present
    )
    return SyncFinished(outcome, SyncMode.SYNC, automatic=False)


def failure(number: int, reason: FailureReason) -> EntryFailure:
    return EntryFailure(GarminActivityId(1000 + number), reason)


def inputs(
    account: AccountStatus = AccountStatus.LINKED,
    *,
    profile_open: bool = True,
    operation: Operation | None = None,
    closing: bool = False,
) -> CommandInputs:
    return CommandInputs(profile_open, account, operation, closing)


# --- Comandi -----


def test_with_a_linked_account_sync_check_and_disconnect_are_available() -> None:
    commands = commands_for(inputs())

    assert commands.sync.enabled
    assert commands.verify.enabled
    assert commands.disconnect.enabled
    assert commands.connect.reason is DisabledReason.ALREADY_LINKED
    assert commands.stop.reason is DisabledReason.NOT_SYNCING


def test_without_an_account_only_connect_is_available() -> None:
    commands = commands_for(inputs(AccountStatus.NOT_LINKED))

    assert commands.connect.enabled
    assert commands.sync.reason is DisabledReason.NOT_LINKED
    assert commands.verify.reason is DisabledReason.NOT_LINKED
    assert commands.disconnect.reason is DisabledReason.NOT_LINKED


def test_expired_tokens_invite_to_connect_again_and_allow_disconnecting() -> None:
    commands = commands_for(inputs(AccountStatus.NEEDS_RECONNECT))

    assert commands.connect.enabled
    assert commands.disconnect.enabled
    assert commands.sync.reason is DisabledReason.NEEDS_RECONNECT


def test_without_a_keychain_every_account_command_says_why() -> None:
    commands = commands_for(inputs(AccountStatus.KEYCHAIN_UNAVAILABLE))

    for availability in (commands.connect, commands.disconnect, commands.sync, commands.verify):
        assert availability.reason is DisabledReason.KEYCHAIN_UNAVAILABLE


def test_without_a_profile_every_account_command_is_off_with_the_reason() -> None:
    commands = commands_for(inputs(AccountStatus.UNKNOWN, profile_open=False))

    for availability in (commands.connect, commands.disconnect, commands.sync, commands.verify):
        assert availability.reason is DisabledReason.NO_PROFILE


def test_while_syncing_only_stop_is_available() -> None:
    commands = commands_for(inputs(operation=Operation.SYNC))

    assert commands.stop.enabled
    for availability in (commands.connect, commands.disconnect, commands.sync, commands.verify):
        assert availability.reason is DisabledReason.BUSY


def test_while_checking_the_account_the_reason_is_the_check() -> None:
    commands = commands_for(inputs(AccountStatus.UNKNOWN, operation=Operation.CHECK))

    assert commands.sync.reason is DisabledReason.CHECKING
    assert commands.connect.reason is DisabledReason.CHECKING


@pytest.mark.parametrize("operation", [Operation.LOGIN, Operation.LOGOUT])
def test_while_signing_in_or_out_nothing_else_starts(operation: Operation) -> None:
    commands = commands_for(inputs(operation=operation))

    assert commands.sync.reason is DisabledReason.BUSY
    assert commands.stop.reason is DisabledReason.NOT_SYNCING


def test_when_closing_nothing_is_available_not_even_stop() -> None:
    commands = commands_for(inputs(operation=Operation.SYNC, closing=True))

    assert not commands.stop.enabled
    assert not commands.sync.enabled


# --- Esiti -----


def test_only_downloaded_activities_count_as_new() -> None:
    assert new_activity_count(finished(downloaded=3, matched=2)) == 3
    assert new_activity_count(SyncNotStarted(SyncProblem.NOT_LINKED, SyncMode.SYNC, False)) == 0


def test_a_clean_or_cancelled_round_does_not_need_a_window() -> None:
    assert not needs_attention(finished(downloaded=2))
    assert not needs_attention(finished(stop=StopReason.CANCELLED, downloaded=1))


@pytest.mark.parametrize(
    "stop", [reason for reason in StopReason if reason is not StopReason.CANCELLED]
)
def test_every_other_stop_reason_needs_attention(stop: StopReason) -> None:
    assert needs_attention(finished(stop=stop))


def test_failed_entries_and_problems_need_attention() -> None:
    assert needs_attention(finished(failures=(failure(1, FailureReason.CORRUPT_FILE),)))
    assert needs_attention(SyncNotStarted(SyncProblem.UNEXPECTED, SyncMode.SYNC, True))


def test_authentication_trouble_asks_to_connect_again_in_both_shapes() -> None:
    assert needs_reconnect(finished(stop=StopReason.AUTHENTICATION_REQUIRED))
    assert needs_reconnect(SyncNotStarted(SyncProblem.AUTH_REQUIRED, SyncMode.SYNC, False))
    assert not needs_reconnect(finished(stop=StopReason.RATE_LIMITED))


def test_the_account_status_follows_what_the_round_learned() -> None:
    linked = AccountStatus.LINKED
    expired = SyncNotStarted(SyncProblem.AUTH_REQUIRED, SyncMode.SYNC, False)
    gone = SyncNotStarted(SyncProblem.NOT_LINKED, SyncMode.SYNC, False)
    locked = SyncNotStarted(SyncProblem.KEYCHAIN_UNAVAILABLE, SyncMode.SYNC, False)

    assert account_status_after(expired, linked) is AccountStatus.NEEDS_RECONNECT
    assert account_status_after(gone, linked) is AccountStatus.NOT_LINKED
    assert account_status_after(locked, linked) is AccountStatus.KEYCHAIN_UNAVAILABLE
    assert account_status_after(finished(downloaded=1), linked) is linked
    assert (
        account_status_after(finished(stop=StopReason.AUTHENTICATION_REQUIRED), linked)
        is AccountStatus.NEEDS_RECONNECT
    )


def test_failures_are_grouped_by_reason_most_frequent_first() -> None:
    result = finished(
        failures=(
            failure(1, FailureReason.CORRUPT_FILE),
            failure(2, FailureReason.UNSUPPORTED_FORMAT),
            failure(3, FailureReason.UNSUPPORTED_FORMAT),
            failure(4, FailureReason.ARCHIVE_INVALID),
        )
    )

    assert failure_counts(result) == (
        (FailureReason.UNSUPPORTED_FORMAT, 2),
        (FailureReason.CORRUPT_FILE, 1),
        (FailureReason.ARCHIVE_INVALID, 1),
    )
    assert failure_counts(SyncNotStarted(SyncProblem.UNEXPECTED, SyncMode.SYNC, False)) == ()


# --- Tempo stimato -----


@pytest.mark.parametrize(
    ("seconds", "unit", "amount"),
    [
        (0.0, RemainingUnit.UNDER_A_MINUTE, 0),
        (59.9, RemainingUnit.UNDER_A_MINUTE, 0),
        (60.0, RemainingUnit.MINUTES, 1),
        (61.0, RemainingUnit.MINUTES, 2),
        (89 * 60.0, RemainingUnit.MINUTES, 89),
        (90 * 60.0, RemainingUnit.HOURS, 2),
        (5 * 3600.0, RemainingUnit.HOURS, 5),
    ],
)
def test_remaining_time_is_rounded_to_a_comfortable_unit(
    seconds: float, unit: RemainingUnit, amount: int
) -> None:
    rounded = round_remaining(seconds)

    assert (rounded.unit, rounded.amount) == (unit, amount)


@pytest.mark.parametrize("seconds", [-1.0, math.inf, math.nan])
def test_remaining_time_must_be_finite_and_not_negative(seconds: float) -> None:
    with pytest.raises(ValueError, match="finito"):
        round_remaining(seconds)


def test_files_already_in_the_profile_are_not_new_activities() -> None:
    assert new_activity_count(finished(downloaded=2, matched=1, already_present=5)) == 2
    assert new_activity_count(finished(already_present=5)) == 0


def test_failures_split_into_temporary_and_lasting_ones() -> None:
    temporary = finished(failures=(failure(1, FailureReason.TRANSIENT_ERROR),))
    lasting = finished(failures=(failure(2, FailureReason.CORRUPT_FILE),))
    both = finished(
        failures=(failure(3, FailureReason.RATE_LIMITED), failure(4, FailureReason.CORRUPT_FILE))
    )

    assert (has_retryable_failures(temporary), has_definitive_failures(temporary)) == (True, False)
    assert (has_retryable_failures(lasting), has_definitive_failures(lasting)) == (False, True)
    assert (has_retryable_failures(both), has_definitive_failures(both)) == (True, True)
    assert not has_retryable_failures(finished())
    assert not has_definitive_failures(SyncNotStarted(SyncProblem.UNEXPECTED, SyncMode.SYNC, False))


def test_the_auto_sync_switch_needs_a_profile_but_not_an_idle_account() -> None:
    assert commands_for(inputs(operation=Operation.SYNC)).auto_sync.enabled
    assert commands_for(inputs(AccountStatus.NOT_LINKED)).auto_sync.enabled
    assert commands_for(inputs(profile_open=False)).auto_sync.reason is DisabledReason.NO_PROFILE
    assert (
        commands_for(inputs(AccountStatus.KEYCHAIN_UNAVAILABLE)).auto_sync.reason
        is DisabledReason.KEYCHAIN_UNAVAILABLE
    )
