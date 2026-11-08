# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Phase 7 use cases: send the plan to Garmin, list, delete with a copy, create again.

T-SYNC-03 and T-SYNC-04 apply to the calendar too (429, transient errors, authentication,
cancellation); T-SEC-10: a deletion touches only the chosen workouts, after saving a copy.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, timedelta

import pytest

from ponos_coach.activities.domain import SyncPolicy
from ponos_coach.activities.ports import (
    AuthenticationRequiredError,
    DefinitiveSourceError,
    RateLimitedError,
    TransientSourceError,
)
from ponos_coach.goals.domain import Priority
from ponos_coach.planning import calendar_use_cases
from ponos_coach.planning.calendar_use_cases import (
    CalendarPorts,
    DeleteScheduledWorkouts,
    FailureReason,
    ListScheduledWorkouts,
    RecreateDeletedWorkouts,
    SendPlanToGarmin,
    SessionOfDay,
    StopReason,
    WeekOfPlan,
    WholePlan,
)
from ponos_coach.planning.domain import (
    WORKOUT_PREFIX,
    Plan,
    ScheduledWorkout,
    WorkoutSpec,
    build_plan,
)
from ponos_coach.planning.workout_calendar import (
    CalendarUnsupportedError,
    NewBackup,
    StoredBackup,
    WorkoutBackupStoreError,
)
from tests.fakes.workout_calendar import InMemoryWorkoutBackupStore, InMemoryWorkoutCalendar
from tests.fixtures.generators.sync_harness import FakeSyncClock, FakeWait, MidpointRandom
from tests.planning.builders import TODAY, inputs, race

POLICY = SyncPolicy(min_pause_s=1.0, max_jitter_s=0.5, backoff_base_s=5.0, backoff_cap_s=60.0)


class Rig:
    """The use cases on in-memory fakes with a fake clock that does not sleep."""

    def __init__(self, calendar: InMemoryWorkoutCalendar | None = None) -> None:
        self.calendar = calendar or InMemoryWorkoutCalendar()
        self.backups = InMemoryWorkoutBackupStore()
        self.clock = FakeSyncClock()
        self.wait = FakeWait(self.clock)
        self.ports = CalendarPorts(
            self.calendar, self.backups, self.clock, self.wait, MidpointRandom(), POLICY
        )

    @property
    def send(self) -> SendPlanToGarmin:
        return SendPlanToGarmin(self.ports)

    @property
    def delete(self) -> DeleteScheduledWorkouts:
        return DeleteScheduledWorkouts(self.ports)

    @property
    def recreate(self) -> RecreateDeletedWorkouts:
        return RecreateDeletedWorkouts(self.ports)

    @property
    def listing(self) -> ListScheduledWorkouts:
        return ListScheduledWorkouts(self.ports)


def a_plan(race_in_days: int = 28) -> Plan:
    built = build_plan(inputs([race("a", TODAY + timedelta(days=race_in_days), Priority.A)]), TODAY)
    assert isinstance(built, Plan)
    return built


def session_days(plan: Plan) -> list[date]:
    return [d.day for d in plan.days if d.session is not None]


def far(plan: Plan) -> date:
    return plan.days[-1].day + timedelta(days=1)


# --- Sending -------------------------------------------------------------------------------------
def test_the_whole_plan_becomes_one_workout_per_session_with_the_prefix() -> None:
    rig, plan = Rig(), a_plan()

    outcome = rig.send(plan, WholePlan(), TODAY)

    assert outcome.stop is StopReason.COMPLETED
    assert [w.day for w in outcome.sent] == session_days(plan)
    assert all(w.name.startswith(WORKOUT_PREFIX) for w in outcome.sent)
    assert outcome.failed == () and outcome.already_there == () and outcome.deferred == 0
    assert rig.calendar.scheduled() == sorted(outcome.sent, key=lambda w: (w.day, w.name))


def test_a_week_sends_only_the_sessions_of_that_week() -> None:
    rig, plan = Rig(), a_plan()
    week = plan.weeks[1].week_start

    outcome = rig.send(plan, WeekOfPlan(week), TODAY)

    expected = [d for d in session_days(plan) if week <= d < week + timedelta(days=7)]
    assert expected
    assert [w.day for w in outcome.sent] == expected


