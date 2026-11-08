# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""From `garminconnect` errors to those of the ports, without losing the chain of causes.

The library wraps almost everything in a few exceptions (`GarminConnectConnectionError` for every
network or HTTP failure, `GarminConnectAuthenticationError` even when the real fault is the
network): here the `__cause__`/`__context__` chain is inspected for the HTTP code and for network
failures, and it is decided how the caller reacts.

- 429 (and 427 of the login) -> rate limit, with `Retry-After` if Garmin gives it;
- 401 or authentication rejected -> authentication required (at login: credentials rejected);
- network, timeout, non-JSON response, 5xx and 408 -> transient;
- 404, other 4xx and everything else -> final.

No message contains texts from the library or from Garmin (they might have paths with ids,
names or response fragments): the cause stays in the chain for whoever wants to inspect it, and
whoever
logs it must write the type, not the text.
"""

from __future__ import annotations

import math
import re
from enum import Enum
from typing import Final

from garminconnect import (
    GarminConnectAuthenticationError,
    GarminConnectNotFoundError,
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

MAX_RETRY_AFTER_S: Final = 3600.0  # beyond one hour the suggested wait is not taken seriously
_MAX_CHAIN: Final = 8  # maximum depth of the chain of causes that is examined
_RATE_LIMIT_STATUSES: Final = frozenset({427, 429})  # 427: Garmin login block
_TRANSIENT_STATUSES: Final = frozenset({408, 425})
_RETRY_AFTER: Final = re.compile(r"[0-9]{1,20}(?:\.[0-9]{1,6})?")  # ASCII digits only
_NETWORK_MODULES: Final = ("requests", "urllib3", "curl_cffi", "http", "ssl", "socket")


class _Outcome(Enum):
    """How the caller must react."""

    RATE_LIMITED = "rate_limited"
    AUTHENTICATION = "authentication"
    TRANSIENT = "transient"
    DEFINITIVE = "definitive"


def _chain(exc: BaseException) -> list[BaseException]:
    """`exc` and its causes, in order, without cycles and at most `_MAX_CHAIN` elements."""
    found: list[BaseException] = []
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen and len(found) < _MAX_CHAIN:
        seen.add(id(current))
        found.append(current)
        current = current.__cause__ or current.__context__
    return found


def _response_of(exc: BaseException) -> object:
    return getattr(exc, "response", None)


def _status_of(exc: BaseException) -> int | None:
    """HTTP code declared by the exception or its response, if it is an integer."""
    for holder in (exc, _response_of(exc)):
        status = getattr(holder, "status_code", None)
        if type(status) is int:
            return status
    return None


def _first_status(chain: list[BaseException]) -> int | None:
    return next((s for s in map(_status_of, chain) if s is not None), None)


def _is_network_failure(exc: BaseException) -> bool:
    """Transport failure: I/O error (also from `requests` and `curl_cffi`, which are)
    without an HTTP response. An `HTTPError` with its response is not a network failure."""
    module = type(exc).__module__.partition(".")[0]
    is_io = isinstance(exc, OSError) or module in _NETWORK_MODULES
    return is_io and _status_of(exc) is None


def _retry_after_s(chain: list[BaseException]) -> float | None:
    """`Retry-After` in seconds (ASCII digits, integer or decimal), if Garmin gives it in a
    sensible way; otherwise None. Never raises: the header is untrusted input."""
    for exc in chain:
        headers = getattr(_response_of(exc), "headers", None)
        getter = getattr(headers, "get", None)
        raw = getter("Retry-After") if callable(getter) else None
        if not isinstance(raw, str) or _RETRY_AFTER.fullmatch(raw.strip()) is None:
            continue  # non-ASCII, signed or other: no suggested wait
        value = float(raw)
        if math.isfinite(value) and value > 0:
            return min(value, MAX_RETRY_AFTER_S)
    return None


def _classify(exc: BaseException) -> tuple[_Outcome, float | None]:
    """Outcome and, for the rate limit, the suggested wait."""
    chain = _chain(exc)
    status = _first_status(chain)
    if status in _RATE_LIMIT_STATUSES or any(
        isinstance(item, GarminConnectTooManyRequestsError) for item in chain
    ):
        return _Outcome.RATE_LIMITED, _retry_after_s(chain)
    if isinstance(exc, GarminConnectNotFoundError) or status == 404:
        return _Outcome.DEFINITIVE, None
    if status == 401:
        return _Outcome.AUTHENTICATION, None
    if status is not None:
        transient = status >= 500 or status in _TRANSIENT_STATUSES
        return (_Outcome.TRANSIENT if transient else _Outcome.DEFINITIVE), None
    if any(_is_network_failure(item) for item in chain):
        return _Outcome.TRANSIENT, None  # even an authentication "failed" because of the network
    is_auth = isinstance(exc, GarminConnectAuthenticationError)
    return (_Outcome.AUTHENTICATION if is_auth else _Outcome.DEFINITIVE), None


def translate_source_error(exc: Exception) -> ActivitySourceError:
    """The error of a list or download call, in the type of the `ActivitySource` port.

    Whoever raises does so with `raise translate_source_error(exc) from exc`.
    """
    outcome, retry_after = _classify(exc)
    if outcome is _Outcome.RATE_LIMITED:
        return RateLimitedError(retry_after)
    if outcome is _Outcome.AUTHENTICATION:
        return AuthenticationRequiredError("authentication required")
    if outcome is _Outcome.TRANSIENT:
        return TransientSourceError("transient Garmin error")
    return DefinitiveSourceError("request rejected or response not interpretable")


def is_not_found(exc: BaseException) -> bool:
    """True if the failure is Garmin saying the thing is not there (404)."""
    chain = _chain(exc)
    return any(isinstance(item, GarminConnectNotFoundError) for item in chain) or (
        _first_status(chain) == 404
    )


def translate_login_error(exc: Exception) -> ActivitySourceError:
    """The error of login with credentials, in the type of the `GarminAccount.login` port.

    Every failure that is not rejected credentials or a rate limit counts as "Garmin does not
    respond" (even a 403 block from the site): retrying later is the only useful reaction.
    """
    outcome, retry_after = _classify(exc)
    if outcome is _Outcome.RATE_LIMITED:
        return RateLimitedError(retry_after)
    if outcome is _Outcome.AUTHENTICATION:
        return InvalidCredentialsError("credentials rejected")
    return ServiceUnavailableError("Garmin does not respond")


def translate_resume_error(exc: Exception) -> ActivitySourceError:
    """The error of resumption from tokens, in the type of the `GarminAccount.resume` port.

    A rejected authentication counts as "tokens expired or revoked"; the library gives the same
    error
    when the network is missing during token verification, and here it is recognized from the chain.
    """
    outcome, retry_after = _classify(exc)
    if outcome is _Outcome.RATE_LIMITED:
        return RateLimitedError(retry_after)
    if outcome is _Outcome.AUTHENTICATION:
        return AuthenticationRequiredError("tokens expired or revoked")
    return ServiceUnavailableError("Garmin does not respond")
