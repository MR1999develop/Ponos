# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Which key sessions of the plan went by undone.

The engine has no memory, so the caller tells it which long and intense sessions were planned and
not done (user's request, 2026-11-12: a missed session revises the plan): this is how that is
worked out from the days the plan had and the load really done on each day.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import date

from ponos_coach.goals.domain import INTENSE_TYPES, SessionType
from ponos_coach.planning.domain import defaults
from ponos_coach.planning.domain.inputs import MissedSession
from ponos_coach.planning.domain.plan import PlanDay

# The sessions worth making up: the others only change the load, which the PMC already sees.
KEY_TYPES = INTENSE_TYPES | {SessionType.LONG}


def missed_key_sessions(
    planned: Iterable[PlanDay], done_daily_load: Mapping[date, float], today: date
) -> tuple[MissedSession, ...]:
    """The long and intense sessions planned before `today` and not done, by day.

    A session is not done when the load of its day is below `DONE_LOAD_SHARE_PERCENT` of the
    session's load (whatever the sport of what was done). The threshold test is left out: it has
    its own rule (`missed_threshold_tests`); so are the days of `today` and later (a session of
    today may still be done). A day given twice counts once.
    """
    missed: dict[date, MissedSession] = {}
    for item in planned:
        session = item.session
        if (
            session is None
            or item.day >= today
            or session.session_type not in KEY_TYPES
            or session.threshold_test is not None
        ):
            continue
        done = done_daily_load.get(item.day, 0.0)
        if done * 100 < session.load * defaults.DONE_LOAD_SHARE_PERCENT:
            missed[item.day] = MissedSession(item.day, session.session_type)
    return tuple(missed[day] for day in sorted(missed))
