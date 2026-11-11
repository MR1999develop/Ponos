# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Activity list filters: types valid by construction and tolerant input parsing.

A single `ActivityFilter` serves both the table and the map. An empty filter lets everything
through; minimum and maximum are optional; if inverted they are swapped. Domain units are those
of the index (seconds, metres); whoever shows the filter converts (minutes, kilometres).
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, tzinfo
from enum import Enum

from ponos_coach.activities.domain.record import ActivityRecord
from ponos_coach.activities.domain.sport import Sport

_UNICODE_MINUS = chr(0x2212)
_MAX_INPUT_CHARS = 32  # the length is checked before any regular expression
_NUMBER = re.compile(r"(\d{1,9})(?:[.,](\d{1,6}))?")
_CLOCK = re.compile(r"(\d{1,6}):([0-5]?\d)(?::([0-5]?\d))?")
_SECONDS_PER_MINUTE = 60.0
_SECONDS_PER_HOUR = 3600.0
_METERS_PER_KM = 1000.0
MAX_FILTER_VALUE = 1_000_000_000.0  # cap of any numeric limit (finite and sensible)


class FilterInputReason(Enum):
    """Why a text is not a valid limit. The value is stable."""

    NOT_A_NUMBER = "not_a_number"
    NEGATIVE = "negative"
    TOO_LARGE = "too_large"


class FilterInputError(ValueError):
    """The text of a limit is not valid; `reason` says why (the text is never repeated)."""

    def __init__(self, reason: FilterInputReason) -> None:
        super().__init__(reason.value)
        self.reason = reason


def _number(text: str) -> float:
    """Non-negative number with comma or point as decimal separator (no thousands separator)."""
    body = text.strip()
    if len(body) > _MAX_INPUT_CHARS:
        raise FilterInputError(FilterInputReason.TOO_LARGE)
    if body.startswith(("-", _UNICODE_MINUS)):
        raise FilterInputError(FilterInputReason.NEGATIVE)
    match = _NUMBER.fullmatch(body)
    if match is None:
        raise FilterInputError(FilterInputReason.NOT_A_NUMBER)
    return float(f"{match.group(1)}.{match.group(2) or '0'}")


def parse_amount(text: str, scale: float = 1.0) -> float | None:
    """Text quantity multiplied by `scale`; empty text means no limit.

    With `scale = 1000`, "10,5" (kilometres) gives 10500.0 metres.

    Raises:
        FilterInputError: non-numeric, negative or too large text.
    """
    if not text.strip():
        return None
    value = _number(text) * scale
    if value > MAX_FILTER_VALUE:
        raise FilterInputError(FilterInputReason.TOO_LARGE)
    return value


def parse_distance_km(text: str) -> float | None:
    """Kilometres as text ("10", "10,5", "10.5") to metres; empty means no limit.

    Raises:
        FilterInputError: non-numeric, negative or too large text.
    """
    return parse_amount(text, _METERS_PER_KM)


def parse_duration(text: str) -> float | None:
    """Duration as text to seconds; empty means no limit.

    "90" and "90,5" are minutes; "1:30" is one hour and 30 minutes; "1:30:15" hours, minutes
    and seconds. Minutes and seconds after a colon are in 0-59.

    Raises:
        FilterInputError: invalid, negative or too large text.
    """
    body = text.strip()
    if not body:
        return None
    clock = _CLOCK.fullmatch(body) if len(body) <= _MAX_INPUT_CHARS else None
    if clock is None:
        return parse_amount(body, _SECONDS_PER_MINUTE)
    hours, minutes, seconds = clock.group(1), clock.group(2), clock.group(3) or "0"
    value = int(hours) * _SECONDS_PER_HOUR + int(minutes) * _SECONDS_PER_MINUTE + int(seconds)
    if value > MAX_FILTER_VALUE:
        raise FilterInputError(FilterInputReason.TOO_LARGE)
    return float(value)


@dataclass(frozen=True, slots=True)
class NumericRange:
    """Closed interval `[minimum, maximum]` with optional ends (`None` = no limit).

    Ends are finite, non-negative and at most `MAX_FILTER_VALUE`; `of` swaps inverted ends,
    while the direct constructor rejects them.
    """

    minimum: float | None = None
    maximum: float | None = None

    def __post_init__(self) -> None:
        for bound in (self.minimum, self.maximum):
            valid = bound is None or (math.isfinite(bound) and 0.0 <= bound <= MAX_FILTER_VALUE)
            if not valid:
                raise ValueError("limits must be finite and non-negative")
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValueError("the minimum exceeds the maximum")

    @classmethod
    def of(cls, first: float | None, second: float | None) -> NumericRange:
        """Interval from two ends in either order: if inverted they are swapped."""
        if first is not None and second is not None and first > second:
            return cls(second, first)
        return cls(first, second)

    @property
    def is_open(self) -> bool:
        """Whether there is no limit at all."""
        return self.minimum is None and self.maximum is None

    def accepts(self, value: float | None) -> bool:
        """Whether the value is within the limits. An unknown value passes only if unlimited."""
        if self.is_open:
            return True
        if value is None:
            return False
        low_ok = self.minimum is None or value >= self.minimum
        return low_ok and (self.maximum is None or value <= self.maximum)


@dataclass(frozen=True, slots=True)
class ActivityFilter:
    """Search criteria on activities; all optional and combined with "and".

    `date_from` and `date_to` are inclusive days (in the time zone given to `matches`); if
    inverted they are swapped. `duration_s` is the moving time; `distance_m` and `ascent_m`
    (positive ascent) come from the summary: an activity that does not declare them fails a
    filter that limits them. Empty `sports` means all sports.
    """

    date_from: date | None = None
    date_to: date | None = None
    duration_s: NumericRange = NumericRange()
    distance_m: NumericRange = NumericRange()
    ascent_m: NumericRange = NumericRange()
    sports: frozenset[Sport] = frozenset()

    def __post_init__(self) -> None:
        first, second = self.date_from, self.date_to
        if first is not None and second is not None and first > second:
            object.__setattr__(self, "date_from", second)
            object.__setattr__(self, "date_to", first)

    @property
    def is_empty(self) -> bool:
        """Whether the filter lets any activity through."""
        return (
            self.date_from is None
            and self.date_to is None
            and self.duration_s.is_open
            and self.distance_m.is_open
            and self.ascent_m.is_open
            and not self.sports
        )

    def matches(self, record: ActivityRecord, tz: tzinfo = UTC) -> bool:
        """Whether the activity passes every criterion."""
        day = record.start.astimezone(tz).date()
        return (
            (self.date_from is None or day >= self.date_from)
            and (self.date_to is None or day <= self.date_to)
            and (not self.sports or record.sport in self.sports)
            and self.duration_s.accepts(record.timer_s)
            and self.distance_m.accepts(record.summary.distance_m)
            and self.ascent_m.accepts(record.summary.ascent_m)
        )


def filter_activities(
    records: Iterable[ActivityRecord], criteria: ActivityFilter, tz: tzinfo = UTC
) -> list[ActivityRecord]:
    """The activities that pass the filter, in the same order. Empty filter = all of them."""
    return [r for r in records if criteria.matches(r, tz)]
