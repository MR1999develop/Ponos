# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Presentation model and texts of the Garmin calendar, without a window."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta

import pytest

from ponos_coach.injury.domain import HaltReason
from ponos_coach.planning.calendar_use_cases import (
    Failure,
    FailureReason,
    SendOutcome,
    SessionOfDay,
    StopReason,
    WeekOfPlan,
    WholePlan,
)
from ponos_coach.planning.domain import PlanHalted
from ponos_coach.planning.domain.workouts import (
    WORKOUT_PREFIX,
    ScheduledWorkout,
    planned_workouts,
)
from ponos_coach.ui.garmin_calendar_model import (
    MAX_SELECTED_FOR_DELETION,
    CalendarInputs,
    CalendarNotStarted,
    CalendarProblem,
    CalendarReason,
    ScopeChoice,
    Sent,
    commands_for,
    failure_counts,
    needs_attention,
    needs_reconnect,
    plan_is_halted,
    scope_of,
    sessions_in,
    too_many_selected,
    unplanned_app_workouts,
    week_start,
)
from ponos_coach.ui.garmin_calendar_text import headline_text, reason_text, summary_of
from ponos_coach.ui.sync_model import DisabledReason
from tests.ui.plan_support import PLAN_TODAY, plan_for

TODAY = PLAN_TODAY  # a Monday


def _inputs(
    *,
    link: DisabledReason | None = None,
    supported: bool = True,
    with_plan: bool = True,
    selected: date | None = None,
) -> CalendarInputs:
    return CalendarInputs(link, supported, plan_for() if with_plan else None, TODAY, selected)


def test_a_week_starts_on_monday() -> None:
    assert week_start(date(2026, 3, 8)) == date(2026, 3, 2)  # a Sunday
    assert week_start(date(2026, 3, 2)) == date(2026, 3, 2)


def test_scopes_follow_the_selected_day() -> None:
    day = date(2026, 3, 5)

    assert scope_of(ScopeChoice.WHOLE_PLAN, None) == WholePlan()
    assert scope_of(ScopeChoice.WEEK, day) == WeekOfPlan(date(2026, 3, 2))
    assert scope_of(ScopeChoice.SESSION, day) == SessionOfDay(day)
    assert scope_of(ScopeChoice.WEEK, None) is None
    assert scope_of(ScopeChoice.SESSION, None) is None


def test_sessions_in_a_scope_count_only_from_today_on() -> None:
    plan = plan_for()
    first_week = sessions_in(plan, WeekOfPlan(TODAY), TODAY)
    past_week = sessions_in(plan, WeekOfPlan(TODAY - timedelta(weeks=1)), TODAY)

    assert 0 < first_week <= 7
    assert past_week == 0
    assert sessions_in(plan, WholePlan(), TODAY) > first_week
    assert sessions_in(plan, SessionOfDay(TODAY - timedelta(days=1)), TODAY) == 0


def test_every_command_is_available_with_a_connected_account_and_a_plan() -> None:
    commands = commands_for(_inputs(selected=TODAY + timedelta(days=2)))

    assert commands.send_plan.enabled
    assert commands.send_week.enabled
    assert commands.send_session.enabled
    assert commands.browse.enabled
    assert commands.recreate.enabled


@pytest.mark.parametrize(
    ("link", "reason"),
    [
        (DisabledReason.NO_PROFILE, CalendarReason.NO_PROFILE),
        (DisabledReason.NOT_LINKED, CalendarReason.NOT_LINKED),
        (DisabledReason.NEEDS_RECONNECT, CalendarReason.NEEDS_RECONNECT),
        (DisabledReason.KEYCHAIN_UNAVAILABLE, CalendarReason.KEYCHAIN_UNAVAILABLE),
        (DisabledReason.CHECKING, CalendarReason.CHECKING),
        (DisabledReason.BUSY, CalendarReason.BUSY),
    ],
)
def test_a_connection_problem_turns_every_command_off_with_its_reason(
    link: DisabledReason, reason: CalendarReason
) -> None:
    commands = commands_for(_inputs(link=link, selected=TODAY))

    for availability in (
        commands.send_plan,
        commands.send_week,
        commands.send_session,
        commands.browse,
        commands.recreate,
    ):
        assert availability.reason is reason


def test_an_adapter_that_cannot_do_workouts_turns_every_command_off() -> None:
    commands = commands_for(_inputs(supported=False, selected=TODAY))

    assert commands.browse.reason is CalendarReason.UNSUPPORTED
    assert commands.send_plan.reason is CalendarReason.UNSUPPORTED


