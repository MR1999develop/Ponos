# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""The long session is the longest of its week: easy and recovery sessions stay under a share of it,
medium and alternative ones are shorter, whatever the budget."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from ponos_coach.activities.domain import Sport
from ponos_coach.goals.domain import SessionType
from ponos_coach.planning.domain import defaults
from ponos_coach.planning.domain.phases import Phase
from ponos_coach.planning.domain.sessions import rates_by_sport
from ponos_coach.planning.domain.week import DayInfo, Placed, Sizer, WeekSetup, plan_week
from tests.planning.builders import TODAY, physiology

_SHARE = defaults.OTHER_SESSION_MAX_SHARE_OF_LONG_PERCENT


def _sizer() -> Sizer:
    rates = rates_by_sport(physiology(), [Sport.RUNNING])[Sport.RUNNING]
    return Sizer(dict.fromkeys(SessionType, rates))


def _week(loaded: int, tapering: int, chosen: set[SessionType]) -> WeekSetup:
    """`loaded` base days ending on Sunday (the long day) after `tapering` taper days."""
    phases = [Phase.TAPER] * tapering + [Phase.BASE] * loaded
    days = tuple(
        DayInfo(TODAY + timedelta(days=i), phase, defaults.DAY_MAX_MINUTES, False, 0.6)
        for i, phase in enumerate(phases)
    )
    return WeekSetup(days, Phase.BASE, False, 6, frozenset(chosen), frozenset())


def _minutes(placed: dict[date, Placed], kinds: set[SessionType]) -> list[int]:
    return [p.shape.minutes for p in placed.values() if p.shape.session_type in kinds]


@pytest.mark.parametrize("taper_budget", [100.0, 300.0, 800.0])
@pytest.mark.parametrize("loaded_budget", [80.0, 150.0, 400.0])
def test_easy_sessions_stay_under_the_long_one_also_with_a_taper_in_the_week(
    loaded_budget: float, taper_budget: float
) -> None:
    chosen = {SessionType.LONG, SessionType.EASY, SessionType.ALTERNATIVE}
    placed = plan_week(_week(2, 5, chosen), _sizer(), loaded_budget, taper_budget)

    (long_minutes,) = _minutes(placed, {SessionType.LONG})
    others = _minutes(placed, {SessionType.EASY, SessionType.ALTERNATIVE})
    assert others
    assert max(others) < long_minutes
    easy = _minutes(placed, {SessionType.EASY})
    assert all(m * 100 <= _SHARE * long_minutes for m in easy)
