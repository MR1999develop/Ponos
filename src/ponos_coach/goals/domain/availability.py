# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Weekly availability: the days the user can train, how many sessions a week, the long day and
the weekly maximum."""

from __future__ import annotations

from dataclasses import dataclass

WEEKDAYS = 7  # Monday is 0, as in `date.weekday()`
MAX_WEEK_MINUTES = 6000  # 100 hours


@dataclass(frozen=True, slots=True)
class WeeklyAvailability:
    """When the user can train.

    `days` are the weekdays (0 = Monday) the user can train on; `sessions_per_week` is how many
    of them get a session (`session_days` says which); `long_day` is the weekday of the long
    session, always one of the days; `max_week_minutes` is the most the user trains in a week
    (hours x 60), the plan never exceeds it.

    The empty availability (no day, no session, no time) is the "nothing set" state: it is the
    only one with no days, and `long_day` is then not used.
    """

    days: frozenset[int]
    sessions_per_week: int
    long_day: int
    max_week_minutes: int

    def __post_init__(self) -> None:
        if not all(0 <= day < WEEKDAYS for day in self.days):
            raise ValueError("a day is a weekday between 0 (Monday) and 6 (Sunday)")
        if not 0 <= self.long_day < WEEKDAYS:
            raise ValueError("the long day is a weekday between 0 (Monday) and 6 (Sunday)")
        if not 0 <= self.max_week_minutes <= MAX_WEEK_MINUTES:
            raise ValueError(f"weekly minutes must be in [0, {MAX_WEEK_MINUTES}]")
        self._check_days_and_sessions()

    def _check_days_and_sessions(self) -> None:
        if not self.days:
            if self.sessions_per_week != 0 or self.max_week_minutes != 0:
                raise ValueError("with no day available there are no sessions and no time")
            return
        if not 1 <= self.sessions_per_week <= len(self.days):
            raise ValueError("sessions per week must be between 1 and the number of days")
        if self.long_day not in self.days:
            raise ValueError("the long day must be one of the available days")

    @classmethod
    def empty(cls) -> WeeklyAvailability:
        """Nothing available: no day, no session, no time."""
        return cls(frozenset(), 0, 0, 0)

    @property
    def is_empty(self) -> bool:
        """True if no training is possible at all (no available day or no weekly time)."""
        return self.max_week_minutes == 0 or not self.days

    @property
    def session_days(self) -> frozenset[int]:
        """The weekdays that get a session: `sessions_per_week` of the available days.

        Always the long day; then, one at a time, the day farthest from the ones already chosen
        (counting the week as a circle: Sunday is next to Monday), the lowest weekday on a tie.
        So the sessions are spread as evenly as the days allow and avoid consecutive days when
        they can. Same availability, same days.
        """
        if not self.days:
            return frozenset()
        chosen = {self.long_day}
        while len(chosen) < self.sessions_per_week:
            rest = sorted(self.days - chosen)
            chosen.add(max(rest, key=lambda day: (_gap(day, chosen), -day)))
        return frozenset(chosen)


def _gap(day: int, chosen: set[int]) -> int:
    """Days between `day` and the nearest chosen day, on the circle of the week."""
    return min(min((day - c) % WEEKDAYS, (c - day) % WEEKDAYS) for c in chosen)