def test_sending_needs_a_plan_and_the_day_commands_need_a_day_with_a_session() -> None:
    no_plan = commands_for(_inputs(with_plan=False, selected=TODAY))
    no_day = commands_for(_inputs())
    past = commands_for(_inputs(selected=TODAY - timedelta(days=3)))

    assert no_plan.send_plan.reason is CalendarReason.NO_PLAN
    assert no_plan.browse.enabled  # listing needs no plan
    assert no_day.send_plan.enabled
    assert no_day.send_week.reason is CalendarReason.NO_DAY_SELECTED
    assert no_day.send_session.reason is CalendarReason.NO_DAY_SELECTED
    assert past.send_session.reason is CalendarReason.DAY_PASSED
    assert past.send_week.reason is CalendarReason.NO_SESSIONS_THAT_WEEK


def test_every_reason_has_a_text() -> None:
    for reason in CalendarReason:
        assert reason_text(reason)


def test_the_selection_limit_matches_one_deletion() -> None:
    assert not too_many_selected(MAX_SELECTED_FOR_DELETION)
    assert too_many_selected(MAX_SELECTED_FOR_DELETION + 1)


def test_failures_are_counted_by_cause_most_frequent_first() -> None:
    day = date(2026, 3, 3)
    failures = [
        Failure(day, "a", FailureReason.TOO_COMPLEX),
        Failure(day, "b", FailureReason.REJECTED),
        Failure(day, "c", FailureReason.REJECTED),
    ]

    assert failure_counts(failures) == (
        (FailureReason.REJECTED, 2),
        (FailureReason.TOO_COMPLEX, 1),
    )


def test_a_send_that_stopped_or_left_something_out_needs_attention() -> None:
    clean = Sent(SendOutcome((), (), (), 0, StopReason.COMPLETED))
    cancelled = Sent(SendOutcome((), (), (), 0, StopReason.CANCELLED))
    limited = Sent(SendOutcome((), (), (), 0, StopReason.RATE_LIMITED))
    left_out = Sent(
        SendOutcome((), (), (Failure(TODAY, "x", FailureReason.REJECTED),), 0, StopReason.COMPLETED)
    )

    assert not needs_attention(clean)
    assert not needs_attention(cancelled)
    assert needs_attention(limited)
    assert needs_attention(left_out)
    assert needs_attention(CalendarNotStarted(CalendarProblem.NOT_LINKED))


def test_only_an_authentication_problem_asks_to_connect_again() -> None:
    expired = Sent(SendOutcome((), (), (), 0, StopReason.AUTHENTICATION_REQUIRED))

    assert needs_reconnect(expired)
    assert needs_reconnect(CalendarNotStarted(CalendarProblem.AUTH_REQUIRED))
    assert not needs_reconnect(CalendarNotStarted(CalendarProblem.RATE_LIMITED))


def test_the_texts_of_a_job_that_did_nothing_do_not_claim_success() -> None:
    nothing = Sent(SendOutcome((), (), (), 0, StopReason.COMPLETED))

    summary = summary_of(nothing)

    assert summary is not None
    assert summary.lines == ("There was nothing to send.",)
    assert headline_text(nothing) == "Workouts on the Garmin calendar: 0"


def test_every_problem_that_stops_a_job_has_a_summary_with_a_remedy() -> None:
    for problem in CalendarProblem:
        summary = summary_of(CalendarNotStarted(problem))
        assert summary is not None
        assert len(summary.lines) == 2  # what happened and what to do


def test_only_the_apps_workouts_of_days_the_plan_no_longer_has_are_offered_for_deletion() -> None:
    plan = plan_for()
    (day, name), *_ = sorted(planned_workouts(plan.days))
    still = ScheduledWorkout(1, 11, name, day)
    moved = ScheduledWorkout(2, 12, name, day + timedelta(days=400))
    renamed = ScheduledWorkout(3, 13, f"{WORKOUT_PREFIX}Other", day)
    athletes = ScheduledWorkout(4, 14, "Tempo run", day)
    past = ScheduledWorkout(5, 15, f"{WORKOUT_PREFIX}Past", TODAY - timedelta(days=1))
    listed = [still, moved, renamed, athletes, past]

    assert unplanned_app_workouts(plan, listed, TODAY) == (moved, renamed)
    assert unplanned_app_workouts(None, listed, TODAY) == (still, moved, renamed)


def test_a_halted_plan_is_told_from_a_changed_one() -> None:
    plan = plan_for()
    halted = replace(plan, warnings=(*plan.warnings, PlanHalted(next(iter(HaltReason)), ())))

    assert plan_is_halted(halted) and not plan_is_halted(plan) and not plan_is_halted(None)
