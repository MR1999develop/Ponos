# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""`GarminConnectAccount`: login con MFA, ripresa dai token, segreti (T-SEC-07)."""

from __future__ import annotations

import logging
from collections.abc import Callable
from functools import partial

import pytest
from garminconnect import GarminConnectConnectionError

from ponos_coach.activities.domain import SessionTokens
from ponos_coach.activities.ports import (
    AuthenticationRequiredError,
    DefinitiveSourceError,
    InvalidCredentialsError,
    MfaCancelledError,
    RateLimitedError,
    ServiceUnavailableError,
)
from ponos_coach.garmin import GarminActivitySource, GarminConnectAccount
from tests.fakes.garmin import PLACEHOLDER_TOKEN, TEST_EMAIL, TEST_MFA_CODE, TEST_PASSWORD
from tests.garmin.scripted import (
    PLACEHOLDER_TOKENS,
    SECRET_MARKERS,
    ScriptedClient,
    ScriptedFactory,
    auth_error,
    http_error,
    rate_limit_error,
)


def account_with(factory: ScriptedFactory, **options: int) -> GarminConnectAccount:
    return GarminConnectAccount(factory, **options)


def no_code() -> str | None:
    raise AssertionError("non doveva chiedere il codice MFA")


class TestLogin:
    def test_login_without_mfa_gives_a_session_with_the_tokens_of_the_client(self) -> None:
        factory = ScriptedFactory()

        session = account_with(factory).login(TEST_EMAIL, TEST_PASSWORD, no_code)

        assert isinstance(session.source, GarminActivitySource)
        assert session.tokens == SessionTokens(PLACEHOLDER_TOKENS)

    def test_the_mfa_code_is_asked_and_passed_on_trimmed(self) -> None:
        factory = ScriptedFactory()
        factory.expected_mfa_code = TEST_MFA_CODE

        account_with(factory).login(TEST_EMAIL, TEST_PASSWORD, lambda: f"  {TEST_MFA_CODE}\n")

        assert factory.prompts_received == [TEST_MFA_CODE]

    @pytest.mark.parametrize("wrapped", [False, True])
    def test_cancelling_the_mfa_prompt_is_a_cancellation_even_if_the_library_wraps_it(
        self, wrapped: bool
    ) -> None:
        factory = ScriptedFactory()
        factory.expected_mfa_code = TEST_MFA_CODE
        factory.wrap_prompt_errors = wrapped

        with pytest.raises(MfaCancelledError):
            account_with(factory).login(TEST_EMAIL, TEST_PASSWORD, lambda: None)

    def test_a_wrong_code_asked_again_stops_after_the_limit(self) -> None:
        factory = ScriptedFactory()
        factory.expected_mfa_code = TEST_MFA_CODE
        asked: list[int] = []

        def wrong() -> str:
            asked.append(1)
            return "000000"

        with pytest.raises(InvalidCredentialsError):
            account_with(factory, max_mfa_prompts=3).login(TEST_EMAIL, TEST_PASSWORD, wrong)

        assert len(asked) == 3
        assert factory.prompts_received == ["000000"] * 3

    @pytest.mark.parametrize("code", ["", "   ", "9" * 33])
    def test_an_empty_or_huge_code_is_not_sent_to_garmin(self, code: str) -> None:
        factory = ScriptedFactory()
        factory.expected_mfa_code = TEST_MFA_CODE

        with pytest.raises(InvalidCredentialsError):
            account_with(factory).login(TEST_EMAIL, TEST_PASSWORD, lambda: code)

        assert factory.prompts_received == []

    @pytest.mark.parametrize(
        ("email", "password"),
        [
            ("", TEST_PASSWORD),
            (TEST_EMAIL, ""),
            ("a" * 321, TEST_PASSWORD),
            (TEST_EMAIL, "p" * 1025),
        ],
    )
    def test_empty_or_huge_credentials_are_refused_without_contacting_garmin(
        self, email: str, password: str
    ) -> None:
        factory = ScriptedFactory()

        with pytest.raises(InvalidCredentialsError):
            account_with(factory).login(email, password, no_code)

        assert factory.login_calls == 0

    def test_rejected_credentials(self) -> None:
        factory = ScriptedFactory()
        factory.login_error = auth_error()

        with pytest.raises(InvalidCredentialsError):
            account_with(factory).login(TEST_EMAIL, TEST_PASSWORD, no_code)

    def test_rate_limit_keeps_the_wait_suggested_by_garmin(self) -> None:
        factory = ScriptedFactory()
        factory.login_error = rate_limit_error("90")

        with pytest.raises(RateLimitedError) as caught:
            account_with(factory).login(TEST_EMAIL, TEST_PASSWORD, no_code)

        assert caught.value.retry_after_s == 90.0

    def test_garmin_down(self) -> None:
        factory = ScriptedFactory()
        factory.login_error = http_error(503)

        with pytest.raises(ServiceUnavailableError):
            account_with(factory).login(TEST_EMAIL, TEST_PASSWORD, no_code)

    @pytest.mark.parametrize("tokens", ["", "x" * 70_000])
    def test_unusable_tokens_after_a_successful_login_are_definitive(self, tokens: str) -> None:
        factory = ScriptedFactory(ScriptedClient(tokens=tokens))

        with pytest.raises(DefinitiveSourceError):
            account_with(factory).login(TEST_EMAIL, TEST_PASSWORD, no_code)

    def test_the_login_does_not_retry(self) -> None:
        factory = ScriptedFactory()
        factory.login_error = http_error(500)

        with pytest.raises(ServiceUnavailableError):
            account_with(factory).login(TEST_EMAIL, TEST_PASSWORD, no_code)

        assert factory.login_calls == 1

    def test_at_least_one_mfa_prompt_is_required(self) -> None:
        with pytest.raises(ValueError, match="max_mfa_prompts"):
            account_with(ScriptedFactory(), max_mfa_prompts=0)


