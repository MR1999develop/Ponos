# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""`GarminAccount` on Garmin Connect: login with MFA and resumption from saved tokens.

The password and the MFA code go only to the client and are neither kept nor logged. The
tokens (`SessionTokens`) are revealed (`reveal()`) only at the point where they are passed to the
client to
resume the session. Login does not retry: every error is translated into the port's type
(`errors`) and the retry policy belongs to the caller.

Limits: the MFA code request (`prompt_mfa_code`) is an interface callback and has
no timeout here, since it would wait for a person; the number of requests per login is limited.
The tokens the session returns (`GarminSession.tokens`) are those after login or
resumption, renewed if the library renewed them: the caller saves them (the
synchronization worker rewrites them in the keyring if they changed). A renewal that happens later,
during the session, is not saved: at the next resumption the library renews them on its own.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Final

from ponos_coach.activities.domain import SessionTokens
from ponos_coach.activities.ports import (
    AuthenticationRequiredError,
    DefinitiveSourceError,
    GarminSession,
    InvalidCredentialsError,
    MfaCancelledError,
)
from ponos_coach.garmin.client import GarminClient, GarminClientFactory
from ponos_coach.garmin.errors import translate_login_error, translate_resume_error
from ponos_coach.garmin.source import GarminActivitySource

DEFAULT_MAX_MFA_PROMPTS: Final = 3
_MAX_EMAIL_CHARS: Final = 320
_MAX_PASSWORD_CHARS: Final = 1024
_MAX_MFA_CODE_CHARS: Final = 32


class _MfaAbortError(Exception):
    """Interrupts the login at the point where the library asks for the MFA code."""


class _MfaPrompter:
    """The MFA code for the library: at most `max_prompts` requests, cancellable.

    Remembers why it interrupted the login (`cancelled`, `exhausted`), so the error of the
    library that follows is not mistaken for wrong credentials or a missing network.
    """

    def __init__(self, ask: Callable[[], str | None], max_prompts: int) -> None:
        self._ask = ask
        self._remaining = max_prompts
        self.cancelled = False
        self.exhausted = False

    def __call__(self) -> str:
        if self._remaining <= 0:
            self.exhausted = True
            raise _MfaAbortError
        self._remaining -= 1
        code = self._ask()
        if code is None:
            self.cancelled = True
            raise _MfaAbortError
        code = code.strip()
        if not 0 < len(code) <= _MAX_MFA_CODE_CHARS:
            self.exhausted = True  # an empty or huge code is not sent to Garmin
            raise _MfaAbortError
        return code


def _is_json_object(text: str) -> bool:
    """True if the text is a JSON object (tokens are an object, never a path)."""
    try:
        return isinstance(json.loads(text), dict)
    except (ValueError, RecursionError):
        return False


class GarminConnectAccount:
    """`GarminAccount` on a `GarminClientFactory` (in production `ConnectClientFactory`).

    Besides the port's errors it may raise `DefinitiveSourceError` if the client gives no
    usable tokens after a successful login.
    """

    def __init__(
        self, factory: GarminClientFactory, *, max_mfa_prompts: int = DEFAULT_MAX_MFA_PROMPTS
    ) -> None:
        if max_mfa_prompts < 1:
            raise ValueError("max_mfa_prompts must be at least 1")
        self._factory = factory
        self._max_mfa_prompts = max_mfa_prompts

    def login(
        self, email: str, password: str, prompt_mfa_code: Callable[[], str | None]
    ) -> GarminSession:
        """See `GarminAccount.login`. Empty or huge email or password are credentials
        rejected without contacting Garmin."""
        if not 0 < len(email) <= _MAX_EMAIL_CHARS or not 0 < len(password) <= _MAX_PASSWORD_CHARS:
            raise InvalidCredentialsError("invalid credentials")
        prompter = _MfaPrompter(prompt_mfa_code, self._max_mfa_prompts)
        try:
            client = self._factory.login(email, password, prompter)
        except Exception as exc:
            if prompter.cancelled:
                raise MfaCancelledError("MFA cancelled") from exc
            if prompter.exhausted:
                raise InvalidCredentialsError("MFA code rejected") from exc
            raise translate_login_error(exc) from exc
        return self._session(client)

    def resume(self, tokens: SessionTokens) -> GarminSession:
        """See `GarminAccount.resume`. Tokens that are not a JSON object do not reach the
        library (it might read them as a file path)."""
        raw = tokens.reveal()
        if not _is_json_object(raw):
            raise AuthenticationRequiredError("tokens not recognized")
        try:
            client = self._factory.resume(raw)
        except Exception as exc:
            raise translate_resume_error(exc) from exc
        return self._session(client)

    @staticmethod
    def _session(client: GarminClient) -> GarminSession:
        try:
            tokens = SessionTokens(client.dump_tokens())
        except Exception as exc:
            raise DefinitiveSourceError("session tokens not usable") from exc
        return GarminSession(GarminActivitySource(client), tokens)
