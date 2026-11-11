# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""When to repeat the threshold test.

Proposed from 42 to 56 days after the last applied test. The unload week of the plan arrives with
the plan (phase 5): for now only the window of dates.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta

from ponos_coach.assessment.domain.defaults import RETEST_MAX_DAYS, RETEST_MIN_DAYS


@dataclass(frozen=True, slots=True)
class NoTest:
    """No test applied yet: the first one is due."""


@dataclass(frozen=True, slots=True)
class NotYet:
    """The last test is recent: the retest window opens on `first_day`."""

    first_day: date
    last_day: date


@dataclass(frozen=True, slots=True)
class Recommended:
    """Today is inside the retest window, `first_day` to `last_day` both included."""

    first_day: date
    last_day: date


@dataclass(frozen=True, slots=True)
class Overdue:
    """The retest window closed `days` (at least 1) days ago."""

    days: int
    last_day: date


type RetestStatus = NoTest | NotYet | Recommended | Overdue


def retest_status(test_days: Iterable[date], today: date) -> RetestStatus:
    """Where `today` stands against the retest window of the latest of `test_days`.

    The window runs from the 42nd to the 56th day after the last test, both included: 41 days
    after is "not yet", 42 and 56 are "recommended", 57 is "overdue by 1 day".
    """
    last_test = max(test_days, default=None)
    if last_test is None:
        return NoTest()
    first_day = last_test + timedelta(days=RETEST_MIN_DAYS)
    last_day = last_test + timedelta(days=RETEST_MAX_DAYS)
    if today < first_day:
        return NotYet(first_day, last_day)
    if today <= last_day:
        return Recommended(first_day, last_day)
    return Overdue((today - last_day).days, last_day)
