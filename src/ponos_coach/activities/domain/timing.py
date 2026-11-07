# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Common checks on instants and durations of an activity."""

from __future__ import annotations

import math
from datetime import datetime, timedelta

from ponos_coach.activities.domain.limits import MAX_ACTIVITY_INSTANT, MIN_ACTIVITY_INSTANT


def check_utc(moment: datetime) -> None:
    """Rejects an instant without time zone or with a time zone other than UTC."""
    if moment.utcoffset() != timedelta(0):
        raise ValueError("instants must be UTC with time zone")


def truncate_to_millisecond(moment: datetime) -> datetime:
    """The instant without the fraction below the millisecond (rounded down, time zone preserved).

    The activity index saves whole milliseconds and rejects finer fractions: the
    readers and the import use case remove them here, in one single place.
    """
    return moment.replace(microsecond=moment.microsecond // 1000 * 1000)


def check_durations(elapsed_s: float, timer_s: float) -> None:
    """Finite and positive durations, with the moving time not above the elapsed time."""
    for duration in (elapsed_s, timer_s):
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError("durations must be finite and positive")
    if timer_s > elapsed_s:
        raise ValueError("the moving time cannot exceed the elapsed time")


def is_plausible_instant(moment: datetime) -> bool:
    """True if the instant (with time zone) lies in the window [1990-01-01, 2100-01-01) UTC."""
    return MIN_ACTIVITY_INSTANT <= moment < MAX_ACTIVITY_INSTANT


def is_plausible_span(start: datetime, elapsed_s: float) -> bool:
    """True if start and end (`start` + `elapsed_s`) lie in the window of allowed times.

    False also if the end is not representable (`OverflowError` becomes a no).
    """
    try:
        end = start + timedelta(seconds=elapsed_s)
    except OverflowError:
        return False
    return is_plausible_instant(start) and is_plausible_instant(end)