def test_a_single_session_sends_only_that_day() -> None:
    rig, plan = Rig(), a_plan()
    day = session_days(plan)[3]

    outcome = rig.send(plan, SessionOfDay(day), TODAY)

    assert [w.day for w in outcome.sent] == [day]
    assert len(rig.calendar.scheduled()) == 1


def test_a_day_without_a_session_sends_nothing_and_asks_garmin_nothing() -> None:
    rig, plan = Rig(), a_plan()
    rest_day = next(d.day for d in plan.days if d.session is None)

    outcome = rig.send(plan, SessionOfDay(rest_day), TODAY)

    assert outcome.sent == () and outcome.stop is StopReason.COMPLETED
    assert rig.calendar.calls == []


def test_days_before_today_are_never_sent() -> None:
    rig, plan = Rig(), a_plan()
    today = session_days(plan)[4]

    outcome = rig.send(plan, WholePlan(), today)

    assert [w.day for w in outcome.sent] == [d for d in session_days(plan) if d >= today]


def test_sending_again_does_not_duplicate_what_is_on_the_calendar() -> None:
    rig, plan = Rig(), a_plan()
    first = rig.send(plan, WholePlan(), TODAY)

    second = rig.send(plan, WholePlan(), TODAY)

    assert second.sent == () and second.stop is StopReason.COMPLETED
    assert {w.schedule_id for w in second.already_there} == {w.schedule_id for w in first.sent}
    assert len(rig.calendar.scheduled()) == len(first.sent)


def test_a_workout_of_the_athlete_on_the_same_day_does_not_count_as_already_sent() -> None:
    rig, plan = Rig(), a_plan()
    day = session_days(plan)[0]
    rig.calendar.add_foreign("Tempo run", day)

    outcome = rig.send(plan, SessionOfDay(day), TODAY)

    assert len(outcome.sent) == 1
    assert len(rig.calendar.scheduled()) == 2


def test_a_send_is_capped_and_says_how_many_sessions_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(calendar_use_cases, "MAX_WORKOUTS_PER_SEND", 3)
    rig, plan = Rig(), a_plan()

    outcome = rig.send(plan, WholePlan(), TODAY)

    assert [w.day for w in outcome.sent] == session_days(plan)[:3]
    assert outcome.deferred == len(session_days(plan)) - 3


def test_a_pause_comes_before_each_request_except_the_first() -> None:
    rig, plan = Rig(), a_plan()

    outcome = rig.send(plan, SessionOfDay(session_days(plan)[0]), TODAY)

    # the look at the calendar, then the creation: one pause between the two requests
    assert len(rig.wait.waits) == 1
    assert POLICY.min_pause_s <= rig.wait.waits[0] <= POLICY.min_pause_s + POLICY.max_jitter_s
    assert len(outcome.sent) == 1


# --- Errors and cancellation ---------------------------------------------------------------------
def test_authentication_required_stops_the_run_and_keeps_what_was_sent() -> None:
    rig, plan = Rig(), a_plan()
    rig.calendar.fail("create_and_schedule", AuthenticationRequiredError("x"))

    outcome = rig.send(plan, WholePlan(), TODAY)

    assert outcome.stop is StopReason.AUTHENTICATION_REQUIRED
    assert outcome.sent == ()


def test_a_run_that_loses_the_session_midway_reports_what_it_did() -> None:
    calendar = InMemoryWorkoutCalendar()
    rig, plan = Rig(calendar), a_plan()
    calendar.fail("create_and_schedule", AuthenticationRequiredError("x"))
    first = rig.send(plan, SessionOfDay(session_days(plan)[0]), TODAY)
    assert first.stop is StopReason.AUTHENTICATION_REQUIRED

    # the same calendar, after the user logs in again: everything goes through
    second = rig.send(plan, WholePlan(), TODAY)

    assert second.stop is StopReason.COMPLETED
    assert len(second.sent) == len(session_days(plan))


def test_a_429_is_waited_out_with_backoff_and_then_the_workout_is_created() -> None:
    rig, plan = Rig(), a_plan()
    rig.calendar.fail("create_and_schedule", RateLimitedError(20.0))

    outcome = rig.send(plan, SessionOfDay(session_days(plan)[0]), TODAY)

    assert len(outcome.sent) == 1 and outcome.stop is StopReason.COMPLETED
    assert max(rig.wait.waits) >= 20.0  # Garmin's Retry-After is respected


