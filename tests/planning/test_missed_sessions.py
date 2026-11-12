# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""A missed session revises the plan (user's request, 2026-11-12): the long and intense sessions
planned earlier in the current week and not done are made up in the days left in it, within the
usual rules; nothing is carried to the next week, and nothing in an unload week."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from ponos_coach.activities.domain import Sport
from ponos_coach.assessment.domain import DEFAULT_PROTOCOL
from ponos_coach.goals.domain import SessionType
from ponos_coach.planning.domain import (
    MissedSession,
    Phase,
    Plan,
    PlanDay,
    PlannedSession,
    build_plan,
    defaults,
    missed_key_sessions,
)
from tests.planning.builders import BIG_WEEK, TODAY, availability, inputs, race
from tests.planning.invariants import violations

# TODAY is a Monday: the plan is rebuilt on the Thursday of the week after, so its first week
# keeps Friday to Sunday. The long day is Wednesday, already gone.
WEDNESDAY = TODAY + timedelta(days=9)
THURSDAY = WEDNESDAY + timedelta(days=1)
LONG_ON_WEDNESDAY = availability(long_day=2, max_week_minutes=1200)
RACE_DAY = TODAY + timedelta(weeks=20)


def _plan(today: date, missed: tuple[MissedSession, ...] = (), **kwargs: object) -> Plan:
    kwargs.setdefault("avail", LONG_ON_WEDNESDAY)
    plan = build_plan(
        inputs([race("a", RACE_DAY)], missed_sessions=missed, **kwargs),  # type: ignore[arg-type]
        today,
    )
    assert isinstance(plan, Plan)
    assert violations(plan) == []
    return plan


def _first_week(plan: Plan) -> list[PlanDay]:
    first = plan.days[0].day
    sunday = first + timedelta(days=6 - first.weekday())
    return [d for d in plan.days if d.day <= sunday]


def _types(days: list[PlanDay]) -> list[SessionType]:
    return [d.session.session_type for d in days if d.session is not None]


# --- Which sessions were missed -----------------------------------------------------------------
def _day(day: date, session_type: SessionType, load: float, *, test: bool = False) -> PlanDay:
    session = PlannedSession(
        sport=Sport.RUNNING,
        session_type=session_type,
        minutes=60,
        blocks=(),
        load=load,
        threshold_test=DEFAULT_PROTOCOL if test else None,
    )
    return PlanDay(day, Phase.BASE, session, None, load)


def test_a_key_session_with_less_than_half_its_load_done_is_missed() -> None:
    planned = [
        _day(WEDNESDAY, SessionType.LONG, 100.0),
        _day(WEDNESDAY - timedelta(days=1), SessionType.THRESHOLD, 80.0),
        _day(WEDNESDAY - timedelta(days=2), SessionType.EASY, 40.0),
    ]
    done = {WEDNESDAY: 49.9, WEDNESDAY - timedelta(days=1): 40.0}

    assert missed_key_sessions(planned, done, THURSDAY) == (
        MissedSession(WEDNESDAY, SessionType.LONG),
    )


def test_today_a_threshold_test_and_an_easy_session_are_never_missed() -> None:
    planned = [
        _day(THURSDAY, SessionType.LONG, 100.0),
        _day(WEDNESDAY, SessionType.THRESHOLD, 80.0, test=True),
        _day(WEDNESDAY - timedelta(days=1), SessionType.EASY, 40.0),
    ]
    assert missed_key_sessions(planned, {}, THURSDAY) == ()


def test_the_same_day_given_twice_counts_once_and_days_come_in_order() -> None:
    long = _day(WEDNESDAY, SessionType.LONG, 100.0)
    vo2 = _day(WEDNESDAY - timedelta(days=2), SessionType.VO2MAX, 60.0)
    assert missed_key_sessions([long, vo2, long], {}, THURSDAY) == (
        MissedSession(vo2.day, SessionType.VO2MAX),
        MissedSession(WEDNESDAY, SessionType.LONG),
    )


