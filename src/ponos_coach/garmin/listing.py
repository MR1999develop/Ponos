# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""From the Garmin JSON list to domain types: validation at the boundary (T-SEC-03).

The response is untrusted. Here it becomes a sequence of valid `RemoteActivity` plus the count
of the entries received; every malformed entry (not an object, non-numeric id or with paths, time
without time zone or out of window, missing or non-finite duration, missing sport type, repeated
id) is
discarded and counted, without repairing it. No free text (name, description) is read. Only
the known keys are accessed with type checks: no recursion on the received structure.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Final

from ponos_coach.activities.domain import (
    MAX_PAGE_SIZE,
    GarminActivityId,
    RemoteActivity,
    truncate_to_millisecond,
)

MAX_TOTAL_COUNT: Final = 10**9  # declared total beyond which the response is suspicious
_MAX_TIME_CHARS: Final = 40
_MAX_SPORT_KEY_CHARS: Final = 64
_GMT_TIME: Final = re.compile(
    r"([0-9]{4})-([0-9]{2})-([0-9]{2})[ T]([0-9]{2}):([0-9]{2}):([0-9]{2})(?:\.([0-9]{1,6}))?Z?"
)


class UnexpectedListingError(Exception):
    """The response is not a list of activities (wrong shape or too many entries)."""


def parse_page(raw: object) -> tuple[tuple[RemoteActivity, ...], int]:
    """The valid activities of a list page and how many entries Garmin returned.

    `raw` is the list of entries, or an object with the list in `activityList`.

    Raises:
        UnexpectedListingError: there is no list, or it has more than `MAX_PAGE_SIZE` entries.
    """
    entries = raw.get("activityList") if isinstance(raw, Mapping) else raw
    if not isinstance(entries, list) or len(entries) > MAX_PAGE_SIZE:
        raise UnexpectedListingError
    items: list[RemoteActivity] = []
    seen: set[GarminActivityId] = set()
    for entry in entries:
        activity = parse_entry(entry)
        if activity is not None and activity.garmin_id not in seen:
            seen.add(activity.garmin_id)
            items.append(activity)
    return tuple(items), len(entries)


def parse_total(raw: object) -> int | None:
    """The declared total (non-negative and plausible integer) from a value, or None."""
    if type(raw) is int and 0 <= raw <= MAX_TOTAL_COUNT:
        return raw
    return None


def parse_entry(entry: object) -> RemoteActivity | None:
    """The activity of a list entry, or None if the entry is malformed."""
    if not isinstance(entry, Mapping):
        return None
    try:
        garmin_id = GarminActivityId.parse(entry.get("activityId"))
        start = _parse_start(entry.get("startTimeGMT"))
        duration_s = _duration_s(entry)
        sport_key = _sport_key(entry.get("activityType"))
        if start is None or duration_s is None or sport_key is None:
            return None
        return RemoteActivity(garmin_id, start, duration_s, sport_key, _has_heart_rate(entry))
    except (ValueError, OverflowError):
        return None


def _number(value: object) -> float | None:
    """A finite number (not a boolean), otherwise None."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _duration_s(entry: Mapping[str, object]) -> float | None:
    """Elapsed duration in seconds: `elapsedDuration` if present, otherwise `duration`.

    The app's duration is the elapsed time (as in FIT), which includes pauses.
    """
    raw = entry.get("elapsedDuration")
    value = _number(raw if raw is not None else entry.get("duration"))
    return value if value is not None and value > 0 else None


def _sport_key(activity_type: object) -> str | None:
    if not isinstance(activity_type, Mapping):
        return None
    key = activity_type.get("typeKey")
    if not isinstance(key, str) or not 0 < len(key) <= _MAX_SPORT_KEY_CHARS:
        return None
    return key


def _has_heart_rate(entry: Mapping[str, object]) -> bool | None:
    """True if the average HR is a positive number; false if it is null (`null` or 0: Garmin did not
    record it); None if the list lacks the key or has a value that is not a number."""
    if "averageHR" not in entry:
        return None
    raw = entry["averageHR"]
    if raw is None:
        return False
    value = _number(raw)
    return None if value is None else value > 0


def _parse_start(raw: object) -> datetime | None:
    """Start in UTC to the millisecond from `startTimeGMT` (`2026-03-02 07:00:00`), or None."""
    if not isinstance(raw, str) or len(raw) > _MAX_TIME_CHARS:
        return None
    match = _GMT_TIME.fullmatch(raw)
    if match is None:
        return None
    year, month, day, hour, minute, second = (int(group) for group in match.groups()[:6])
    fraction = match.group(7) or ""
    microsecond = int(fraction.ljust(6, "0")) if fraction else 0
    try:
        moment = datetime(year, month, day, hour, minute, second, microsecond, tzinfo=UTC)
    except ValueError:
        return None
    return truncate_to_millisecond(moment)