def test_a_429_that_does_not_stop_ends_the_run_after_the_attempts_of_the_policy() -> None:
    rig, plan = Rig(), a_plan()
    rig.calendar.fail("create_and_schedule", RateLimitedError(), times=100)

    outcome = rig.send(plan, WholePlan(), TODAY)

    assert outcome.stop is StopReason.RATE_LIMITED
    assert rig.calendar.calls.count("create_and_schedule") == POLICY.max_attempts_per_request
    assert max(rig.wait.waits) <= POLICY.backoff_cap_s


def test_a_transient_error_is_retried_and_does_not_duplicate_the_workout() -> None:
    rig, plan = Rig(), a_plan()
    rig.calendar.fail("create_and_schedule", TransientSourceError("x"), times=2)

    outcome = rig.send(plan, SessionOfDay(session_days(plan)[0]), TODAY)

    assert len(outcome.sent) == 1
    assert len(rig.calendar.scheduled()) == 1


class _AnswerLostCalendar(InMemoryWorkoutCalendar):
    """Creates the workout and then fails, as when the answer is lost on the way."""

    def __init__(self) -> None:
        super().__init__()
        self.lose_next = True

    def create_and_schedule(self, spec: WorkoutSpec, day: date) -> ScheduledWorkout:
        created = super().create_and_schedule(spec, day)
        if self.lose_next:
            self.lose_next = False
            raise TransientSourceError("answer lost")
        return created


def test_a_lost_answer_is_recovered_by_looking_for_the_workout_not_by_creating_it_again() -> None:
    calendar = _AnswerLostCalendar()
    rig, plan = Rig(calendar), a_plan()

    outcome = rig.send(plan, SessionOfDay(session_days(plan)[0]), TODAY)

    assert len(outcome.sent) == 1
    assert len(calendar.scheduled()) == 1  # the retry found it: no second workout
    assert calendar.calls.count("create_and_schedule") == 1


def test_a_workout_garmin_refuses_fails_alone_and_the_run_goes_on() -> None:
    rig, plan = Rig(), a_plan()
    rig.calendar.fail("create_and_schedule", DefinitiveSourceError("x"))

    outcome = rig.send(plan, WholePlan(), TODAY)

    assert [f.reason for f in outcome.failed] == [FailureReason.REJECTED]
    assert outcome.failed[0].day == session_days(plan)[0]
    assert len(outcome.sent) == len(session_days(plan)) - 1
    assert outcome.stop is StopReason.COMPLETED


def test_an_adapter_that_cannot_do_it_stops_the_run_with_a_clear_reason() -> None:
    rig, plan = Rig(), a_plan()
    rig.calendar.fail("create_and_schedule", CalendarUnsupportedError("x"))

    outcome = rig.send(plan, WholePlan(), TODAY)

    assert outcome.stop is StopReason.UNSUPPORTED and outcome.sent == ()


def test_the_first_look_at_the_calendar_failing_for_good_sends_nothing() -> None:
    rig, plan = Rig(), a_plan()
    rig.calendar.fail("list_scheduled", DefinitiveSourceError("x"))

    outcome = rig.send(plan, WholePlan(), TODAY)

    assert outcome.stop is StopReason.UNAVAILABLE and outcome.sent == ()
    assert "create_and_schedule" not in rig.calendar.calls


def test_cancelling_during_a_pause_stops_within_that_pause_and_keeps_the_sent_ones() -> None:
    rig, plan = Rig(), a_plan()
    rig.wait.cancel_on_wait(3)  # list, create 1 | pause, create 2 | pause <- cancelled

    outcome = rig.send(plan, WholePlan(), TODAY)

    assert outcome.stop is StopReason.CANCELLED
    assert len(outcome.sent) == 2
    assert len(rig.calendar.scheduled()) == 2


def test_cancelling_before_the_first_request_makes_no_request() -> None:
    rig, plan = Rig(), a_plan()

    outcome = rig.send(plan, WholePlan(), TODAY, is_cancelled=lambda: True)

    assert outcome.stop is StopReason.CANCELLED
    assert rig.calendar.calls == []


# --- Listing -------------------------------------------------------------------------------------
def test_the_list_has_the_workouts_of_the_athlete_and_of_the_app() -> None:
    rig, plan = Rig(), a_plan()
    rig.send(plan, SessionOfDay(session_days(plan)[0]), TODAY)
    rig.calendar.add_foreign("Tempo run", TODAY + timedelta(days=1))

    found = rig.listing(TODAY, far(plan))

    assert found.stop is StopReason.COMPLETED
    assert sorted(w.is_app_created for w in found.workouts) == [False, True]


