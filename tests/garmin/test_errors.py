# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Dagli errori di `garminconnect` a quelli delle porte: 429, rete, 5xx, autenticazione, resto."""

from __future__ import annotations

import pytest
from garminconnect import (
    GarminConnectAuthenticationError,
    GarminConnectConnectionError,
    GarminConnectInvalidFileFormatError,
    GarminConnectTooManyRequestsError,
)

from ponos_coach.activities.ports import (
    ActivitySourceError,
    AuthenticationRequiredError,
    DefinitiveSourceError,
    InvalidCredentialsError,
    RateLimitedError,
    ServiceUnavailableError,
    TransientSourceError,
)
from ponos_coach.garmin.errors import (
    MAX_RETRY_AFTER_S,
    translate_login_error,
    translate_resume_error,
    translate_source_error,
)
from tests.garmin.scripted import (
    FakeResponse,
    auth_error,
    http_error,
    not_found_error,
    rate_limit_error,
)


class HttpFailureError(OSError):
    """Come `requests.HTTPError`: un errore di I/O che porta la sua risposta."""

    def __init__(self, status: int) -> None:
        super().__init__("http")
        self.response = FakeResponse(status)


def chained(outer: Exception, cause: BaseException) -> Exception:
    outer.__cause__ = cause
    return outer


def retry_after_of(error: ActivitySourceError) -> float | None:
    assert isinstance(error, RateLimitedError)
    return error.retry_after_s


class TestSourceErrors:
    def test_rate_limit_carries_the_suggested_wait(self) -> None:
        assert retry_after_of(translate_source_error(rate_limit_error("120"))) == 120.0

    @pytest.mark.parametrize(
        ("header", "expected"),
        [
            (None, None),
            ("0", None),
            ("-5", None),
            ("abc", None),
            ("", None),
            ("Wed, 21 Oct 2026 07:28:00 GMT", None),
            ("1.5", 1.5),
            ("99999", MAX_RETRY_AFTER_S),
            ("3600", 3600.0),
            ("1e3", None),
            ("²", None),  # D-F2-01: cifra che float() non legge
            ("٣", None),  # cifra araba: non ASCII
            ("+5", None),
            ("5 6", None),
            ("9" * 500, None),
        ],
    )
    def test_retry_after_is_read_only_when_it_is_a_sensible_number(
        self, header: str | None, expected: float | None
    ) -> None:
        assert retry_after_of(translate_source_error(rate_limit_error(header))) == expected

    @pytest.mark.parametrize("status", [429, 427])
    def test_a_rate_limit_status_without_the_dedicated_error_is_still_a_rate_limit(
        self, status: int
    ) -> None:
        error = translate_source_error(http_error(status, {"Retry-After": "7"}))

        assert retry_after_of(error) == 7.0

    @pytest.mark.parametrize("status", [500, 502, 503, 504, 599, 408, 425])
    def test_server_errors_and_timeouts_are_transient(self, status: int) -> None:
        assert type(translate_source_error(http_error(status))) is TransientSourceError

    @pytest.mark.parametrize("status", [400, 403, 404, 410, 422, 499])
    def test_other_client_errors_are_definitive(self, status: int) -> None:
        assert type(translate_source_error(http_error(status))) is DefinitiveSourceError

    def test_not_found_is_definitive(self) -> None:
        assert type(translate_source_error(not_found_error())) is DefinitiveSourceError

    def test_authentication_is_required_after_a_401(self) -> None:
        assert type(translate_source_error(auth_error())) is AuthenticationRequiredError
        assert type(translate_source_error(http_error(401))) is AuthenticationRequiredError

    @pytest.mark.parametrize("cause", [TimeoutError(), ConnectionResetError(), OSError("rete")])
    def test_network_failures_are_transient(self, cause: BaseException) -> None:
        error = chained(GarminConnectConnectionError("Connection error"), cause)

        assert type(translate_source_error(error)) is TransientSourceError

    def test_an_authentication_error_caused_by_the_network_is_transient(self) -> None:
        error = chained(GarminConnectAuthenticationError("profilo"), ConnectionError())

        assert type(translate_source_error(error)) is TransientSourceError

    def test_an_http_error_with_its_response_is_not_a_network_failure(self) -> None:
        error = chained(GarminConnectAuthenticationError("rifiutato"), HttpFailureError(401))

        assert type(translate_source_error(error)) is AuthenticationRequiredError

    def test_a_connection_error_without_status_or_network_cause_is_definitive(self) -> None:
        assert type(translate_source_error(GarminConnectConnectionError("?"))) is (
            DefinitiveSourceError
        )

    @pytest.mark.parametrize(
        "error", [ValueError("x"), KeyError("k"), GarminConnectInvalidFileFormatError("f")]
    )
    def test_everything_else_is_definitive(self, error: Exception) -> None:
        assert type(translate_source_error(error)) is DefinitiveSourceError

    def test_a_cyclic_cause_chain_terminates(self) -> None:
        first, second = GarminConnectConnectionError("a"), GarminConnectConnectionError("b")
        first.__cause__, second.__cause__ = second, first

        assert type(translate_source_error(first)) is DefinitiveSourceError

    def test_a_very_long_chain_is_examined_only_up_to_a_limit(self) -> None:
        error: Exception = GarminConnectTooManyRequestsError("in fondo")
        for _ in range(50):
            error = chained(GarminConnectConnectionError("anello"), error)

        assert type(translate_source_error(error)) is DefinitiveSourceError

    def test_messages_never_repeat_the_text_of_the_library(self) -> None:
        secret = "TOKEN-SEGNAPOSTO-0123456789abcdef /activity/12345"  # noqa: S105 - segnaposto
        for original in (
            GarminConnectConnectionError(secret),
            chained(GarminConnectAuthenticationError(secret), OSError(secret)),
            chained(GarminConnectTooManyRequestsError(secret), http_error(429)),
        ):
            for translate in (
                translate_source_error,
                translate_login_error,
                translate_resume_error,
            ):
                error = translate(original)
                assert secret not in str(error) + repr(error)