class TestResume:
    def test_tokens_reach_the_client_exactly_as_stored(self) -> None:
        factory = ScriptedFactory()

        session = account_with(factory).resume(SessionTokens(PLACEHOLDER_TOKENS))

        assert factory.resume_calls == [PLACEHOLDER_TOKENS]
        assert session.tokens == SessionTokens(PLACEHOLDER_TOKENS)

    def test_the_session_has_the_renewed_tokens_of_the_client(self) -> None:
        renewed = '{"di_token": "NUOVO-SEGNAPOSTO"}'
        factory = ScriptedFactory(ScriptedClient(tokens=renewed))

        session = account_with(factory).resume(SessionTokens(PLACEHOLDER_TOKENS))

        assert session.tokens == SessionTokens(renewed)

    @pytest.mark.parametrize("stored", [PLACEHOLDER_TOKEN, "[1]", "/etc/passwd", "{rotto", '"x"'])
    def test_tokens_that_are_not_a_json_object_never_reach_the_library(self, stored: str) -> None:
        factory = ScriptedFactory()

        with pytest.raises(AuthenticationRequiredError):
            account_with(factory).resume(SessionTokens(stored))

        assert factory.resume_calls == []

    def test_deeply_nested_json_is_refused_without_crashing(self) -> None:
        stored = "[" * 30_000 + "]" * 30_000

        with pytest.raises(AuthenticationRequiredError):
            account_with(ScriptedFactory()).resume(SessionTokens(stored))

    def test_revoked_tokens(self) -> None:
        factory = ScriptedFactory()
        factory.resume_error = auth_error()

        with pytest.raises(AuthenticationRequiredError):
            account_with(factory).resume(SessionTokens(PLACEHOLDER_TOKENS))

    def test_garmin_unreachable_is_not_a_revoked_token(self) -> None:
        factory = ScriptedFactory()
        factory.resume_error = GarminConnectConnectionError("rete")
        factory.resume_error.__cause__ = TimeoutError()

        with pytest.raises(ServiceUnavailableError):
            account_with(factory).resume(SessionTokens(PLACEHOLDER_TOKENS))

    def test_resume_rate_limit(self) -> None:
        factory = ScriptedFactory()
        factory.resume_error = rate_limit_error("5")

        with pytest.raises(RateLimitedError):
            account_with(factory).resume(SessionTokens(PLACEHOLDER_TOKENS))


class TestSecrets:
    """T-SEC-07: password, email e token non compaiono in messaggi, rappresentazioni e log."""

    def test_errors_and_sessions_do_not_leak_the_secrets_even_if_the_library_does(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        leak = " ".join(SECRET_MARKERS)
        failures: list[Exception] = [
            GarminConnectConnectionError(leak),
            auth_error(),
            rate_limit_error(),
            http_error(503),
        ]
        collected: list[str] = []
        with caplog.at_level(logging.DEBUG):
            for failure in failures:
                factory = ScriptedFactory()
                factory.login_error = failure
                factory.resume_error = failure
                account = account_with(factory)
                calls: tuple[Callable[[], object], ...] = (
                    partial(account.login, TEST_EMAIL, TEST_PASSWORD, no_code),
                    partial(account.resume, SessionTokens(PLACEHOLDER_TOKENS)),
                )
                for call in calls:
                    with pytest.raises(Exception, match=r".") as caught:
                        call()
                    collected += [str(caught.value), repr(caught.value)]
            session = account_with(ScriptedFactory()).login(TEST_EMAIL, TEST_PASSWORD, no_code)
            collected += [repr(session), repr(session.tokens), str(session.tokens)]

        text = "\n".join(collected) + caplog.text
        assert not [marker for marker in SECRET_MARKERS if marker in text]

    def test_the_tokens_are_revealed_only_to_resume_the_session(self) -> None:
        factory = ScriptedFactory()

        session = account_with(factory).login(TEST_EMAIL, TEST_PASSWORD, no_code)

        assert "TOKEN-SEGNAPOSTO" not in repr(session)
        assert factory.resume_calls == []
