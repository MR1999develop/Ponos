# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Rules of the goal form, testable without a window.

A `GoalDraft` is what the user has typed (a race or a form goal); `issues_of` says which field has
which problem, as soon as it appears; `goal_of` gives the domain goal once there are no problems.
The store checks the same rules again when saving (a day with another race, for example): here the
user hears about them on the field. A goal needs a day after today, because the plan starts
tomorrow. The target time is read from text, tolerantly (`1:45:00` or `45:00`); a race of a sport
without target time (cycling) ignores whatever the field holds.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from enum import Enum

from ponos_coach.activities.domain import Sport
from ponos_coach.goals.domain import FitnessGoal, GoalId, Priority, RaceGoal, takes_target_time
from ponos_coach.goals.domain.goal import (
    MAX_ASCENT_M,
    MAX_CTL,
    MAX_DISTANCE_M,
    MAX_MEAN_GRADE_PERCENT,
    MAX_TARGET_TIME_S,
    MIN_DISTANCE_M,
    MIN_TARGET_TIME_S,
)
from ponos_coach.goals.ports import Goal

# The distances most people race, in metres; any other distance is typed in kilometres.
FIVE_KM_M = 5_000
TEN_KM_M = 10_000
HALF_MARATHON_M = 21_097
MARATHON_M = 42_195
COMMON_DISTANCES_M = (FIVE_KM_M, TEN_KM_M, HALF_MARATHON_M, MARATHON_M)

# Sports the plan can be made for (strength and the alternative session are session types).
RACE_SPORTS = (Sport.RUNNING, Sport.TRAIL, Sport.CYCLING, Sport.HIKING, Sport.WALKING)
DEFAULT_FORM_CTL = 50.0
MAX_FORM_CTL = MAX_CTL
MIN_DISTANCE_KM = MIN_DISTANCE_M / 1000
MAX_DISTANCE_KM = MAX_DISTANCE_M / 1000

_SECONDS_PER_MINUTE = 60
_MINUTES_PER_HOUR = 60
_TIME_PARTS_WITH_HOURS = 3
_TIME_PARTS_MINUTES_ONLY = 2
# An elevation gain as people type it: "1200", "1 200", "1.200", "1,200", with or without a final
# "m"; a group separator is always followed by exactly three digits.
_GROUPED_ASCENT = re.compile(r"\d{1,3}(?:[., ]\d{3})+")


class GoalFormKind(Enum):
    """What the form asks for."""

    RACE = "race"
    FORM = "form"


class GoalField(Enum):
    """The fields that can have a problem."""

    DATE = "date"
    DISTANCE = "distance"
    TARGET_TIME = "target_time"
    ASCENT = "ascent"
    CTL = "ctl"


class GoalProblem(Enum):
    """What is wrong with a field."""

    DATE_NOT_AFTER_TODAY = "date_not_after_today"
    RACE_DAY_TAKEN = "race_day_taken"
    DISTANCE_OUT_OF_RANGE = "distance_out_of_range"
    TIME_NOT_UNDERSTOOD = "time_not_understood"
    TIME_OUT_OF_RANGE = "time_out_of_range"
    ASCENT_NOT_UNDERSTOOD = "ascent_not_understood"
    ASCENT_OUT_OF_RANGE = "ascent_out_of_range"
    ASCENT_TOO_STEEP = "ascent_too_steep"
    CTL_OUT_OF_RANGE = "ctl_out_of_range"


@dataclass(frozen=True, slots=True)
class GoalIssue:
    """A problem on a field."""

    field: GoalField
    problem: GoalProblem


@dataclass(frozen=True, slots=True)
class GoalDraft:
    """What the form holds. `target_time_text` is what was typed ("" = no target time);
    `ascent_text` the elevation gain in metres as typed ("" = 0, a flat race)."""

    kind: GoalFormKind
    day: date
    priority: Priority = Priority.A
    sport: Sport = Sport.RUNNING
    distance_m: int = MARATHON_M
    target_time_text: str = ""
    ctl: float = DEFAULT_FORM_CTL
    ascent_text: str = ""


