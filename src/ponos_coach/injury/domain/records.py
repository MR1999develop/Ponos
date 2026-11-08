# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""What the user tells the app: the questionnaire, the daily check-ins, the illness.

Health data: types valid by construction, never in logs or error messages. The doctor's notes are
free text that stays on this computer and that nothing reads.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date

from ponos_coach.injury.domain import defaults
from ponos_coach.injury.domain.vocabulary import (
    BodyZone,
    IllnessKind,
    InjuryId,
    Onset,
    PainFreeActivity,
    PainScore,
    Side,
    WarningSign,
)

_FIRST_DAY = date(defaults.FIRST_DAY_YEAR, 1, 1)
_ALLOWED_CONTROLS = "\n\t"


class InvalidChangeError(ValueError):
    """The change does not fit the record (a check-in before the opening, on a closed injury...)."""


@dataclass(frozen=True, slots=True)
class PainReport:
    """Pain from 0 to 10 at rest, walking, running and the morning after.

    `running` is `None` when the user cannot (or did not) run: it is then not part of any rule.
    """

    rest: PainScore
    walking: PainScore
    running: PainScore | None
    next_morning: PainScore


def _check_notes(text: str) -> None:
    if len(text) > defaults.MAX_NOTES_CHARS:
        raise ValueError(f"notes are at most {defaults.MAX_NOTES_CHARS} characters")
    if any(ord(ch) < 32 and ch not in _ALLOWED_CONTROLS for ch in text) or "\x7f" in text:
        raise ValueError("notes contain control characters")


@dataclass(frozen=True, slots=True)
class Questionnaire:
    """The answers when an injury is opened: zone, side, how and when it began, the pain, what
    does not hurt, the warning signs ticked, the doctor's notes (free text, may be empty)."""

    zone: BodyZone
    side: Side
    onset: Onset
    onset_day: date
    pain: PainReport
    pain_free: frozenset[PainFreeActivity] = frozenset()
    signs: frozenset[WarningSign] = frozenset()
    doctor_notes: str = ""

    def __post_init__(self) -> None:
        if self.onset_day < _FIRST_DAY:
            raise ValueError("the onset cannot precede 1900-01-01")
        _check_notes(self.doctor_notes)

    def alarms(self) -> frozenset[WarningSign]:
        """The ticked signs, and strong rest pain if the rest pain reaches the limit."""
        if self.pain.rest.value >= defaults.STRONG_REST_PAIN:
            return self.signs | {WarningSign.STRONG_REST_PAIN}
        return self.signs


@dataclass(frozen=True, slots=True)
class CheckIn:
    """One day's answers: pain during the day's activity (`None` if there was none), pain this
    morning (after the day before), and any warning sign."""

    day: date
    pain_during: PainScore | None
    pain_morning: PainScore
    signs: frozenset[WarningSign] = frozenset()

    def __post_init__(self) -> None:
        if self.day < _FIRST_DAY:
            raise ValueError("a check-in cannot precede 1900-01-01")

    @property
    def pain_free(self) -> bool:
        """No pain this morning and none during the activity."""
        return self.pain_morning.value == 0 and (
            self.pain_during is None or self.pain_during.value == 0
        )


