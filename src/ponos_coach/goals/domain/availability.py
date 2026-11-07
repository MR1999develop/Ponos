# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Weekly availability: minutes per weekday, the day of the long session, the weekly maximum."""

from __future__ import annotations

from dataclasses import dataclass

WEEKDAYS = 7  # Monday is 0, as in `date.weekday()`
MAX_DAY_MINUTES = 720  # 12 hours: beyond this a figure is a typing mistake
MAX_WEEK_MINUTES = 6000  # 100 hours


@dataclass(frozen=True, slots=True)
class WeeklyAvailability:
    """When the user can train.

    `day_minutes` has seven entries, Monday first: the most the user can train that day, 0 =
    not available. `long_day` is the weekday (0 = Monday) of the long session.
    `max_week_minutes` is the most the user trains in a week (hours x 60), the plan
    never exceeds it.
    """

    day_minutes: tuple[int, int, int, int, int, int, int]
    long_day: int
    max_week_minutes: int

    def __post_init__(self) -> None:
        if not all(0 <= minutes <= MAX_DAY_MINUTES for minutes in self.day_minutes):
            raise ValueError(f"minutes per day must be in [0, {MAX_DAY_MINUTES}]")
        if not 0 <= self.long_day < WEEKDAYS:
            raise ValueError("the long day is a weekday between 0 (Monday) and 6 (Sunday)")
        if not 0 <= self.max_week_minutes <= MAX_WEEK_MINUTES:
            raise ValueError(f"weekly minutes must be in [0, {MAX_WEEK_MINUTES}]")

    @property
    def is_empty(self) -> bool:
        """True if no training is possible at all (no available day or no weekly time)."""
        return self.max_week_minutes == 0 or not any(self.day_minutes)

    def minutes_on(self, weekday: int) -> int:
        """Minutes available on a weekday (0 = Monday)."""
        return self.day_minutes[weekday]
