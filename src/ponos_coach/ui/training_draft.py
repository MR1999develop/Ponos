"""Rules of the training settings form, testable without a window.

A `TrainingDraft` holds the weekdays the user can train on, how many sessions a week, the long day,
the most time a week and the session types ticked for each sport. `hints_of` says what the plan
will not be able to do with these settings (it never blocks saving: the plan says the same in its
notices). A profile with nothing saved starts from suggested values; the form says so.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from ponos_coach.activities.domain import Sport
from ponos_coach.goals.domain import (
    SUGGESTED_TYPES,
    SessionChoices,
    SessionType,
    WeeklyAvailability,
)
from ponos_coach.goals.domain.availability import MAX_WEEK_MINUTES, WEEKDAYS
from ponos_coach.ui.goal_draft import RACE_SPORTS

MINUTES_PER_HOUR = 60
MAX_WEEK_HOURS = MAX_WEEK_MINUTES // MINUTES_PER_HOUR
_ALL_DAYS = frozenset(range(WEEKDAYS))
_SUGGESTED_SESSIONS = 4
_SUGGESTED_LONG_DAY = 6
_SUGGESTED_WEEK_MINUTES = 480


class TrainingHint(Enum):
    """What the plan cannot do with the settings."""

    NO_TIME = "no_time"  # no day or no weekly time: no plan


@dataclass(frozen=True, slots=True)
class TrainingDraft:
    """What the form holds. `choices` keeps the sports the form does not show, as saved.

    `sessions` is at least 1 and, while some day is ticked, at most the number of ticked days;
    `long_day` is one of the ticked days. With no day ticked both keep what they were, for when a
    day is ticked again (the settings are then the empty availability).
    """

    days: frozenset[int]
    sessions: int
    long_day: int
    max_week_minutes: int
    choices: SessionChoices

    def __post_init__(self) -> None:
        if not all(0 <= day < WEEKDAYS for day in self.days) or not 0 <= self.long_day < WEEKDAYS:
            raise ValueError("a week has seven days")
        if not 1 <= self.sessions <= WEEKDAYS:
            raise ValueError("sessions per week must be between 1 and 7")
        if self.days and (self.sessions > len(self.days) or self.long_day not in self.days):
            raise ValueError("sessions and the long day must fit the ticked days")

    def with_day(self, weekday: int, ticked: bool) -> TrainingDraft:
        """The draft with a weekday ticked or unticked.

        A session is planned on every ticked day until the user asks for fewer, so ticking a day
        adds a session if all the ticked days had one; unticking keeps the sessions within the
        days. The long day moves to the last ticked day if its day is unticked.
        """
        days = self.days | {weekday} if ticked else self.days - {weekday}
        sessions = self.sessions
        if ticked and weekday not in self.days and sessions == len(self.days):
            sessions = min(sessions + 1, WEEKDAYS)
        if days:
            sessions = min(sessions, len(days))
        long_day = self.long_day if not days or self.long_day in days else max(days)
        return TrainingDraft(
            frozenset(days), sessions, long_day, self.max_week_minutes, self.choices
        )

    def with_sessions(self, sessions: int) -> TrainingDraft:
        """The draft with another number of sessions a week (clamped to 1 and the ticked days)."""
        value = min(max(sessions, 1), len(self.days) or WEEKDAYS)
        return TrainingDraft(self.days, value, self.long_day, self.max_week_minutes, self.choices)

    def with_long_day(self, weekday: int) -> TrainingDraft:
        """The draft with another long day; a day that is not ticked becomes ticked."""
        base = self.with_day(weekday, True) if self.days else self
        return TrainingDraft(base.days, base.sessions, weekday, base.max_week_minutes, base.choices)

    def with_max_week_minutes(self, minutes: int) -> TrainingDraft:
        """The draft with another weekly maximum (clamped)."""
        value = min(max(minutes, 0), MAX_WEEK_MINUTES)
        return TrainingDraft(self.days, self.sessions, self.long_day, value, self.choices)

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
            self.days,
            self.sessions,
            self.long_day,
            self.max_week_minutes,
            SessionChoices.of(by_sport),
        )

    def availability(self) -> WeeklyAvailability:
        """The domain value (the empty availability while no day is ticked)."""
        if not self.days:
            return WeeklyAvailability.empty()
        return WeeklyAvailability(self.days, self.sessions, self.long_day, self.max_week_minutes)


def draft_of(availability: WeeklyAvailability | None, choices: SessionChoices) -> TrainingDraft:
    """The form filled in from what is saved; suggestions where nothing is saved yet."""
    if availability is None:
        draft = TrainingDraft(
            _ALL_DAYS,
            _SUGGESTED_SESSIONS,
            _SUGGESTED_LONG_DAY,
            _SUGGESTED_WEEK_MINUTES,
            choices,
        )
    elif not availability.days:
        draft = TrainingDraft(
            frozenset(), 1, _SUGGESTED_LONG_DAY, availability.max_week_minutes, choices
        )
    else:
        draft = TrainingDraft(
            availability.days,
            availability.sessions_per_week,
            availability.long_day,
            availability.max_week_minutes,
            choices,
        )
    if not choices.by_sport:
        draft = TrainingDraft(
            draft.days,
            draft.sessions,
            draft.long_day,
            draft.max_week_minutes,
            SessionChoices.of({Sport.RUNNING: SUGGESTED_TYPES}),
        )
    return draft


def is_suggestion(availability: WeeklyAvailability | None, choices: SessionChoices) -> bool:
    """True when the form starts from suggestions because the profile saved nothing."""
    return availability is None or not choices.by_sport


def hints_of(draft: TrainingDraft) -> tuple[TrainingHint, ...]:
    """What the plan cannot do with these settings, in the order of the fields."""
    return (TrainingHint.NO_TIME,) if draft.availability().is_empty else ()


def shown_sports() -> tuple[Sport, ...]:
    """The sports the form lists."""
    return RACE_SPORTS
