# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Verification of IANA time zone names: the only point that reads the time zone data.

It lives outside the pure domain because the lookup (`ZoneInfo`) reads the `tzdata` files from disk.
It is used by the use case that creates a profile and by the database adapter.
"""

from __future__ import annotations

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

_TIMEZONE_MAX_CHARS = 64


def require_known_timezone(name: str) -> None:
    """Checks that `name` is a known IANA time zone (lookup in the `tzdata` data, no network).

    Raises:
        ValueError: empty name, longer than 64 characters, invalid or unknown.
    """
    if not 0 < len(name) <= _TIMEZONE_MAX_CHARS:
        raise ValueError("time zone name empty or too long")
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, OSError) as exc:
        raise ValueError("unknown time zone") from exc
