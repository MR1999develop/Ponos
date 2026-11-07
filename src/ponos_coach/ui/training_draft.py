# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Rules of the training settings form, testable without a window.

A `TrainingDraft` holds the minutes for each weekday, the long day, the most time a week and the
session types ticked for each sport. `hints_of` says what the plan will not be able to do with
these settings (it never blocks saving: the plan says the same in its notices). A profile with
nothing saved starts from suggested values; the form says so.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from ponos_coach.activities.domain import Sport
from ponos_coach.goals.domain import SessionChoices, SessionType, WeeklyAvailability
from ponos_coach.goals.domain.availability import MAX_DAY_MINUTES, MAX_WEEK_MINUTES, WEEKDAYS
from ponos_coach.ui.goal_draft import RACE_SPORTS

MINUTES_PER_HOUR = 60
MAX_WEEK_HOURS = MAX_WEEK_MINUTES // MINUTES_PER_HOUR
DAY_MINUTES_STEP = 15
_SUGGESTED_DAY_MINUTES = (60, 60, 60, 60, 60, 90, 120)
_SUGGESTED_LONG_DAY = 6
_SUGGESTED_WEEK_MINUTES = 480
_SUGGESTED_TYPES = frozenset(
    {SessionType.EASY, SessionType.LONG, SessionType.THRESHOLD, SessionType.RECOVERY}
)

type DayMinutes = tuple[int, int, int, int, int, int, int]


class TrainingHint(Enum):
    """What the plan cannot do with the settings."""

    NO_TIME = "no_time"  # no day or no weekly time: no plan
    LONG_DAY_WITHOUT_TIME = "long_day_without_time"  # a long session is ticked, its day has no time


@dataclass(frozen=True, slots=True)
class TrainingDraft:
    """What the form holds. `choices` keeps the sports the form does not show, as saved."""

    day_minutes: DayMinutes
    long_day: int
    max_week_minutes: int
    choices: SessionChoices

    def __post_init__(self) -> None:
        if len(self.day_minutes) != WEEKDAYS:
            raise ValueError("a week has seven days")

    def with_day_minutes(self, weekday: int, minutes: int) -> TrainingDraft:
        """The draft with another time on a weekday (clamped to what a day can hold)."""
        value = min(max(minutes, 0), MAX_DAY_MINUTES)
        updated = list(self.day_minutes)
        updated[weekday] = value
        return TrainingDraft(_as_week(updated), self.long_day, self.max_week_minutes, self.choices)

    def with_long_day(self, weekday: int) -> TrainingDraft:
        """The draft with another long day."""
        return TrainingDraft(self.day_minutes, weekday, self.max_week_minutes, self.choices)

    def with_max_week_minutes(self, minutes: int) -> TrainingDraft:
        """The draft with another weekly maximum (clamped)."""
        value = min(max(minutes, 0), MAX_WEEK_MINUTES)
        return TrainingDraft(self.day_minutes, self.long_day, value, self.choices)

    def with_type(self, sport: Sport, session_type: SessionType, ticked: bool) -> TrainingDraft:
        """The draft with a session type ticked or unticked for a sport."""
        current = set(self.choices.for_sport(sport))
        if ticked:
            current.add(session_type)
        else:
            current.discard(session_type)
        by_sport = {s: set(types) for s, types in self.choices.by_sport}
        by_sport[sport] = current
        return TrainingDraft(
            self.day_minutes,
            self.long_day,
            self.max_week_minutes,
            SessionChoices.of(by_sport),
        )

    def availability(self) -> WeeklyAvailability:
        """The domain value."""
        return WeeklyAvailability(self.day_minutes, self.long_day, self.max_week_minutes)


def _as_week(values: list[int]) -> DayMinutes:
    first, second, third, fourth, fifth, sixth, seventh = values
    return (first, second, third, fourth, fifth, sixth, seventh)


def draft_of(availability: WeeklyAvailability | None, choices: SessionChoices) -> TrainingDraft:
    """The form filled in from what is saved; suggestions where nothing is saved yet."""
    if availability is None:
        suggested = _SUGGESTED_DAY_MINUTES
        draft = TrainingDraft(suggested, _SUGGESTED_LONG_DAY, _SUGGESTED_WEEK_MINUTES, choices)
    else:
        draft = TrainingDraft(
            availability.day_minutes,
            availability.long_day,
            availability.max_week_minutes,
            choices,
        )
    if not choices.by_sport:
        draft = TrainingDraft(
            draft.day_minutes,
            draft.long_day,
            draft.max_week_minutes,
            SessionChoices.of({Sport.RUNNING: _SUGGESTED_TYPES}),
        )
    return draft


def is_suggestion(availability: WeeklyAvailability | None, choices: SessionChoices) -> bool:
    """True when the form starts from suggestions because the profile saved nothing."""
    return availability is None or not choices.by_sport


def hints_of(draft: TrainingDraft) -> tuple[TrainingHint, ...]:
    """What the plan cannot do with these settings, in the order of the fields."""
    hints = []
    if draft.availability().is_empty:
        hints.append(TrainingHint.NO_TIME)
    long_chosen = any(SessionType.LONG in types for sport, types in draft.choices.by_sport)
    if long_chosen and draft.day_minutes[draft.long_day] == 0:
        hints.append(TrainingHint.LONG_DAY_WITHOUT_TIME)
    return tuple(hints)


def shown_sports() -> tuple[Sport, ...]:
    """The sports the form lists."""
    return RACE_SPORTS