def test_a_list_that_cannot_be_read_says_why_instead_of_raising() -> None:
    rig = Rig()
    rig.calendar.fail("list_scheduled", AuthenticationRequiredError("x"))

    outcome = rig.listing(TODAY, TODAY + timedelta(days=30))

    assert outcome.stop is StopReason.AUTHENTICATION_REQUIRED and outcome.workouts == ()


def test_a_long_range_is_listed_in_several_requests() -> None:
    rig = Rig()

    outcome = rig.listing(TODAY, TODAY + timedelta(days=900))

    assert outcome.stop is StopReason.COMPLETED
    assert rig.calendar.calls.count("list_scheduled") == 3


def test_an_inverted_range_is_a_value_error() -> None:
    with pytest.raises(ValueError, match="inverted"):
        Rig().listing(TODAY, TODAY - timedelta(days=1))


# --- Deleting (T-SEC-10) -------------------------------------------------------------------------
class _SpyingBackups(InMemoryWorkoutBackupStore):
    """Records what was still on the calendar when each copy was saved."""

    def __init__(self, calendar: InMemoryWorkoutCalendar) -> None:
        super().__init__()
        self._calendar = calendar
        self.on_calendar_at_save: list[bool] = []

    def add(self, backup: NewBackup) -> StoredBackup:
        stored = super().add(backup)
        names = [w.name for w in self._calendar.scheduled() if w.day == backup.day]
        self.on_calendar_at_save.append(backup.name in names)
        return stored


def test_the_copy_is_saved_before_the_workout_is_taken_off_the_calendar() -> None:
    calendar = InMemoryWorkoutCalendar()
    rig, plan = Rig(calendar), a_plan()
    spy = _SpyingBackups(calendar)
    rig.ports = CalendarPorts(calendar, spy, rig.clock, rig.wait, MidpointRandom(), POLICY)
    sent = rig.send(plan, SessionOfDay(session_days(plan)[0]), TODAY).sent

    outcome = rig.delete(sent, TODAY)

    assert outcome.deleted == sent and outcome.stop is StopReason.COMPLETED
    assert spy.on_calendar_at_save == [True]
    assert calendar.scheduled() == []


def test_only_the_chosen_workouts_are_deleted() -> None:
    rig, plan = Rig(), a_plan()
    sent = rig.send(plan, WholePlan(), TODAY).sent
    chosen = [sent[1], sent[4]]

    outcome = rig.delete(chosen, TODAY)

    assert list(outcome.deleted) == chosen
    assert {w.schedule_id for w in rig.calendar.scheduled()} == {
        w.schedule_id for w in sent if w not in chosen
    }
    assert [b.name for b in rig.backups.pending()] == [w.name for w in chosen]


def test_a_workout_of_the_athlete_leaves_the_calendar_but_stays_in_the_library() -> None:
    rig = Rig()
    foreign = rig.calendar.add_foreign("Tempo run", TODAY + timedelta(days=3))
    mine = rig.send(a_plan(), SessionOfDay(session_days(a_plan())[0]), TODAY).sent[0]

    rig.delete([foreign, mine], TODAY)

    assert foreign.workout_id in rig.calendar.library_ids()
    assert mine.workout_id not in rig.calendar.library_ids()


def test_a_workout_of_a_past_day_is_not_touched() -> None:
    rig = Rig()
    past = rig.calendar.add_foreign("Old run", TODAY - timedelta(days=1))

    outcome = rig.delete([past], TODAY)

    assert [f.reason for f in outcome.failed] == [FailureReason.NOT_FUTURE]
    assert rig.calendar.scheduled() == [past]
    assert rig.backups.pending() == []


def test_a_workout_whose_definition_cannot_be_copied_is_not_deleted() -> None:
    rig = Rig()
    item = rig.calendar.add_foreign("Tempo run", TODAY + timedelta(days=1))
    rig.calendar.fail("export_definition", DefinitiveSourceError("x"))

    outcome = rig.delete([item], TODAY)

    assert [f.reason for f in outcome.failed] == [FailureReason.NO_COPY]
    assert rig.calendar.scheduled() == [item]