@dataclass(frozen=True, slots=True)
class NoTargetTime:
    """Nothing typed: the race has no target time."""


@dataclass(frozen=True, slots=True)
class TargetTime:
    """A time understood, in seconds."""

    seconds: int


@dataclass(frozen=True, slots=True)
class TimeNotUnderstood:
    """Text that is not `h:mm:ss` or `m:ss`."""


type ParsedTime = NoTargetTime | TargetTime | TimeNotUnderstood


def _number(text: str) -> int | None:
    stripped = text.strip()
    return int(stripped) if stripped.isascii() and stripped.isdecimal() else None


def _seconds_of(numbers: list[int]) -> int | None:
    """`h, mm, ss` or `m, ss` as seconds; `None` if the minutes or seconds pass 59 where they
    cannot."""
    if len(numbers) == _TIME_PARTS_WITH_HOURS:
        hours, minutes, seconds = numbers
        if minutes >= _MINUTES_PER_HOUR or seconds >= _SECONDS_PER_MINUTE:
            return None
        return (hours * _MINUTES_PER_HOUR + minutes) * _SECONDS_PER_MINUTE + seconds
    if len(numbers) == _TIME_PARTS_MINUTES_ONLY:
        minutes, seconds = numbers
        return None if seconds >= _SECONDS_PER_MINUTE else minutes * _SECONDS_PER_MINUTE + seconds
    return None


def parse_target_time(text: str) -> ParsedTime:
    """`h:mm:ss` or `m:ss` (minutes may pass 59); blank means no target time."""
    if not text.strip():
        return NoTargetTime()
    parts = [_number(part) for part in text.split(":")]
    numbers = [part for part in parts if part is not None]
    seconds = _seconds_of(numbers) if len(numbers) == len(parts) else None
    return TimeNotUnderstood() if seconds is None else TargetTime(seconds)


def parse_ascent(text: str) -> int | None:
    """Metres of elevation gain from what was typed: blank is 0; spaces, a group separator and a
    final "m" are tolerated; `None` for anything else (decimals, signs, letters)."""
    cleaned = text.strip().removesuffix("m").removesuffix("M").strip()
    if not cleaned:
        return 0
    if _GROUPED_ASCENT.fullmatch(cleaned):
        cleaned = re.sub(r"[., ]", "", cleaned)
    return int(cleaned) if cleaned.isascii() and cleaned.isdecimal() else None


def ascent_text(ascent_m: int) -> str:
    """An elevation gain as the form shows it ("" for a flat race)."""
    return str(ascent_m) if ascent_m else ""


def target_time_text(seconds: int | None) -> str:
    """A target time as `h:mm:ss` ("" for none)."""
    if seconds is None:
        return ""
    hours, rest = divmod(seconds, _MINUTES_PER_HOUR * _SECONDS_PER_MINUTE)
    minutes, secs = divmod(rest, _SECONDS_PER_MINUTE)
    return f"{hours}:{minutes:02d}:{secs:02d}"


def issues_of(
    draft: GoalDraft,
    goals: Sequence[Goal],
    today: date,
    editing: GoalId | None = None,
) -> tuple[GoalIssue, ...]:
    """The problems of a draft, in the order of the fields. `editing` is the goal being changed:
    it does not count as another race on its own day."""
    issues: list[GoalIssue] = []
    if draft.day <= today:
        issues.append(GoalIssue(GoalField.DATE, GoalProblem.DATE_NOT_AFTER_TODAY))
    elif draft.kind is GoalFormKind.RACE and any(
        isinstance(goal, RaceGoal) and goal.day == draft.day and goal.goal_id != editing
        for goal in goals
    ):
        issues.append(GoalIssue(GoalField.DATE, GoalProblem.RACE_DAY_TAKEN))
    if draft.kind is GoalFormKind.RACE:
        issues.extend(_race_issues(draft))
    elif not 0.0 <= draft.ctl <= MAX_FORM_CTL:
        issues.append(GoalIssue(GoalField.CTL, GoalProblem.CTL_OUT_OF_RANGE))
    return tuple(issues)


