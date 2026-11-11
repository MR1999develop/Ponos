# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Garmin session token: an opaque string that is never printed or serialized."""

from __future__ import annotations

import hmac
from typing import Final, NoReturn

MAX_TOKEN_CHARS: Final = 65536  # project default: Garmin tokens are a few KB


class SessionTokens:
    """Session token as an opaque string (it has no structure for the app).

    `repr`, `str` and f-strings always give `SessionTokens(<hidden>)`; the content comes out only
    from `reveal()`, called by the keyring and by whoever resumes the session. It is not copied with
    `pickle` or `copy`, it is not comparable as data (the comparison is constant-time) and it
    is not hashable.
    """

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        """Raises:
        ValueError: empty, longer than `MAX_TOKEN_CHARS` or not a string.
        """
        candidate: object = value  # anything can arrive at runtime
        if not isinstance(candidate, str) or not 0 < len(candidate) <= MAX_TOKEN_CHARS:
            raise ValueError("invalid token")
        self._value = candidate

    def reveal(self) -> str:
        """The content, not to be logged or shown."""
        return self._value

    def __repr__(self) -> str:
        return "SessionTokens(<hidden>)"

    __str__ = __repr__

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, SessionTokens):
            return NotImplemented
        return hmac.compare_digest(self._value.encode(), other._value.encode())

    __hash__ = None  # type: ignore[assignment]  # not hashable: a secret is not a key

    def __reduce__(self) -> NoReturn:
        raise TypeError("SessionTokens cannot be serialized")
