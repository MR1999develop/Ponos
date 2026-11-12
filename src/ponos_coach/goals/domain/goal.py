# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Goals: races with a priority and a form goal (CTL at a date). Valid by construction."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import date
from enum import Enum

from ponos_coach.activities.domain import Sport

MIN_DISTANCE_M = 400
MAX_DISTANCE_M = 500_000
# Positive elevation gain of a race. The most of any known race is about 24 000 m (330 km), so
# 30 000 m leaves room; a race is never steeper on average than MAX_MEAN_GRADE_PERCENT.
MAX_ASCENT_M = 30_000
MAX_MEAN_GRADE_PERCENT = 50
MIN_TARGET_TIME_S = 60
MAX_TARGET_TIME_S = 7 * 24 * 3600
MAX_CTL = 200.0
_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")
_FIRST_DAY = date(1900, 1, 1)
# Races planned from distance and elevation gain only, never from a target time (user's request,
# 2026-11-11: a cycling race has no target time).
UNTIMED_RACE_SPORTS = frozenset({Sport.CYCLING})


def takes_target_time(sport: Sport) -> bool:
    """True if a race of this sport can have a target time."""
    return sport not in UNTIMED_RACE_SPORTS


class Priority(Enum):
    """Priority of a race: A is the season goal, B an important race, C a training race."""

    A = "A"
    B = "B"
    C = "C"


@dataclass(frozen=True, slots=True)
class GoalId:
    """Stable identifier of a goal (1-64 characters of letters, digits, `_` and `-`)."""

    value: str

    def __post_init__(self) -> None:
        if len(self.value) > 64 or not _ID.fullmatch(self.value):
            raise ValueError("a goal id is 1-64 letters, digits, '_' or '-'")


@dataclass(frozen=True, slots=True)
class RaceGoal:
    """A race on a date: sport, distance in metres, optional target time in seconds, and the
    positive elevation gain `ascent_m` in metres (0 = a flat race, the default)."""

    goal_id: GoalId
    day: date
    priority: Priority
    sport: Sport
    distance_m: int
    target_time_s: int | None = None
    ascent_m: int = 0

    def __post_init__(self) -> None:
        if self.day < _FIRST_DAY:
            raise ValueError("a goal date cannot precede 1900-01-01")
        if not MIN_DISTANCE_M <= self.distance_m <= MAX_DISTANCE_M:
            raise ValueError(f"distance must be in [{MIN_DISTANCE_M}, {MAX_DISTANCE_M}] m")
        if not 0 <= self.ascent_m <= MAX_ASCENT_M:
            raise ValueError(f"elevation gain must be in [0, {MAX_ASCENT_M}] m")
        if self.ascent_m * 100 > self.distance_m * MAX_MEAN_GRADE_PERCENT:
            raise ValueError(f"mean grade cannot exceed {MAX_MEAN_GRADE_PERCENT}%")
        if self.target_time_s is not None and not (
            MIN_TARGET_TIME_S <= self.target_time_s <= MAX_TARGET_TIME_S
        ):
            raise ValueError("target time outside the plausible range")

    @property
    def planned_target_time_s(self) -> int | None:
        """The target time the plan works with: none for a sport whose races have none (a cycling
        race saved with a time before 2026-11-11 keeps it in the store, but the plan ignores it)."""
        return self.target_time_s if takes_target_time(self.sport) else None

    @property
    def ascent_m_per_km(self) -> float:
        """Mean climb per kilometre (0.0 for a flat race)."""
        return self.ascent_m * 1000 / self.distance_m


@dataclass(frozen=True, slots=True)
class FitnessGoal:
    """A fitness goal: reach a CTL (0 to 200) on a date."""

    goal_id: GoalId
    day: date
    ctl: float

    def __post_init__(self) -> None:
        if self.day < _FIRST_DAY:
            raise ValueError("a goal date cannot precede 1900-01-01")
        if not math.isfinite(self.ctl) or not 0.0 <= self.ctl <= MAX_CTL:
            raise ValueError(f"CTL must be in [0, {MAX_CTL}]")