def _race_issues(draft: GoalDraft) -> list[GoalIssue]:
    issues = []
    if not MIN_DISTANCE_M <= draft.distance_m <= MAX_DISTANCE_M:
        issues.append(GoalIssue(GoalField.DISTANCE, GoalProblem.DISTANCE_OUT_OF_RANGE))
    issues.extend(_time_issues(draft))
    issues.extend(_ascent_issues(draft))
    return issues


def _time_issues(draft: GoalDraft) -> list[GoalIssue]:
    if not takes_target_time(draft.sport):
        return []
    match parse_target_time(draft.target_time_text):
        case TimeNotUnderstood():
            return [GoalIssue(GoalField.TARGET_TIME, GoalProblem.TIME_NOT_UNDERSTOOD)]
        case TargetTime(seconds) if not MIN_TARGET_TIME_S <= seconds <= MAX_TARGET_TIME_S:
            return [GoalIssue(GoalField.TARGET_TIME, GoalProblem.TIME_OUT_OF_RANGE)]
        case _:
            return []


def _ascent_issues(draft: GoalDraft) -> list[GoalIssue]:
    ascent = parse_ascent(draft.ascent_text)
    if ascent is None:
        return [GoalIssue(GoalField.ASCENT, GoalProblem.ASCENT_NOT_UNDERSTOOD)]
    if ascent > MAX_ASCENT_M:
        return [GoalIssue(GoalField.ASCENT, GoalProblem.ASCENT_OUT_OF_RANGE)]
    if ascent * 100 > draft.distance_m * MAX_MEAN_GRADE_PERCENT:
        return [GoalIssue(GoalField.ASCENT, GoalProblem.ASCENT_TOO_STEEP)]
    return []


def goal_of(
    draft: GoalDraft,
    goals: Sequence[Goal],
    today: date,
    goal_id: GoalId,
) -> Goal | None:
    """The domain goal of a draft, or `None` while it has a problem."""
    if issues_of(draft, goals, today, goal_id):
        return None
    if draft.kind is GoalFormKind.FORM:
        return FitnessGoal(goal_id, draft.day, draft.ctl)
    parsed = parse_target_time(draft.target_time_text)
    seconds = (
        parsed.seconds
        if isinstance(parsed, TargetTime) and takes_target_time(draft.sport)
        else None
    )
    return RaceGoal(
        goal_id,
        draft.day,
        draft.priority,
        draft.sport,
        draft.distance_m,
        seconds,
        parse_ascent(draft.ascent_text) or 0,
    )


def draft_of(goal: Goal) -> GoalDraft:
    """The form filled in from a saved goal."""
    if isinstance(goal, FitnessGoal):
        return GoalDraft(GoalFormKind.FORM, goal.day, ctl=goal.ctl)
    return GoalDraft(
        GoalFormKind.RACE,
        goal.day,
        goal.priority,
        goal.sport,
        goal.distance_m,
        target_time_text(goal.planned_target_time_s),
        ascent_text=ascent_text(goal.ascent_m),
    )


def default_draft(kind: GoalFormKind, day: date) -> GoalDraft:
    """A new goal on a day (the default priority is A, the default race a marathon)."""
    return GoalDraft(kind, day)


__all__ = [
    "COMMON_DISTANCES_M",
    "DEFAULT_FORM_CTL",
    "MAX_ASCENT_M",
    "MAX_DISTANCE_KM",
    "MAX_FORM_CTL",
    "MIN_DISTANCE_KM",
    "RACE_SPORTS",
    "GoalDraft",
    "GoalField",
    "GoalFormKind",
    "GoalIssue",
    "GoalProblem",
    "NoTargetTime",
    "ParsedTime",
    "TargetTime",
    "TimeNotUnderstood",
    "ascent_text",
    "default_draft",
    "draft_of",
    "goal_of",
    "issues_of",
    "parse_ascent",
    "parse_target_time",
    "target_time_text",
]
