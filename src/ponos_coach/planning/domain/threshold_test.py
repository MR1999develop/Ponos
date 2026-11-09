# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""The threshold test among the sessions of the plan: which planned tests went by undone.

The engine plans the test once, and once more if the first one was missed (ADR 015). It has no
memory, so the caller tells it which planned tests were missed: this is how that is worked out
from the days the plan had frozen and the tests that were applied.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date

from ponos_coach.planning.domain.plan import PlanDay


def missed_threshold_tests(
    planned: Iterable[PlanDay], test_days: Iterable[date], today: date
) -> tuple[date, ...]:
    """The days, ascending and without repeats, on which the plan had a threshold test that was
    not done.

    A planned test is missed if its day is before `today` (a test of today may still be done) and
    no test was applied on that day or later (a test done earlier is seen by the retest window:
    the engine then plans none). Days with no test session are ignored.
    """
    done = sorted(set(test_days))
    last_test = done[-1] if done else None
    return tuple(
        sorted(
            {
                item.day
                for item in planned
                if item.session is not None
                and item.session.threshold_test is not None
                and item.day < today
                and (last_test is None or last_test < item.day)
            }
        )
    )
