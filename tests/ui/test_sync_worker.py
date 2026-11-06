# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Il lavoro dell'account Garmin senza finestra: token, accesso, uscita e un giro.

Fake in memoria per Garmin, portachiavi, database del profilo e archivio; nessuna rete.
"""

from __future__ import annotations

import logging
import threading
import uuid
from pathlib import Path

import pytest

from ponos_coach.__main__ import extract_original_activity
from ponos_coach.activities.decoding import decode_stored_activity
from ponos_coach.activities.domain import SessionTokens, SyncPolicy, SyncStatus
from ponos_coach.activities.ports import (
    AuthenticationRequiredError,
    GarminSession,
    RateLimitedError,
    ServiceUnavailableError,
)
from ponos_coach.activities.sync import StopReason, SyncMode, SyncProgress
from ponos_coach.profiles.domain import ProfileId, ProfileName
from ponos_coach.profiles.ports import ProfileNotFoundError
from ponos_coach.ui.sync_model import (
    AccountStatus,
    LoginEnd,
    LoginFailure,
    SyncFinished,
    SyncNotStarted,
    SyncProblem,
)
from ponos_coach.ui.sync_worker import Credentials, MfaMailbox, SyncServices, SyncWorker
from tests.fakes.archive import InMemoryActivityArchive
from tests.fakes.garmin import (
    PLACEHOLDER_TOKEN,
    TEST_EMAIL,
    TEST_MFA_CODE,
    TEST_PASSWORD,
    Call,
    FakeActivitySource,
    FakeGarminAccount,
    make_remote_history,
)
from tests.fakes.notifier import RecordingNotifier
from tests.fakes.profile_storage import InMemoryProfileStorage
from tests.fakes.tokens import InMemoryTokenStore
from tests.fixtures.generators.sync_harness import FakeSyncClock, MidpointRandom

PROFILE_ID = ProfileId(uuid.UUID("00000000-0000-4000-8000-000000000001"))
FOLDER = Path("profili") / "prova"
FAST = SyncPolicy(min_pause_s=0.001, max_jitter_s=0.0, backoff_base_s=0.01, backoff_cap_s=0.05)


class Rig:
    """Un worker con fake: Garmin, account, portachiavi, database e archivio in memoria."""

    def __init__(
        self,
        *,
        activities: int = 3,
        mfa_required: bool = False,
        tokens: bool = True,
        account_class: type[FakeGarminAccount] = FakeGarminAccount,
    ) -> None:
        self.source = FakeActivitySource(make_remote_history(activities))
        self.account = account_class(self.source, mfa_required=mfa_required)
        self.store = InMemoryTokenStore()
        self.storage = InMemoryProfileStorage()
        self.storage.create(FOLDER, PROFILE_ID, ProfileName.parse("Atleta di prova"), "UTC")
        self.archive = InMemoryActivityArchive()
        self.services = SyncServices(
            account=self.account,
            tokens=self.store,
            extract_original=extract_original_activity,
            decode=decode_stored_activity,
            clock=FakeSyncClock(),
            make_rng=MidpointRandom,
            notifier=RecordingNotifier(),
            policy=FAST,
        )
        self.worker = SyncWorker(self.services, self.storage, lambda _folder: self.archive)
        if tokens:
            self.link()

    def link(self) -> None:
        """Collega l'account come farebbe l'utente (i token sono quelli che Garmin riconosce)."""
        result = self.worker.log_in(
            PROFILE_ID,
            Credentials(TEST_EMAIL, TEST_PASSWORD),
            lambda: TEST_MFA_CODE,
            threading.Event(),
        )
        assert result is LoginEnd.CONNECTED

    def synchronize(
        self, mode: SyncMode = SyncMode.SYNC, cancelled: threading.Event | None = None
    ) -> tuple[object, list[SyncProgress]]:
        seen: list[SyncProgress] = []
        result = self.worker.synchronize(
            PROFILE_ID,
            FOLDER,
            mode,
            automatic=False,
            cancelled=cancelled or threading.Event(),
            report=seen.append,
        )
        return result, seen


# --- Controllo dei token -----


def test_check_reads_the_saved_tokens_of_the_profile() -> None:
    rig = Rig(tokens=False)
    assert rig.worker.check(PROFILE_ID) is AccountStatus.NOT_LINKED

    rig.link()

    assert rig.worker.check(PROFILE_ID) is AccountStatus.LINKED


def test_check_tells_a_damaged_store_from_an_unavailable_one() -> None:
    rig = Rig()
    rig.store.corrupt(PROFILE_ID)
    assert rig.worker.check(PROFILE_ID) is AccountStatus.NEEDS_RECONNECT

    rig.store.unavailable = True

    assert rig.worker.check(PROFILE_ID) is AccountStatus.KEYCHAIN_UNAVAILABLE


def test_without_a_keychain_nothing_can_be_linked_or_read() -> None:
    rig = Rig(tokens=False)
    services = SyncServices(
        rig.account,
        None,
        extract_original_activity,
        decode_stored_activity,
        FakeSyncClock(),
        MidpointRandom,
        RecordingNotifier(),
    )
    worker = SyncWorker(services, rig.storage, lambda _folder: rig.archive)

    assert worker.check(PROFILE_ID) is AccountStatus.KEYCHAIN_UNAVAILABLE
    assert not worker.log_out(PROFILE_ID)
    login = worker.log_in(
        PROFILE_ID, Credentials(TEST_EMAIL, TEST_PASSWORD), lambda: None, threading.Event()
    )
    assert login is LoginFailure.KEYCHAIN_UNAVAILABLE
    assert rig.account.login_calls == 0


def test_log_out_deletes_the_tokens_and_a_keychain_failure_is_reported() -> None:
    rig = Rig()

    assert rig.worker.log_out(PROFILE_ID)
    assert PROFILE_ID not in rig.store.saved

    rig.store.unavailable = True
    assert not rig.worker.log_out(PROFILE_ID)


# --- Accesso -----


def test_login_without_mfa_saves_the_tokens_for_the_profile() -> None:
    rig = Rig(tokens=False)

    result = rig.worker.log_in(
        PROFILE_ID, Credentials(TEST_EMAIL, TEST_PASSWORD), lambda: None, threading.Event()
    )

    assert result is LoginEnd.CONNECTED
    assert rig.store.saved[PROFILE_ID].reveal() == PLACEHOLDER_TOKEN
    assert rig.account.mfa_prompts == 0


def test_login_with_mfa_asks_for_the_code_once_and_saves_the_tokens() -> None:
    rig = Rig(tokens=False, mfa_required=True)
    codes = iter([TEST_MFA_CODE])

    result = rig.worker.log_in(
        PROFILE_ID, Credentials(TEST_EMAIL, TEST_PASSWORD), lambda: next(codes), threading.Event()
    )

    assert result is LoginEnd.CONNECTED
    assert rig.account.mfa_prompts == 1
    assert PROFILE_ID in rig.store.saved


def test_a_cancelled_mfa_saves_nothing_and_is_not_a_failure() -> None:
    rig = Rig(tokens=False, mfa_required=True)

    result = rig.worker.log_in(
        PROFILE_ID, Credentials(TEST_EMAIL, TEST_PASSWORD), lambda: None, threading.Event()
    )

    assert result is LoginEnd.CANCELLED
    assert rig.store.saved == {}


def test_a_wrong_code_or_password_is_a_credentials_failure_and_saves_nothing() -> None:
    rig = Rig(tokens=False, mfa_required=True)
    wrong_code = rig.worker.log_in(
        PROFILE_ID, Credentials(TEST_EMAIL, TEST_PASSWORD), lambda: "000000", threading.Event()
    )
    wrong_password = rig.worker.log_in(
        PROFILE_ID, Credentials(TEST_EMAIL, "altra"), lambda: TEST_MFA_CODE, threading.Event()
    )

    assert wrong_code is LoginFailure.INVALID_CREDENTIALS
    assert wrong_password is LoginFailure.INVALID_CREDENTIALS
    assert rig.store.saved == {}


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (RateLimitedError(30.0), LoginFailure.RATE_LIMITED),
        (ServiceUnavailableError("offline"), LoginFailure.SERVICE_UNAVAILABLE),
        (RuntimeError("imprevisto"), LoginFailure.OTHER),
    ],
)
def test_other_login_failures_are_told_apart_by_what_the_user_can_do(
    error: Exception, expected: LoginFailure
) -> None:
    rig = Rig(tokens=False)
    rig.account.fail_login_with(error)

    result = rig.worker.log_in(
        PROFILE_ID, Credentials(TEST_EMAIL, TEST_PASSWORD), lambda: None, threading.Event()
    )

    assert result is expected
    assert rig.store.saved == {}


def test_a_keychain_that_cannot_save_means_the_account_is_not_connected() -> None:
    rig = Rig(tokens=False)
    rig.store.unavailable = True

    result = rig.worker.log_in(
        PROFILE_ID, Credentials(TEST_EMAIL, TEST_PASSWORD), lambda: None, threading.Event()
    )

    assert result is LoginFailure.KEYCHAIN_UNAVAILABLE


def test_a_login_cancelled_while_garmin_answers_is_thrown_away() -> None:
    rig = Rig(tokens=False)
    cancelled = threading.Event()
    cancelled.set()

    result = rig.worker.log_in(
        PROFILE_ID, Credentials(TEST_EMAIL, TEST_PASSWORD), lambda: None, cancelled
    )

    assert result is LoginEnd.CANCELLED
    assert rig.store.saved == {}


def test_the_password_is_not_in_the_credentials_repr_nor_in_the_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    rig = Rig(tokens=False)
    rig.account.fail_login_with(RuntimeError(f"boom {TEST_PASSWORD} {TEST_EMAIL}"))
    credentials = Credentials(TEST_EMAIL, TEST_PASSWORD)

    with caplog.at_level(logging.DEBUG):
        result = rig.worker.log_in(PROFILE_ID, credentials, lambda: None, threading.Event())

    assert result is LoginFailure.OTHER
    assert TEST_PASSWORD not in repr(credentials)
    assert TEST_PASSWORD not in caplog.text
    assert TEST_EMAIL not in caplog.text
    assert "RuntimeError" in caplog.text  # del guasto si scrive il tipo


# --- Attesa del codice MFA -----


def test_the_mailbox_hands_over_the_first_answer_only() -> None:
    box = MfaMailbox(1.0)
    box.answer("123456")
    box.answer("999999")

    assert box.wait() == "123456"


def test_cancelling_the_mailbox_or_answering_none_releases_the_wait() -> None:
    cancelled = MfaMailbox(5.0)
    cancelled.cancel()
    declined = MfaMailbox(5.0)
    declined.answer(None)

    assert cancelled.wait() is None
    assert declined.wait() is None


def test_an_unanswered_mailbox_gives_up_after_its_limit() -> None:
    assert MfaMailbox(0.01).wait() is None


# --- Sincronizzazione -----


def test_a_round_downloads_everything_with_its_own_connection_and_reports_progress() -> None:
    rig = Rig(activities=3)

    result, seen = rig.synchronize()

    assert isinstance(result, SyncFinished)
    assert result.outcome.completed
    assert result.outcome.downloaded == 3
    assert seen[0].phase.value == "listing"
    assert seen[-1].phase.value == "downloading"
    assert rig.storage.open(FOLDER).activities.list_by_start().__len__() == 3


def test_a_second_round_has_nothing_new() -> None:
    rig = Rig(activities=2)
    rig.synchronize()

    result, _ = rig.synchronize()

    assert isinstance(result, SyncFinished)
    assert result.outcome.downloaded == 0
    assert result.outcome.status_counts[SyncStatus.DOWNLOADED] == 2


def test_the_cancellation_event_stops_a_round_cleanly() -> None:
    rig = Rig(activities=3)
    cancelled = threading.Event()
    cancelled.set()

    result, _ = rig.synchronize(cancelled=cancelled)

    assert isinstance(result, SyncFinished)
    assert result.outcome.stop is StopReason.CANCELLED


@pytest.mark.parametrize(
    ("setup", "problem"),
    [
        ("unlink", SyncProblem.NOT_LINKED),
        ("corrupt", SyncProblem.AUTH_REQUIRED),
        ("unavailable", SyncProblem.KEYCHAIN_UNAVAILABLE),
        ("revoked", SyncProblem.AUTH_REQUIRED),
        ("rate_limited", SyncProblem.RATE_LIMITED),
        ("offline", SyncProblem.SERVICE_UNAVAILABLE),
    ],
)
def test_a_round_that_cannot_start_says_why(setup: str, problem: SyncProblem) -> None:
    rig = Rig()
    if setup == "unlink":
        rig.store.saved.clear()
    elif setup == "corrupt":
        rig.store.corrupt(PROFILE_ID)
    elif setup == "unavailable":
        rig.store.unavailable = True
    elif setup == "revoked":
        rig.account.revoke_tokens()
    elif setup == "rate_limited":
        rig.account.rate_limit()
    else:
        rig.account.go_offline()

    result, _ = rig.synchronize()

    assert result == SyncNotStarted(problem, SyncMode.SYNC, False)
    assert rig.source.calls == []  # nessuna richiesta a Garmin


def test_a_profile_that_cannot_be_opened_is_a_problem_not_a_crash() -> None:
    rig = Rig()
    rig.storage.disk.databases.clear()

    result, _ = rig.synchronize()

    assert result == SyncNotStarted(SyncProblem.PROFILE_UNAVAILABLE, SyncMode.SYNC, False)
    with pytest.raises(ProfileNotFoundError):
        rig.storage.open(FOLDER)


class ExplodingAccount(FakeGarminAccount):
    """Un account che fallisce in modo imprevisto, con un testo che contiene un segreto."""

    def resume(self, tokens: SessionTokens) -> GarminSession:  # noqa: ARG002
        raise RuntimeError(f"segreto {PLACEHOLDER_TOKEN}")


class RenewingAccount(FakeGarminAccount):
    """Un account la cui ripresa rinnova i token, come può fare la libreria."""

    def resume(self, tokens: SessionTokens) -> GarminSession:
        session = super().resume(tokens)
        return GarminSession(session.source, RENEWED_TOKENS)


RENEWED_TOKENS = SessionTokens("TOKEN-RINNOVATO-0123456789abcdef")


def test_an_unexpected_failure_is_logged_by_type_only_and_reported_as_unexpected(
    caplog: pytest.LogCaptureFixture,
) -> None:
    rig = Rig(account_class=ExplodingAccount)

    with caplog.at_level(logging.DEBUG):
        result, _ = rig.synchronize()

    assert result == SyncNotStarted(SyncProblem.UNEXPECTED, SyncMode.SYNC, False)
    assert PLACEHOLDER_TOKEN not in caplog.text
    assert "RuntimeError" in caplog.text


def test_tokens_renewed_by_garmin_replace_the_saved_ones() -> None:
    rig = Rig(account_class=RenewingAccount)

    rig.synchronize()

    assert rig.store.saved[PROFILE_ID].reveal() == RENEWED_TOKENS.reveal()


def test_authentication_lost_in_the_middle_stops_the_round_with_its_reason() -> None:
    rig = Rig(activities=3)
    rig.source.fail_call(Call.DOWNLOAD, 2, AuthenticationRequiredError("scaduti"))

    result, _ = rig.synchronize()

    assert isinstance(result, SyncFinished)
    assert result.outcome.stop is StopReason.AUTHENTICATION_REQUIRED
    assert result.outcome.downloaded == 1


def test_rate_limit_responses_are_the_account_not_a_crash() -> None:
    rig = Rig()
    rig.account.rate_limit(1.0)

    result, _ = rig.synchronize()

    assert isinstance(result, SyncNotStarted)
    assert result.problem is SyncProblem.RATE_LIMITED
