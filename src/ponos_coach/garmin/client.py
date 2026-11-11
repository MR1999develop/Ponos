# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""The "client": what the package asks of `garminconnect`, as an internal protocol.

The mapping of responses and errors is tested with a fake client; the only class that
really uses the library is `connect.ConnectClient`. Responses are untrusted JSON (`object`):
the caller interprets them, at the boundary. The errors are those of the library, not yet
translated into the ports' types.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol


class GarminClient(Protocol):
    """An authenticated session with Garmin Connect. Every call has a timeout."""

    def list_activities(self, offset: int, limit: int) -> object:
        """A page of the activity list, from the most recent, as uninterpreted JSON
        (usually a list of objects)."""
        ...

    def count_activities(self) -> object:
        """The total of activities declared by Garmin, as an uninterpreted value."""
        ...

    def download_original(self, activity_id: int) -> bytes:
        """The bytes of the original (a ZIP) of an activity, by an already validated numeric id."""
        ...

    def dump_tokens(self) -> str:
        """The session tokens, serialized, to save in the keyring. Secrets."""
        ...


class GarminClientFactory(Protocol):
    """Creates sessions: with email and password (and MFA) or from saved tokens."""

    def login(self, email: str, password: str, prompt_mfa: Callable[[], str]) -> GarminClient:
        """Logs in with the credentials; if MFA is needed it calls `prompt_mfa` for the code.

        Login does not retry: the errors are those of the library.
        """
        ...

    def resume(self, tokens: str) -> GarminClient:
        """Resumes the session from serialized tokens (JSON), without a password."""
        ...
