# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""The threshold pace the user sets by hand, and which pace is in force on a day.

The pace comes from two sources: a dated series the user edits in the profile settings and the
threshold tests that measured one. `threshold_pace_in_force` chooses between them: the value of the
latest day wins, and on the same day the manual one (the user's deliberate choice). It is a pure
function of its inputs, so the plan stays deterministic.

The pace is a health-related training datum: never in a log or in an error message.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from enum import Enum

from ponos_coach.assessment.domain.defaults import MAX_PACE_S_PER_KM, MIN_PACE_S_PER_KM
from ponos_coach.assessment.domain.record import ThresholdTest

_SECONDS_PER_MINUTE = 60
_TEXT_MAX_CHARS = 16  # the length is checked before matching the text
_ASCII_BLANKS = " \t\r\n"
# `m:ss` with `:`, `.`, `,` or `'` between minutes and seconds (two digits), or minutes alone.
# ASCII digits only: full-width and other Unicode digits are rejected, never reinterpreted.
_PACE_TEXT = re.compile(r"(?P<minutes>[0-9]{1,2})(?:[:.,'](?P<seconds>[0-9]{2}))?", re.ASCII)


@dataclass(frozen=True, slots=True)
class ThresholdPace:
    """Pace at the lactate threshold in whole seconds per kilometre, from 1:30 to 60:00."""

    s_per_km: int

    def __post_init__(self) -> None:
        if not MIN_PACE_S_PER_KM <= self.s_per_km <= MAX_PACE_S_PER_KM:
            raise ValueError("the pace must be between 1:30 and 60:00 per km")

    @classmethod
    def parse(cls, text: str) -> ThresholdPace:
        """The pace from user text: `5:30`, `5.30`, `5,30`, `5'30`, or whole minutes (`6`).

        Seconds are two digits from 00 to 59. ASCII digits only, (ASCII) blanks at both ends;
        anything else (signs, exponents, a third group) is rejected, never reinterpreted.

        Raises:
            ValueError: text too long, not in this grammar, seconds above 59 or outside the
                range of `ThresholdPace`.
        """
        if len(text) > _TEXT_MAX_CHARS:
            raise ValueError("the pace must be written as minutes and seconds")
        found = _PACE_TEXT.fullmatch(text.strip(_ASCII_BLANKS))
        if found is None:
            raise ValueError("the pace must be written as minutes and seconds")
        seconds = int(found["seconds"] or 0)
        if seconds >= _SECONDS_PER_MINUTE:
            raise ValueError("the seconds of a pace are from 00 to 59")
        return cls(int(found["minutes"]) * _SECONDS_PER_MINUTE + seconds)


@dataclass(frozen=True, slots=True)
class ThresholdPaceEntry:
    """A pace valid from a day (included) until the next entry of its series."""

    valid_from: date
    pace: ThresholdPace


class PaceSource(Enum):
    """Where the pace in force comes from."""

    MANUAL = "manual"
    TEST = "test"


@dataclass(frozen=True, slots=True)
class PaceInForce:
    """The pace in force on a day, where it comes from and since when."""

    pace: ThresholdPace
    source: PaceSource
    since: date


def entries_of_tests(tests: Iterable[ThresholdTest]) -> tuple[ThresholdPaceEntry, ...]:
    """The tests that measured a pace, as entries dated on the test day (input order kept)."""
    return tuple(
        ThresholdPaceEntry(test.test_day, ThresholdPace(test.pace_s_per_km))
        for test in tests
        if test.pace_s_per_km is not None
    )


def _latest_until(entries: Sequence[ThresholdPaceEntry], day: date) -> ThresholdPaceEntry | None:
    """The entry of the latest date not after `day`; among equal dates the last in the sequence."""
    best: ThresholdPaceEntry | None = None
    for entry in entries:
        if entry.valid_from <= day and (best is None or entry.valid_from >= best.valid_from):
            best = entry
    return best


def threshold_pace_in_force(
    manual: Sequence[ThresholdPaceEntry], from_tests: Sequence[ThresholdPaceEntry], day: date
) -> PaceInForce | None:
    """The pace in force on `day`: the most recent between the manual series and the tests.

    Only entries not after `day` count (nothing before the first one: no backfill). On the same
    date the manual entry wins; `None` if neither series has an entry yet.
    """
    by_hand = _latest_until(manual, day)
    by_test = _latest_until(from_tests, day)
    if by_hand is not None and (by_test is None or by_hand.valid_from >= by_test.valid_from):
        return PaceInForce(by_hand.pace, PaceSource.MANUAL, by_hand.valid_from)
    if by_test is not None:
        return PaceInForce(by_test.pace, PaceSource.TEST, by_test.valid_from)
    return None