# --- Making them up -----------------------------------------------------------------------------
def test_a_missed_long_session_is_made_up_in_the_days_left_in_the_week() -> None:
    without = _first_week(_plan(THURSDAY))
    with_makeup = _first_week(_plan(THURSDAY, (MissedSession(WEDNESDAY, SessionType.LONG),)))

    assert SessionType.LONG not in _types(without)
    assert _types(with_makeup).count(SessionType.LONG) == 1


def test_a_long_session_missed_last_week_is_not_carried_over() -> None:
    sunday_missed = MissedSession(THURSDAY - timedelta(days=11), SessionType.LONG)
    assert _plan(THURSDAY, (sunday_missed,)).days == _plan(THURSDAY).days


def test_a_new_week_starts_clean() -> None:
    sunday = TODAY + timedelta(days=6)  # rebuilt on Sunday: the plan starts on Monday
    missed = (MissedSession(sunday - timedelta(days=4), SessionType.LONG),)
    assert _plan(sunday, missed).days == _plan(sunday).days


def test_a_long_session_not_chosen_is_not_made_up() -> None:
    types = frozenset(SessionType) - {SessionType.LONG}
    missed = (MissedSession(WEDNESDAY, SessionType.LONG),)
    plan = _plan(THURSDAY, missed, types=types)
    assert SessionType.LONG not in _types(_first_week(plan))


def test_when_the_long_day_is_still_ahead_the_long_session_stays_on_it() -> None:
    sunday_long = availability(long_day=6, max_week_minutes=1200)
    missed = (MissedSession(TODAY + timedelta(days=7), SessionType.LONG),)
    plan = _plan(THURSDAY, missed, avail=sunday_long)
    longs = [
        d.day for d in _first_week(plan) if d.session and d.session.session_type is SessionType.LONG
    ]
    assert all(day.weekday() == 6 for day in longs)
    assert plan.days == _plan(THURSDAY, avail=sunday_long).days


def test_a_missed_threshold_session_is_made_up_in_the_days_left() -> None:
    missed = (MissedSession(WEDNESDAY - timedelta(days=1), SessionType.THRESHOLD),)
    without = _types(_first_week(_plan(THURSDAY, avail=BIG_WEEK)))
    with_makeup = _types(_first_week(_plan(THURSDAY, missed, avail=BIG_WEEK)))

    assert SessionType.THRESHOLD not in without
    assert SessionType.THRESHOLD in with_makeup


def test_an_intense_type_the_phase_does_not_use_is_not_made_up() -> None:
    # The base weeks have threshold and medium work, no VO2max.
    missed = (MissedSession(WEDNESDAY - timedelta(days=1), SessionType.VO2MAX),)
    plan = _plan(THURSDAY, missed, avail=BIG_WEEK)
    assert SessionType.VO2MAX not in _types(_first_week(plan))


def test_the_plan_with_missed_sessions_is_the_same_every_time() -> None:
    missed = (
        MissedSession(WEDNESDAY, SessionType.LONG),
        MissedSession(WEDNESDAY - timedelta(days=2), SessionType.THRESHOLD),
    )
    assert _plan(THURSDAY, missed) == _plan(THURSDAY, tuple(reversed(missed)))


def test_two_missed_sessions_on_one_day_are_refused_as_input() -> None:
    with pytest.raises(ValueError, match="one day"):
        inputs(
            missed_sessions=(
                MissedSession(WEDNESDAY, SessionType.LONG),
                MissedSession(WEDNESDAY, SessionType.THRESHOLD),
            )
        )


def test_the_method_version_says_the_plan_changed() -> None:
    assert defaults.METHOD_VERSION == 13
    names = dict(_plan(THURSDAY).parameters)
    assert names["done_load_share_percent"] == defaults.DONE_LOAD_SHARE_PERCENT
