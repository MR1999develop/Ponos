# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Fake client and factory to try the Garmin adapters without network or library.

`ScriptedClient` implements `GarminClient` with JSON responses given by the test; the errors are
the library's real ones (plain exceptions: building them is not a mock). No real data.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field

from garminconnect import (
    GarminConnectAuthenticationError,
    GarminConnectConnectionError,
    GarminConnectNotFoundError,
    GarminConnectTooManyRequestsError,
)

from ponos_coach.activities.domain import RemoteActivity
from ponos_coach.garmin.client import GarminClient

PLACEHOLDER_TOKENS = '{"di_token": "TOKEN-SEGNAPOSTO-0123456789abcdef", "di_refresh_token": "R"}'
SECRET_MARKERS = (
    "TOKEN-SEGNAPOSTO-0123456789abcdef",
    "password-di-prova",
    "atleta@example.invalid",
)


@dataclass(frozen=True, slots=True)
class FakeResponse:
    """Just enough of an HTTP response: status code and headers."""

    status_code: int
    headers: Mapping[str, str] = field(default_factory=dict)


def http_error(
    status: int, headers: Mapping[str, str] | None = None
) -> GarminConnectConnectionError:
    """The error the library raises for an HTTP response `status`."""
    error: GarminConnectConnectionError
    if status == 404:
        error = GarminConnectNotFoundError(f"API Error {status}")
    else:
        error = GarminConnectConnectionError(f"API Error {status}")
    error.response = FakeResponse(status, headers or {})
    return error


def rate_limit_error(retry_after: str | None = None) -> GarminConnectTooManyRequestsError:
    """The 429 as the library gives it: its own error, with the HTTP one as cause."""
    headers = {} if retry_after is None else {"Retry-After": retry_after}
    error = GarminConnectTooManyRequestsError("Rate limit exceeded")
    error.__cause__ = http_error(429, headers)
    return error


def auth_error() -> GarminConnectAuthenticationError:
    """Rejected authentication (401) as the library gives it."""
    error = GarminConnectAuthenticationError("Authentication failed")
    error.__cause__ = http_error(401)
    return error


def not_found_error() -> GarminConnectNotFoundError:
    """A missing resource (404)."""
    return GarminConnectNotFoundError("API Error 404")


def entry_for(activity: RemoteActivity) -> dict[str, object]:
    """The Garmin listing entry that `parse_entry` reads as `activity`."""
    milliseconds = activity.start.microsecond // 1000
    entry: dict[str, object] = {
        "activityId": activity.garmin_id.value,
        "startTimeGMT": f"{activity.start:%Y-%m-%d %H:%M:%S}.{milliseconds:03d}",
        "duration": activity.duration_s,
        "activityType": {"typeKey": activity.sport_key},
    }
    if activity.has_heart_rate is not None:
        entry["averageHR"] = 140.0 if activity.has_heart_rate else None
    return entry


def newest_first(activities: Sequence[RemoteActivity]) -> list[RemoteActivity]:
    """The same order as the Garmin listing and the fake: decreasing start, then id."""
    return sorted(activities, key=lambda a: (a.start, a.garmin_id.value), reverse=True)


class ScriptedClient:
    """`GarminClient` with a list of entries, downloads and failures decided by the test.

    `list_activities` serves the window `[offset, offset + limit)` of `entries` like Garmin
    (past the end, empty list) or, if `listing` is given, always that value. Queued
    failures are consumed one per call.
    """

    def __init__(
        self,
        entries: Sequence[object] = (),
        *,
        listing: object | None = None,
        total: object = None,
        downloads: Mapping[int, bytes] | None = None,
        tokens: str = PLACEHOLDER_TOKENS,
    ) -> None:
        self._entries = list(entries)
        self._listing = listing
        self._total = len(self._entries) if total is None else total
        self._downloads = dict(downloads or {})
        self._tokens = tokens
        self.list_failures: deque[Exception] = deque()
        self.download_failures: deque[Exception] = deque()
        self.count_failure: Exception | None = None
        self.list_calls: list[tuple[int, int]] = []
        self.count_calls = 0
        self.downloaded: list[int] = []

    def replace_entry(self, index: int, entry: object) -> None:
        """Change the entry at position `index` (to make it malformed)."""
        self._entries[index] = entry

    def set_download(self, activity_id: int, content: bytes) -> None:
        """What gets downloaded for that id."""
        self._downloads[activity_id] = content

    # --- GarminClient ------------------------------------------------------------------------

    def list_activities(self, offset: int, limit: int) -> object:
        self.list_calls.append((offset, limit))
        if self.list_failures:
            raise self.list_failures.popleft()
        if self._listing is not None:
            return self._listing
        return self._entries[offset : offset + limit]

    def count_activities(self) -> object:
        self.count_calls += 1
        if self.count_failure is not None:
            raise self.count_failure
        return self._total

    def download_original(self, activity_id: int) -> bytes:
        self.downloaded.append(activity_id)
        if self.download_failures:
            raise self.download_failures.popleft()
        if activity_id not in self._downloads:
            raise not_found_error()
        return self._downloads[activity_id]

    def dump_tokens(self) -> str:
        return self._tokens


class ScriptedFactory:
    """Fake `GarminClientFactory`: gives `client` or raises `login_error` / `resume_error`.

    `mfa_codes_expected` is the list of MFA codes the login accepts in sequence: the factory
    asks for them one at a time and, if the code differs, raises the rejected authentication like
    the library. It records the calls; it does not keep the password.
    """

    def __init__(self, client: GarminClient | None = None) -> None:
        self.client: GarminClient = client or ScriptedClient()
        self.login_error: Exception | None = None
        self.resume_error: Exception | None = None
        self.wrap_prompt_errors = False
        self.expected_mfa_code: str | None = None
        self.login_calls = 0
        self.resume_calls: list[str] = []
        self.prompts_received: list[str] = []
        self.mfa_attempts_until_giving_up = 10

    def login(self, email: str, password: str, prompt_mfa: Callable[[], str]) -> GarminClient:
        del email, password
        self.login_calls += 1
        if self.login_error is not None:
            raise self.login_error
        if self.expected_mfa_code is not None:
            self._ask_for_the_code(prompt_mfa)
        return self.client

    def resume(self, tokens: str) -> GarminClient:
        self.resume_calls.append(tokens)
        if self.resume_error is not None:
            raise self.resume_error
        return self.client

    def _ask_for_the_code(self, prompt_mfa: Callable[[], str]) -> None:
        for _ in range(self.mfa_attempts_until_giving_up):
            try:
                code = prompt_mfa()
            except Exception as exc:
                if self.wrap_prompt_errors:
                    raise GarminConnectConnectionError("login fallito") from exc
                raise
            self.prompts_received.append(code)
            if code == self.expected_mfa_code:
                return
        raise auth_error()
