# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""What the user types in the injury forms, and how it becomes a record (or says it cannot).

A draft is plain values; the domain types (`Questionnaire`, `Injury`, `CheckIn`, `Illness`) are
valid by construction, so a draft that does not fit gives `None` and the form says why before.
Testable without a window. Health data: never in logs or error messages.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, replace
from datetime import date

from ponos_coach.injury.domain import (
    BodyZone,
    CheckIn,
    Illness,
    IllnessKind,
    Injury,
    InjuryId,
    Onset,
    PainFreeActivity,
    PainReport,
    PainScore,
    Questionnaire,
    Side,
    WarningSign,
    defaults,
)

NO_ANSWER = -1  # the pain spin boxes' special value: "cannot run" or "no activity today"
MAX_NOTES = defaults.MAX_NOTES_CHARS


def new_problem_id() -> InjuryId:
    """A fresh id (the interface chooses it, so a repeated command is refused, not doubled)."""
    return InjuryId(uuid.uuid4().hex)


def _score(value: int) -> PainScore | None:
    return None if value == NO_ANSWER else PainScore(value)


@dataclass(frozen=True, slots=True)
class QuestionnaireDraft:
    """The answers of the questionnaire as typed. `running` is `NO_ANSWER` when the user cannot
    run."""

    zone: BodyZone = BodyZone.KNEE
    side: Side = Side.LEFT
    onset: Onset = Onset.GRADUAL
    onset_day: date = date(2026, 1, 1)
    rest: int = 0
    walking: int = 0
    running: int = NO_ANSWER
    next_morning: int = 0
    pain_free: frozenset[PainFreeActivity] = frozenset()
    signs: frozenset[WarningSign] = frozenset()
    notes: str = ""


def questionnaire_of(draft: QuestionnaireDraft) -> Questionnaire | None:
    """The questionnaire, or `None` if the draft does not fit (pain out of the scale, notes too long
    or with control characters)."""
    try:
        return Questionnaire(
            draft.zone,
            draft.side,
            draft.onset,
            draft.onset_day,
            PainReport(
                PainScore(draft.rest),
                PainScore(draft.walking),
                _score(draft.running),
                PainScore(draft.next_morning),
            ),
            draft.pain_free,
            draft.signs,
            draft.notes,
        )
    except ValueError:
        return None


def injury_of(draft: QuestionnaireDraft, injury_id: InjuryId, today: date) -> Injury | None:
    """The injury opened `today` from the draft, or `None` if it does not fit (the onset is after
    today, or the answers do not)."""
    questionnaire = questionnaire_of(draft)
    if questionnaire is None:
        return None
    try:
        return Injury(injury_id, today, questionnaire)
    except ValueError:
        return None


def alarms_of(draft: QuestionnaireDraft) -> frozenset[WarningSign]:
    """The warning signs the draft carries: those ticked, and strong rest pain if the rest pain
    reaches the limit. Empty if the answers do not fit (the notes are not looked at)."""
    questionnaire = questionnaire_of(replace(draft, notes=""))
    return frozenset() if questionnaire is None else questionnaire.alarms()


def notes_problem(notes: str) -> bool:
    """True if the notes cannot be saved (too long, or control characters)."""
    return questionnaire_of(QuestionnaireDraft(notes=notes)) is None


@dataclass(frozen=True, slots=True)
class CheckInDraft:
    """One day's answers as typed: `pain_during` is `NO_ANSWER` when there was no activity."""

    pain_during: int = NO_ANSWER
    pain_morning: int = 0
    signs: frozenset[WarningSign] = frozenset()


def checkin_of(day: date, draft: CheckInDraft) -> CheckIn | None:
    """The check-in of `day`, or `None` if the pain is out of the scale."""
    try:
        return CheckIn(day, _score(draft.pain_during), PainScore(draft.pain_morning), draft.signs)
    except ValueError:
        return None


def draft_of_checkin(checkin: CheckIn | None) -> CheckInDraft:
    """The draft that shows a saved check-in again (a new one if there is none)."""
    if checkin is None:
        return CheckInDraft()
    during = NO_ANSWER if checkin.pain_during is None else checkin.pain_during.value
    return CheckInDraft(during, checkin.pain_morning.value, checkin.signs)


def illness_of(kind: IllnessKind, started_on: date, illness_id: InjuryId) -> Illness | None:
    """The illness that began on `started_on`, or `None` if the date does not fit."""
    try:
        return Illness(illness_id, kind, started_on)
    except ValueError:
        return None
