# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Possible-duplicate rule across formats: same start and same duration (PLAN.md 4.3).

One single definition: indexes, fakes and use cases use it; an adapter may prefilter with
a wider interval but then applies this function.
"""

from __future__ import annotations

from datetime import datetime, timedelta

DUPLICATE_START_TOLERANCE = timedelta(seconds=60)  # project default
DUPLICATE_DURATION_TOLERANCE_PERCENT = 5  # project default
_MS_PER_S = 1000
_PERCENT = 100


def is_possible_duplicate(
    start_a: datetime, elapsed_a_s: float, start_b: datetime, elapsed_b_s: float
) -> bool:
    """True if two activities could be the same one recorded in two formats.

    Starts are at most 60 s apart and elapsed times differ by at most 5% of the larger
    (comparison in whole milliseconds, endpoints included). Symmetric: arrival order does not
    matter.
    """
    if abs(start_a - start_b) > DUPLICATE_START_TOLERANCE:
        return False
    ms_a, ms_b = round(elapsed_a_s * _MS_PER_S), round(elapsed_b_s * _MS_PER_S)
    return abs(ms_a - ms_b) * _PERCENT <= DUPLICATE_DURATION_TOLERANCE_PERCENT * max(ms_a, ms_b)