class TestLoginErrors:
    def test_rejected_credentials(self) -> None:
        assert type(translate_login_error(auth_error())) is InvalidCredentialsError

    def test_login_rate_limit(self) -> None:
        assert retry_after_of(translate_login_error(rate_limit_error("45"))) == 45.0

    @pytest.mark.parametrize("error", [http_error(503), http_error(403), TimeoutError()])
    def test_a_failure_that_is_not_the_credentials_is_service_unavailable(
        self, error: Exception
    ) -> None:
        assert type(translate_login_error(error)) is ServiceUnavailableError

    def test_service_unavailable_is_a_transient_error(self) -> None:
        assert isinstance(translate_login_error(http_error(500)), TransientSourceError)


class TestResumeErrors:
    def test_revoked_or_expired_tokens(self) -> None:
        assert type(translate_resume_error(auth_error())) is AuthenticationRequiredError

    def test_missing_tokens_error_from_the_library(self) -> None:
        error = GarminConnectAuthenticationError("Username and password are required")

        assert type(translate_resume_error(error)) is AuthenticationRequiredError

    def test_authentication_error_caused_by_a_dead_network_is_not_a_revoked_token(self) -> None:
        error = chained(GarminConnectAuthenticationError("profilo"), TimeoutError())

        assert type(translate_resume_error(error)) is ServiceUnavailableError

    def test_resume_rate_limit(self) -> None:
        assert retry_after_of(translate_resume_error(rate_limit_error())) is None

    @pytest.mark.parametrize("status", [500, 503, 404, 403])
    def test_other_failures_are_service_unavailable(self, status: int) -> None:
        assert type(translate_resume_error(http_error(status))) is ServiceUnavailableError
