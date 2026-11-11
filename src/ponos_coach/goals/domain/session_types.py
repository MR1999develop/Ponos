# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Session types the user may choose for each sport (the plan schedules only these)."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import Enum

from ponos_coach.activities.domain import Sport


class SessionType(Enum):
    """Kind of training session. The value is a stable identifier (used in saved data)."""

    EASY = "easy"
    LONG = "long"
    MEDIUM = "medium"
    THRESHOLD = "threshold"
    VO2MAX = "vo2max"
    RECOVERY = "recovery"
    STRENGTH = "strength"
    ALTERNATIVE = "alternative"


# Sessions that count as intense: at most 3 a week (2 in the base phase).
INTENSE_TYPES = frozenset({SessionType.MEDIUM, SessionType.THRESHOLD, SessionType.VO2MAX})
# Sessions that are hard on the body: two of them never fall on consecutive days.
HARD_TYPES = INTENSE_TYPES | {SessionType.LONG}
# What the plan schedules for a sport the user chose nothing for (user's request, 2026-11-11: a
# cycling race brings cycling sessions without first ticking them in the training settings).
SUGGESTED_TYPES = frozenset(
    {SessionType.EASY, SessionType.LONG, SessionType.THRESHOLD, SessionType.RECOVERY}
)


@dataclass(frozen=True, slots=True)
class SessionChoices:
    """The session types chosen for each sport; a sport without an entry has none.

    Stored in a canonical form (sorted by sport), so two equal choices compare equal.
    """

    by_sport: tuple[tuple[Sport, frozenset[SessionType]], ...]

    def __post_init__(self) -> None:
        sports = [sport.value for sport, _ in self.by_sport]
        if sports != sorted(set(sports)):
            raise ValueError("choices must be sorted by sport, one entry per sport")

    @classmethod
    def of(cls, choices: Mapping[Sport, Iterable[SessionType]]) -> SessionChoices:
        """Builds the canonical form from any mapping; sports with no type are dropped."""
        entries = {sport: frozenset(types) for sport, types in choices.items()}
        return cls(
            tuple(
                sorted(
                    ((sport, types) for sport, types in entries.items() if types),
                    key=lambda entry: entry[0].value,
                )
            )
        )

    def for_sport(self, sport: Sport) -> frozenset[SessionType]:
        """The session types chosen for the sport (empty if none)."""
        for chosen_sport, types in self.by_sport:
            if chosen_sport is sport:
                return types
        return frozenset()

    def for_sport_or_suggested(self, sport: Sport) -> frozenset[SessionType]:
        """The session types chosen for the sport, or `SUGGESTED_TYPES` if none is."""
        return self.for_sport(sport) or SUGGESTED_TYPES
