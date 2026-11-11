# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Builders of injuries for the tests: neutral data, one obvious default for everything."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, timedelta

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
    PathState,
    Questionnaire,
    Side,
    Step,
    WarningSign,
    advance,
    follow,
)

DAY0 = date(2026, 3, 2)  # a Monday: the day the injury is opened


def pain(value: int) -> PainScore:
    """A pain score."""
    return PainScore(value)


def questionnaire(
    zone: BodyZone = BodyZone.SHIN,
    *,
    onset: Onset = Onset.GRADUAL,
    morning: int = 2,
    rest: int = 1,
    walking: int = 2,
    running: int | None = 4,
    free: Iterable[PainFreeActivity] = (PainFreeActivity.CYCLING, PainFreeActivity.SWIMMING),
    signs: Iterable[WarningSign] = (),
    notes: str = "",
) -> Questionnaire:
    """The answers of a typical case: some pain at the shin, cycling and swimming do not hurt."""
    return Questionnaire(
        zone,
        Side.LEFT,
        onset,
        DAY0 - timedelta(days=10),
        PainReport(
            pain(rest), pain(walking), None if running is None else pain(running), pain(morning)
        ),
        frozenset(free),
        frozenset(signs),
        notes,
    )


def injury(
    injury_id: str = "i1",
    q: Questionnaire | None = None,
    checkins: Iterable[CheckIn] = (),
    *,
    opened_on: date = DAY0,
) -> Injury:
    """An injury opened on `DAY0` with the given questionnaire and check-ins."""
    return Injury(InjuryId(injury_id), opened_on, q or questionnaire(), tuple(checkins))


def illness(kind: IllnessKind = IllnessKind.SYSTEMIC, illness_id: str = "ill1") -> Illness:
    """An open illness since `DAY0`."""
    return Illness(InjuryId(illness_id), kind, DAY0)


def checkin(
    offset: int,
    during: int | None = 1,
    morning: int = 0,
    signs: Iterable[WarningSign] = (),
) -> CheckIn:
    """The check-in `offset` days after the opening."""
    return CheckIn(
        DAY0 + timedelta(days=offset),
        None if during is None else pain(during),
        pain(morning),
        frozenset(signs),
    )


def good_days(
    first: int, count: int, *, active: bool = True, morning: int = 0, during: int = 1
) -> list[CheckIn]:
    """`count` consecutive good check-ins from day `first`, all with an activity (with this pain
    during it) or none."""
    return [checkin(first + i, during if active else None, morning) for i in range(count)]


def state_after(inj: Injury) -> PathState:
    """Where the injury stands."""
    return follow(inj)


def fold(inj: Injury, extra: Iterable[CheckIn]) -> PathState:
    """The state after more check-ins than the injury has (without storing them)."""
    state = follow(inj)
    for item in extra:
        state = advance(state, item, inj.questionnaire)
    return state


def climb(inj: Injury, target: Step, *, start: int = 1, limit: int = 120) -> list[CheckIn]:
    """Perfect days (no pain at all, an activity every day) from day `start` until the injury
    stands on `target`; the days are returned, not stored."""
    days: list[CheckIn] = []
    state = follow(inj)
    for offset in range(start, start + limit):
        if state.step >= target:
            return days
        item = checkin(offset, 0, 0)
        days.append(item)
        state = advance(state, item, inj.questionnaire)
    raise AssertionError("the target step was not reached")
