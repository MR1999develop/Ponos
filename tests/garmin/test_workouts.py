# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""`GarminWorkoutCalendar`: errors, promises of the port and the session that carries it."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date

import pytest

from ponos_coach.activities.domain import SessionTokens, Sport
from ponos_coach.activities.ports import (
    AuthenticationRequiredError,
    DefinitiveSourceError,
    RateLimitedError,
    TransientSourceError,
)
from ponos_coach.garmin import GarminConnectAccount, GarminConnectSession, GarminWorkoutCalendar
from ponos_coach.planning.domain import WORKOUT_PREFIX, StepKind, WorkoutSpec, WorkoutStep
from ponos_coach.planning.workout_calendar import CalendarUnsupportedError
from tests.garmin.scripted import (
    PLACEHOLDER_TOKENS,
    ScriptedClient,
    ScriptedFactory,
    auth_error,
    http_error,
    rate_limit_error,
)
from tests.garmin.workout_fakes import FakeWorkoutService

DAY = date(2026, 6, 10)
SPEC = WorkoutSpec(
    f"{WORKOUT_PREFIX}Easy 30 min",
    Sport.RUNNING,
    (WorkoutStep(StepKind.INTERVAL, 1800, 120, 140),),
)


def rig() -> tuple[GarminWorkoutCalendar, FakeWorkoutService]:
    service = FakeWorkoutService()
    return GarminWorkoutCalendar(service), service


# --- Errors of the library become the errors of the source ---------------------------------------
@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (auth_error(), AuthenticationRequiredError),
        (rate_limit_error("30"), RateLimitedError),
        (http_error(500), TransientSourceError),
        (http_error(400), DefinitiveSourceError),
    ],
)
@pytest.mark.parametrize("method", ["upload_workout", "scheduled_between", "get_workout"])
def test_the_errors_of_the_library_are_translated(
    method: str, error: Exception, expected: type[Exception]
) -> None:
    calendar, service = rig()
    service.fail(method, error)
    calls: dict[str, Callable[[], object]] = {
        "upload_workout": lambda: calendar.create_and_schedule(SPEC, DAY),
        "scheduled_between": lambda: calendar.list_scheduled(DAY, DAY),
        "get_workout": lambda: calendar.export_definition(1),
    }

    with pytest.raises(expected) as caught:
        calls[method]()

    assert caught.value.__cause__ is error


def test_a_429_carries_the_wait_garmin_suggests() -> None:
    calendar, service = rig()
    service.fail("upload_workout", rate_limit_error("45"))

    with pytest.raises(RateLimitedError) as caught:
        calendar.create_and_schedule(SPEC, DAY)

    assert caught.value.retry_after_s == 45.0


def test_a_method_the_library_no_longer_has_is_unsupported_not_a_crash() -> None:
    calendar, service = rig()
    service.fail("scheduled_between", AttributeError("no such method"))

    with pytest.raises(CalendarUnsupportedError):
        calendar.list_scheduled(DAY, DAY)


def test_a_response_that_does_not_fit_is_a_definitive_error_without_its_text() -> None:
    class Odd(FakeWorkoutService):
        def upload_workout(self, payload: dict[str, object]) -> object:
            del payload
            return {"workoutId": "SECRET-TEXT"}

    with pytest.raises(DefinitiveSourceError) as caught:
        GarminWorkoutCalendar(Odd()).create_and_schedule(SPEC, DAY)

    assert "SECRET-TEXT" not in str(caught.value)


# --- Promises of the port ------------------------------------------------------------------------
def test_the_request_that_creates_the_workout_carries_the_spec() -> None:
    calendar, service = rig()

    calendar.create_and_schedule(SPEC, DAY)

    (uploaded,) = service.uploaded
    assert uploaded["workoutName"] == SPEC.name
    assert uploaded["estimatedDurationInSecs"] == 1800


def test_a_scheduling_that_fails_leaves_no_new_workout_in_the_library() -> None:
    calendar, service = rig()
    service.fail("schedule_workout", http_error(500))

    with pytest.raises(TransientSourceError):
        calendar.create_and_schedule(SPEC, DAY)

    assert service.library_ids() == set() and service.schedule_ids() == set()


def test_a_scheduling_that_fails_and_cannot_clean_up_still_raises_the_first_error() -> None:
    calendar, service = rig()
    service.fail("schedule_workout", rate_limit_error())
    service.fail("delete_workout", http_error(500))

    with pytest.raises(RateLimitedError):
        calendar.create_and_schedule(SPEC, DAY)


def test_a_removal_checks_the_entry_is_still_the_one_chosen_and_otherwise_removes_nothing() -> None:
    calendar, service = rig()
    created = calendar.create_and_schedule(SPEC, DAY)
    _, other_schedule = service.add_foreign("Tempo run", DAY)
    stale = type(created)(other_schedule, created.workout_id, created.name, DAY)  # wrong pairing

    with pytest.raises(DefinitiveSourceError, match="no longer"):
        calendar.remove(stale, delete_workout=True)

    assert service.schedule_ids() >= {created.schedule_id, other_schedule}
    assert created.workout_id in service.library_ids()


def test_removing_an_entry_already_gone_still_deletes_the_workout_when_asked() -> None:
    calendar, service = rig()
    created = calendar.create_and_schedule(SPEC, DAY)
    service.unschedule_workout(created.schedule_id)  # removed by hand on the Garmin site

    calendar.remove(created, delete_workout=True)

    assert service.library_ids() == set()


def test_removing_without_the_workout_makes_no_request_to_delete_it() -> None:
    calendar, service = rig()
    created = calendar.create_and_schedule(SPEC, DAY)

    calendar.remove(created, delete_workout=False)

    assert "delete_workout" not in service.calls
    assert created.workout_id in service.library_ids()


def test_a_calendar_entry_that_cannot_be_read_is_left_out_of_the_list() -> None:
    calendar, service = rig()
    service.add_foreign("fine", DAY)
    service.add_foreign("bad\nname", DAY)

    assert [w.name for w in calendar.list_scheduled(DAY, DAY)] == ["fine"]


# --- The session carries the calendar ------------------------------------------------------------
def test_a_session_has_the_workout_calendar_when_the_client_can_do_workouts() -> None:
    class Both(ScriptedClient, FakeWorkoutService):
        def __init__(self) -> None:
            ScriptedClient.__init__(self)
            FakeWorkoutService.__init__(self)

    session = GarminConnectAccount(ScriptedFactory(Both())).resume(
        SessionTokens(PLACEHOLDER_TOKENS)
    )

    assert isinstance(session, GarminConnectSession)
    assert isinstance(session.calendar, GarminWorkoutCalendar)


def test_a_session_of_a_client_without_workouts_has_no_calendar_and_still_syncs() -> None:
    session = GarminConnectAccount(ScriptedFactory(ScriptedClient())).resume(
        SessionTokens(PLACEHOLDER_TOKENS)
    )

    assert isinstance(session, GarminConnectSession)
    assert session.calendar is None
    assert session.source is not None
