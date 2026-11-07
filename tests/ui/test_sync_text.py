# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Synchronization texts: each reason has a sentence in plain words (English source)."""

from __future__ import annotations

import pytest

from ponos_coach.activities.domain import FailureReason, GarminActivityId, ListingKind
from ponos_coach.activities.sync import (
    EntryFailure,
    StopReason,
    SyncMode,
    SyncOutcome,
    SyncPhase,
    SyncProgress,
)
from ponos_coach.ui.sync_model import (
    DisabledReason,
    LoginFailure,
    SyncFinished,
    SyncNotStarted,
    SyncProblem,
)
from ponos_coach.ui.sync_text import (
    disabled_reason_text,
    failure_reason_text,
    headline_text,
    login_failure_text,
    notification_texts,
    problem_text,
    progress_text,
    remaining_text,
    stop_reason_text,
    summary_lines,
    summary_message,
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


def assert_finished_text(text: str) -> None:
    """A finished text: not empty, with no placeholders left or unresolved `%n`."""
    assert text.strip()
    assert "{" not in text
    assert "%n" not in text


@pytest.mark.parametrize("reason", list(FailureReason))
def test_every_failure_reason_has_a_plain_sentence(reason: FailureReason) -> None:
    assert_finished_text(failure_reason_text(reason))


@pytest.mark.parametrize("reason", list(StopReason))
def test_every_stop_reason_has_a_plain_sentence(reason: StopReason) -> None:
    assert_finished_text(stop_reason_text(reason))


@pytest.mark.parametrize("problem", list(SyncProblem))
def test_every_problem_has_a_plain_sentence(problem: SyncProblem) -> None:
    assert_finished_text(problem_text(problem))


@pytest.mark.parametrize("reason", list(DisabledReason))
def test_every_disabled_command_says_why(reason: DisabledReason) -> None:
    assert_finished_text(disabled_reason_text(reason))


@pytest.mark.parametrize("failure", list(LoginFailure))
def test_every_login_failure_says_what_to_do(failure: LoginFailure) -> None:
    assert_finished_text(login_failure_text(failure))


def test_the_progress_names_the_phase_the_counter_and_the_time_left() -> None:
    listing = progress_text(SyncProgress(SyncPhase.LISTING, 200, 1000))
    unknown_total = progress_text(SyncProgress(SyncPhase.LISTING, 200, None))
    downloading = progress_text(SyncProgress(SyncPhase.DOWNLOADING, 4, 20, 125.0))

    assert listing == "Listing activities: 200 of 1000"
    assert unknown_total == "Listing activities: 200"
    assert downloading == "Downloading activity 5 of 20 - about 3 min left"


def test_the_time_left_is_omitted_until_it_is_known() -> None:
    assert progress_text(SyncProgress(SyncPhase.DOWNLOADING, 0, 20, None)) == (
        "Downloading activity 1 of 20"
    )


def test_remaining_time_in_words() -> None:
    assert remaining_text(10.0) == "less than a minute left"
    assert remaining_text(7200.0) == "about 2 h left"


def test_the_notification_carries_the_number_of_new_activities() -> None:
    title, message = notification_texts(5)

    assert title == "New activities from Garmin"
    assert "5" in message


def test_the_headline_tells_how_it_went() -> None:
    assert headline_text(finished()) == "Garmin is up to date"
    assert headline_text(finished(downloaded=3)) == "New activities synchronised: 3"
    assert headline_text(finished(downloaded=3, matched=2)) == (
        "New activities synchronised: 3, matched with imported files: 2"
    )
    assert headline_text(finished(stop=StopReason.CANCELLED)) == "Synchronisation stopped"
    assert (
        headline_text(SyncNotStarted(SyncProblem.NOT_LINKED, SyncMode.SYNC, False))
        == "Synchronisation did not start"
    )


def test_the_summary_lists_new_matched_failed_with_reasons_and_the_stop() -> None:
    failures = (
        EntryFailure(GarminActivityId(1), FailureReason.UNSUPPORTED_FORMAT),
        EntryFailure(GarminActivityId(2), FailureReason.UNSUPPORTED_FORMAT),
        EntryFailure(GarminActivityId(3), FailureReason.CORRUPT_FILE),
    )
    result = finished(
        stop=StopReason.SERVICE_UNAVAILABLE, downloaded=4, matched=1, failures=failures
    )

    lines = summary_lines(result)

    assert lines[0] == "New activities downloaded: 4"
    assert lines[1] == "Activities matched with files you imported: 1"
    assert lines[2] == "Activities not downloaded: 3"
    assert lines[3] == "  the file format is not supported (for example TCX): 2"
    assert lines[4] == "  the file is damaged: 1"
    assert lines[5] == stop_reason_text(StopReason.SERVICE_UNAVAILABLE)
    for line in lines:
        assert_finished_text(line)


def test_the_summary_message_never_carries_ids_or_internal_names() -> None:
    result = finished(
        failures=(EntryFailure(GarminActivityId(123456789), FailureReason.ARCHIVE_INVALID),)
    )

    message = summary_message(result)

    assert "123456789" not in f"{message.title}{message.what}{message.remedy}"
    assert "ARCHIVE" not in message.what


def test_files_already_in_the_profile_are_said_apart_and_never_called_new() -> None:
    result = finished(downloaded=1, already_present=4)

    lines = summary_lines(result)

    assert lines[0] == "New activities downloaded: 1"
    assert lines[1] == "Activities already in the profile: 4"
    assert headline_text(result) == "New activities synchronised: 1"
    assert headline_text(finished(already_present=4)) == "Garmin is up to date"


def test_a_rejected_original_covers_both_causes_in_one_sentence() -> None:
    assert failure_reason_text(FailureReason.REMOTE_REJECTED) == (
        "Garmin did not provide a valid original file for this activity"
    )


def test_a_storage_stop_says_the_profile_may_be_busy_and_to_try_again() -> None:
    text = stop_reason_text(StopReason.STORAGE_ERROR)

    assert "busy" in text
    assert "Try again" in text


def test_the_remedy_keeps_temporary_failures_apart_from_lasting_ones() -> None:
    temporary = (EntryFailure(GarminActivityId(1), FailureReason.TRANSIENT_ERROR),)
    lasting = (EntryFailure(GarminActivityId(2), FailureReason.CORRUPT_FILE),)

    only_temporary = summary_message(finished(failures=temporary)).remedy
    only_lasting = summary_message(finished(failures=lasting)).remedy
    both = summary_message(finished(failures=temporary + lasting)).remedy

    assert "tried again next time" in only_temporary
    assert "not tried again" not in only_temporary
    assert "not tried again" in only_lasting
    assert "stay in the list" not in only_lasting
    assert "tried again next time" in both
    assert "not tried again" in both


def test_a_stopped_round_also_says_it_resumes_where_it_left_off() -> None:
    message = summary_message(finished(stop=StopReason.SERVICE_UNAVAILABLE))

    assert "resumes where it left off" in message.remedy