def test_if_the_copy_cannot_be_saved_nothing_is_deleted_and_the_run_stops() -> None:
    rig = Rig()
    items = [rig.calendar.add_foreign(f"Run {n}", TODAY + timedelta(days=n)) for n in (1, 2)]
    rig.backups.fail_on_add = WorkoutBackupStoreError("disk full")

    outcome = rig.delete(items, TODAY)

    assert outcome.stop is StopReason.BACKUP_FAILED and outcome.deleted == ()
    assert rig.calendar.scheduled() == items


def test_a_deletion_that_loses_the_session_keeps_the_copies_of_what_it_did_not_finish() -> None:
    rig = Rig()
    items = [rig.calendar.add_foreign(f"Run {n}", TODAY + timedelta(days=n)) for n in (1, 2)]
    rig.calendar.fail("remove", AuthenticationRequiredError("x"))

    outcome = rig.delete(items, TODAY)

    assert outcome.stop is StopReason.AUTHENTICATION_REQUIRED and outcome.deleted == ()
    assert rig.calendar.scheduled() == items  # still there: the copy is only a safety net


def test_a_deletion_of_too_many_workouts_at_once_is_refused() -> None:
    rig = Rig()
    item = ScheduledWorkout(1, 2, "x", TODAY)

    with pytest.raises(ValueError, match="too many"):
        rig.delete([item] * (calendar_use_cases.MAX_DELETIONS_PER_CALL + 1), TODAY)


# --- Creating again ------------------------------------------------------------------------------
def test_a_deleted_workout_is_created_again_on_its_day_and_the_copy_is_done() -> None:
    rig, plan = Rig(), a_plan()
    sent = rig.send(plan, SessionOfDay(session_days(plan)[0]), TODAY).sent
    rig.delete(sent, TODAY)

    outcome = rig.recreate(TODAY)

    assert outcome.stop is StopReason.COMPLETED
    assert [(w.name, w.day) for w in outcome.restored] == [(sent[0].name, sent[0].day)]
    assert [(w.name, w.day) for w in rig.calendar.scheduled()] == [(sent[0].name, sent[0].day)]
    assert rig.backups.pending() == []


def test_creating_again_twice_does_not_duplicate() -> None:
    rig = Rig()
    item = rig.calendar.add_foreign("Tempo run", TODAY + timedelta(days=2))
    rig.delete([item], TODAY)
    rig.recreate(TODAY)

    second = rig.recreate(TODAY)

    assert second.restored == () and second.already_there == ()
    assert len(rig.calendar.scheduled()) == 1


def test_a_copy_whose_workout_is_already_on_its_day_is_marked_done_without_creating() -> None:
    rig = Rig()
    item = rig.calendar.add_foreign("Tempo run", TODAY + timedelta(days=2))
    rig.delete([item], TODAY)
    rig.calendar.add_foreign("Tempo run", item.day)  # the athlete made it again by hand

    outcome = rig.recreate(TODAY)

    assert outcome.restored == () and len(outcome.already_there) == 1
    assert rig.backups.pending() == []
    assert len(rig.calendar.scheduled()) == 1


def test_a_copy_of_a_day_that_has_passed_stays_pending_and_is_reported() -> None:
    rig = Rig()
    item = rig.calendar.add_foreign("Tempo run", TODAY + timedelta(days=2))
    rig.delete([item], TODAY)

    outcome = rig.recreate(TODAY + timedelta(days=5))

    assert [f.reason for f in outcome.failed] == [FailureReason.EXPIRED]
    assert len(rig.backups.pending()) == 1 and rig.calendar.scheduled() == []


def test_only_the_copies_chosen_are_created_again() -> None:
    rig = Rig()
    items = [rig.calendar.add_foreign(f"Run {n}", TODAY + timedelta(days=n)) for n in (1, 2, 3)]
    rig.delete(items, TODAY)
    wanted = rig.backups.pending()[1].backup_id

    outcome = rig.recreate(TODAY, only={wanted})

    assert [w.name for w in outcome.restored] == ["Run 2"]
    assert len(rig.backups.pending()) == 2


def test_creating_again_with_the_store_unreadable_says_so() -> None:
    class Broken(InMemoryWorkoutBackupStore):
        def pending(self) -> Sequence[StoredBackup]:
            raise WorkoutBackupStoreError("unreadable")

    rig = Rig()
    rig.ports = CalendarPorts(rig.calendar, Broken(), rig.clock, rig.wait, MidpointRandom(), POLICY)

    assert rig.recreate(TODAY).stop is StopReason.BACKUP_FAILED