@dataclass(frozen=True, slots=True)
class Injury:
    """An injury being followed: the questionnaire, the check-ins by ascending day (one a day),
    the day of the medical review that cleared the warning signs, the day it was closed.

    The step of the path is not stored: it follows from the check-ins (`follow`).
    """

    injury_id: InjuryId
    opened_on: date
    questionnaire: Questionnaire
    checkins: tuple[CheckIn, ...] = ()
    reviewed_on: date | None = None
    closed_on: date | None = None

    def __post_init__(self) -> None:
        if self.opened_on < self.questionnaire.onset_day:
            raise ValueError("an injury cannot be opened before it began")
        if len(self.checkins) > defaults.MAX_CHECKINS:
            raise ValueError("too many check-ins")
        days = [c.day for c in self.checkins]
        if days != sorted(set(days)) or (days and days[0] < self.opened_on):
            raise ValueError("check-ins are one a day, ascending, from the opening on")
        if self.reviewed_on is not None and self.reviewed_on < self.opened_on:
            raise ValueError("the review cannot precede the opening")
        last = max([self.opened_on, *days, *([self.reviewed_on] if self.reviewed_on else [])])
        if self.closed_on is not None and self.closed_on < last:
            raise ValueError("an injury cannot be closed before its last record")

    @property
    def is_open(self) -> bool:
        """True until the user closes it."""
        return self.closed_on is None

    def active_alarms(self) -> frozenset[WarningSign]:
        """The warning signs reported since the last medical review (all, if there was none)."""
        reviewed = self.reviewed_on
        signs: set[WarningSign] = set()
        if reviewed is None:
            signs |= self.questionnaire.alarms()
        for checkin in self.checkins:
            if reviewed is None or checkin.day > reviewed:
                signs |= checkin.signs
        return frozenset(signs)

    def with_checkin(self, checkin: CheckIn) -> Injury:
        """The injury with this check-in; one for the same day is replaced.

        Raises:
            InvalidChangeError: closed, before the opening, or too many check-ins.
        """
        if self.closed_on is not None:
            raise InvalidChangeError("the injury is closed")
        if checkin.day < self.opened_on:
            raise InvalidChangeError("a check-in cannot precede the opening")
        others = tuple(c for c in self.checkins if c.day != checkin.day)
        if len(others) >= defaults.MAX_CHECKINS:
            raise InvalidChangeError("too many check-ins")
        merged = tuple(sorted((*others, checkin), key=lambda c: c.day))
        return replace(self, checkins=merged)

    def with_review(self, day: date) -> Injury:
        """The injury with the signs reported until `day` marked as seen by a doctor.

        Raises:
            InvalidChangeError: closed, or the day precedes the opening or the last review.
        """
        if self.closed_on is not None:
            raise InvalidChangeError("the injury is closed")
        if day < self.opened_on or (self.reviewed_on is not None and day < self.reviewed_on):
            raise InvalidChangeError("the review day is out of order")
        return replace(self, reviewed_on=day)

    def closed(self, day: date) -> Injury:
        """The injury closed on `day`.

        Raises:
            InvalidChangeError: already closed, or before the last record.
        """
        if self.closed_on is not None:
            raise InvalidChangeError("the injury is already closed")
        try:
            return replace(self, closed_on=day)
        except ValueError:
            raise InvalidChangeError("closed before the last record") from None


@dataclass(frozen=True, slots=True)
class Illness:
    """Not feeling well: the group of symptoms, from when to when."""

    illness_id: InjuryId
    kind: IllnessKind
    started_on: date
    recovered_on: date | None = None

    def __post_init__(self) -> None:
        if self.started_on < _FIRST_DAY:
            raise ValueError("an illness cannot start before 1900-01-01")
        if self.recovered_on is not None and self.recovered_on < self.started_on:
            raise ValueError("recovery before the start")

    @property
    def is_open(self) -> bool:
        """True until the user says they feel well."""
        return self.recovered_on is None

    def recovered(self, day: date) -> Illness:
        """The illness over on `day`.

        Raises:
            InvalidChangeError: already over, or before the start.
        """
        if self.recovered_on is not None:
            raise InvalidChangeError("the illness is already over")
        if day < self.started_on:
            raise InvalidChangeError("recovery before the start")
        return replace(self, recovered_on=day)


type Problem = Injury | Illness


def problem_id(problem: Problem) -> InjuryId:
    """The id of an injury or an illness."""
    return problem.injury_id if isinstance(problem, Injury) else problem.illness_id


def problem_end(problem: Problem) -> date | None:
    """The day it was closed, or `None` while it lasts."""
    return problem.closed_on if isinstance(problem, Injury) else problem.recovered_on
