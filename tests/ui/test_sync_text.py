# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Testi della sincronizzazione: ogni motivo ha una frase in parole comuni (sorgente inglese)."""

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
    failures: tuple[EntryFailure, ...] = (),
) -> SyncFinished:
    outcome = SyncOutcome(ListingKind.FULL, stop, downloaded, downloaded, matched, failures, {})
    return SyncFinished(outcome, SyncMode.SYNC, automatic=False)


def assert_finished_text(text: str) -> None:
    """Un testo finito: non vuoto, senza segnaposto rimasti né `%n` non risolti."""
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
    assert downloading == "Downloading activity 5 of 20 - about 3 minute(s) left"


def test_the_time_left_is_omitted_until_it_is_known() -> None:
    assert progress_text(SyncProgress(SyncPhase.DOWNLOADING, 0, 20, None)) == (
        "Downloading activity 1 of 20"
    )


def test_remaining_time_in_words() -> None:
    assert remaining_text(10.0) == "less than a minute left"
    assert remaining_text(7200.0) == "about 2 hour(s) left"


def test_the_notification_carries_the_number_of_new_activities() -> None:
    title, message = notification_texts(5)

    assert title == "New activities from Garmin"
    assert "5" in message


def test_the_headline_tells_how_it_went() -> None:
    assert headline_text(finished()) == "Garmin is up to date"
    assert headline_text(finished(downloaded=3)) == "Synchronised: 3 new activity(ies)"
    assert headline_text(finished(downloaded=3, matched=2)) == (
        "Synchronised: 3 new activity(ies), 2 matched with imported files"
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

    assert lines[0] == "4 new activity(ies) downloaded"
    assert lines[1] == "1 activity(ies) matched with files you imported"
    assert lines[2] == "3 activity(ies) not downloaded:"
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
