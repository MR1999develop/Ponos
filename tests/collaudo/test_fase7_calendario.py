# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
# ruff: noqa: ANN201, ANN202, ARG002, RUF001, PLE2502
"""Phase 7 acceptance campaign: the Garmin calendar on the real adapter over a fake Garmin service.

The use cases (`planning.calendar_use_cases`), the real adapter (`GarminWorkoutCalendar` with the
JSON of `workout_json`) and the real SQLite store of the copies are wired together; only the Garmin
client is a fake (no login, no network, no call to the real service). The fake keeps a library and
a calendar and can lose answers, like a flaky connection. The athlete is invented.
"""

from __future__ import annotations

import random
from collections.abc import Callable
from datetime import date, timedelta
from pathlib import Path
from typing import Any, cast

import pytest
from garminconnect import GarminConnectAuthenticationError

from ponos_coach.activities.domain import SyncPolicy
from ponos_coach.garmin.workouts import GarminWorkoutCalendar
from ponos_coach.goals.domain import Priority
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
from ponos_coach.planning.domain import WORKOUT_PREFIX, Plan, ScheduledWorkout, build_plan
from ponos_coach.planning.workout_calendar import MAX_PENDING_BACKUPS, NewBackup
from ponos_coach.storage.sqlite_database import create_database
from ponos_coach.storage.sqlite_workouts import SqliteWorkoutBackupStore
from tests.fixtures.generators.sync_harness import FakeSyncClock, FakeWait, MidpointRandom
from tests.garmin.scripted import http_error, rate_limit_error
from tests.garmin.workout_fakes import FakeWorkoutService
from tests.planning.builders import BIG_WEEK, TODAY, inputs, race

POLICY = SyncPolicy(min_pause_s=1.0, max_jitter_s=0.5, backoff_base_s=5.0, backoff_cap_s=60.0)
LONG_RACE_IN_DAYS = 330


class FlakyService(FakeWorkoutService):
    """The fake Garmin service with a seeded flaky connection and a library that, like the real
    one, drops a workout's calendar entries when the workout is deleted.

    Each call fails with probability `p`: half of the failures happen before the request has any
    effect, half after it (the answer is lost although Garmin did the work).
    """

    def __init__(self, seed: int = 0, p: float = 0.0) -> None:
        super().__init__()
        self._rng = random.Random(seed)
        self.p = p
        self.lost_after_effect = 0

    def _flaky(self, method: str, do: Callable[[], object]) -> object:
        if self._rng.random() < self.p:
            if self._rng.random() < 0.5:
                self.calls.append(method)
                raise http_error(503)
            result = do()
            self.lost_after_effect += 1
            del result  # the effect happened; only the answer is lost
            raise http_error(503)
        return do()

    def upload_workout(self, payload: dict[str, object]) -> object:
        return self._flaky(
            "upload_workout", lambda: super(FlakyService, self).upload_workout(payload)
        )

    def schedule_workout(self, workout_id: int, day_iso: str) -> object:
        return self._flaky(
            "schedule_workout",
            lambda: super(FlakyService, self).schedule_workout(workout_id, day_iso),
        )

    def delete_workout(self, workout_id: int) -> None:
        super().delete_workout(workout_id)
        for schedule_id in [s for s, (w, _) in self._schedule.items() if w == workout_id]:
            del self._schedule[schedule_id]


class Rig:
    """Use cases, adapter, fake service and the real store of the copies."""

    def __init__(self, folder: Path, service: FakeWorkoutService | None = None) -> None:
        self.service = service or FakeWorkoutService()
        self.adapter = GarminWorkoutCalendar(self.service)
        self.database = create_database(folder / "profile.sqlite3", {})
        self.backups = SqliteWorkoutBackupStore(self.database)
        self.clock = FakeSyncClock()
        self.wait = FakeWait(self.clock)
        self.ports = CalendarPorts(
            self.adapter, self.backups, self.clock, self.wait, MidpointRandom(), POLICY
        )
        self.send = SendPlanToGarmin(self.ports)
        self.delete = DeleteScheduledWorkouts(self.ports)
        self.recreate = RecreateDeletedWorkouts(self.ports)
        self.list = ListScheduledWorkouts(self.ports)

    def on_calendar(self, first: date = TODAY, last: date | None = None) -> list[ScheduledWorkout]:
        return list(self.adapter.list_scheduled(first, last or first + timedelta(days=399)))


@pytest.fixture
def rig(tmp_path: Path):  # type: ignore[no-untyped-def]
    built = Rig(tmp_path)
    yield built
    built.database.close()


def recording(
    order: list[str], name: str, original: Callable[..., object]
) -> Callable[..., object]:
    """`original` that first notes its name in `order`."""

    def call(*args: object, **kwargs: object) -> object:
        order.append(name)
        return original(*args, **kwargs)

    return call


def a_plan(race_in_days: int = 28) -> Plan:
    built = build_plan(
        inputs([race("a", TODAY + timedelta(days=race_in_days), Priority.A)], avail=BIG_WEEK),
        TODAY,
    )
    assert isinstance(built, Plan)
    return built


def session_days(plan: Plan) -> list[date]:
    return [d.day for d in plan.days if d.session is not None]


def test_the_flaky_fake_really_loses_answers_after_the_effect(tmp_path: Path) -> None:
    lost = 0
    for seed in range(12):
        service = FlakyService(seed, p=0.25)
        folder = tmp_path / f"s{seed}"
        folder.mkdir()
        built = Rig(folder, service)
        try:
            built.send(a_plan(14), WholePlan(), TODAY)
        finally:
            built.database.close()
        lost += service.lost_after_effect

    assert lost > 0


# --- Sending: idempotent, capped, flaky --------------------------------------------------------
def test_a_send_repeated_three_times_leaves_one_workout_per_session(rig: Rig) -> None:
    plan = a_plan()
    for _ in range(3):
        outcome = rig.send(plan, WholePlan(), TODAY)
        assert outcome.stop is StopReason.COMPLETED

    on_calendar = rig.on_calendar()
    assert sorted(w.day for w in on_calendar) == session_days(plan)
    assert len(rig.service.library_ids()) == len(on_calendar)  # nothing orphaned in the library
    assert len(rig.service.schedule_ids()) == len(on_calendar)


@pytest.mark.parametrize("seed", range(12))
def test_a_flaky_connection_never_duplicates_a_day_and_repeating_the_send_completes_it(
    tmp_path: Path, seed: int
) -> None:
    service = FlakyService(seed, p=0.25)
    rig = Rig(tmp_path, service)
    plan = a_plan(14)
    foreign_day = session_days(plan)[0]
    service.p = 0.0
    service.add_foreign("Athlete's own tempo", foreign_day)
    service.p = 0.25
    try:
        outcome = rig.send(plan, WholePlan(), TODAY)
        attempts = 1
        while outcome.stop is not StopReason.COMPLETED or outcome.failed:
            outcome = rig.send(plan, WholePlan(), TODAY)
            attempts += 1
            assert attempts < 40, "the send never completes"
        service.p = 0.0
        entries = rig.on_calendar()
    finally:
        rig.database.close()

    app_entries = [(w.day, w.name) for w in entries if w.is_app_created]
    assert sorted(day for day, _ in app_entries) == session_days(plan)
    assert len(set(app_entries)) == len(app_entries)  # no day twice
    assert any(w.name == "Athlete's own tempo" for w in entries)  # the athlete's one is untouched


def test_the_send_stops_at_the_first_expiry_of_the_session_and_keeps_what_was_done(
    rig: Rig,
) -> None:
    plan = a_plan()
    first_uploads = 3
    done = {"n": 0}
    original = rig.service.upload_workout

    def upload(payload: dict[str, object]) -> object:
        done["n"] += 1
        if done["n"] == first_uploads + 1:
            raise GarminConnectAuthenticationError("expired")
        return original(payload)

    rig.service.upload_workout = upload  # type: ignore[method-assign]

    outcome = rig.send(plan, WholePlan(), TODAY)

    assert outcome.stop is StopReason.AUTHENTICATION_REQUIRED
    assert len(outcome.sent) == first_uploads
    assert len(rig.on_calendar()) == first_uploads
    rig.service.upload_workout = original  # type: ignore[method-assign]
    again = rig.send(plan, WholePlan(), TODAY)  # after logging in again
    assert again.stop is StopReason.COMPLETED
    assert len(again.already_there) == first_uploads
    assert sorted(w.day for w in rig.on_calendar()) == session_days(plan)


@pytest.mark.parametrize("retry_after", [None, "1", "30", "3600", "-5", "nan", "x"])
def test_a_429_waits_within_the_cap_and_then_stops_without_hammering(
    rig: Rig, retry_after: str | None
) -> None:
    rig.service.fail("scheduled_between", rate_limit_error(retry_after), times=50)

    outcome = rig.send(a_plan(), WholePlan(), TODAY)

    assert outcome.stop is StopReason.RATE_LIMITED
    assert outcome.sent == ()
    assert all(w <= POLICY.backoff_cap_s + 1e-9 for w in rig.wait.waits)
    assert rig.service.calls.count("scheduled_between") <= POLICY.max_attempts_per_request
    assert "upload_workout" not in rig.service.calls


def test_a_whole_plan_of_more_than_60_sessions_is_completed_by_sending_again(rig: Rig) -> None:
    """D-F7-02 (medium): `deferred` promises "left for another send", but the next send chooses
    the same first 60 (already there) and defers the same rest again: sessions 61 and later can
    only be sent one week at a time."""
    plan = a_plan(LONG_RACE_IN_DAYS)
    days = session_days(plan)
    assert len(days) > 60

    first = rig.send(plan, WholePlan(), TODAY)
    second = rig.send(plan, WholePlan(), TODAY)

    assert len(first.sent) == 60 and first.deferred == len(days) - 60
    assert len(second.sent) > 0
    for _ in range(20):
        if rig.send(plan, WholePlan(), TODAY).deferred == 0:
            break
    assert sorted(w.day for w in rig.on_calendar()) == days


def test_the_sessions_beyond_the_cap_can_still_be_sent_week_by_week(rig: Rig) -> None:
    plan = a_plan(LONG_RACE_IN_DAYS)
    days = session_days(plan)
    rig.send(plan, WholePlan(), TODAY)

    for week in plan.weeks:
        rig.send(plan, WeekOfPlan(week.week_start), TODAY)

    assert sorted(w.day for w in rig.on_calendar()) == days


# --- Conversion: session -> workout, durations and heart rates kept -----------------------------
def test_every_uploaded_workout_keeps_the_duration_and_the_heart_rate_ranges_of_its_session(
    rig: Rig,
) -> None:
    plan = a_plan(63)
    rig.send(plan, WholePlan(), TODAY)
    by_name: dict[str, list[Any]] = {}
    for payload in rig.service.uploaded:
        by_name.setdefault(str(payload["workoutName"]), []).append(payload)

    for day in plan.days:
        session = day.session
        if session is None:
            continue
        label = session.session_type.value.replace("_", " ").capitalize()
        name = f"{WORKOUT_PREFIX}{label} {session.minutes} min"
        assert name in by_name
        steps: Any = by_name[name][0]["workoutSegments"][0]["workoutSteps"]
        seconds = sum(s["endConditionValue"] for s in steps)
        assert abs(seconds - session.minutes * 60) <= max(120, 0.1 * session.minutes * 60)
        low = [b.hr_low_bpm for b in session.blocks]
        high = [b.hr_high_bpm for b in session.blocks]
        for step in steps:
            # A running step with a pace leads with the pace; the heart rate is then secondary.
            hr_key = "target"
            if step["targetType"]["workoutTargetTypeKey"] == "pace.zone":
                hr_key = "secondaryTarget"
            assert step[f"{hr_key}Type"]["workoutTargetTypeKey"] == "heart.rate.zone"
            assert min(low) <= step[f"{hr_key}ValueOne"] <= step[f"{hr_key}ValueTwo"] <= max(high)


# --- Listing: hostile answers ---------------------------------------------------------------------
class HostileListing(FakeWorkoutService):
    def __init__(self, entries: object) -> None:
        super().__init__()
        self.entries = entries

    def scheduled_between(self, start_iso: str, end_iso: str) -> object:
        return self.entries


def entry(name: object, day: object = "2026-03-10", schedule: object = 11, workout: object = 22):  # type: ignore[no-untyped-def]
    return {
        "workoutScheduleId": schedule,
        "calendarDate": day,
        "workout": {"workoutId": workout, "workoutName": name},
    }


def listed(entries: object, tmp_path: Path) -> list[ScheduledWorkout]:
    rig = Rig(tmp_path, HostileListing(entries))
    try:
        return rig.on_calendar(date(2026, 3, 1), date(2026, 3, 31))
    finally:
        rig.database.close()


@pytest.mark.parametrize(
    "bad",
    [
        entry("ok", schedule=True),
        entry("ok", schedule=1.0),
        entry("ok", schedule="11"),
        entry("ok", schedule=-1),
        entry("ok", schedule=10**20),
        entry("ok", workout=0),
        entry("", day="2026-03-10"),
        entry("x" * 201),
        entry("line\nbreak"),
        entry("nul\x00"),
        entry("sep "),
        entry("csi\x9b"),
        entry(["not", "text"]),
        entry(None),
        entry("ok", day="2026-02-30"),
        entry("ok", day="٢٠٢٦-٠٣-١٠"),
        entry("ok", day="2026-3-10"),
        entry("ok", day="2026-03-10T00:00:00"),
        entry("ok", day="2026-04-10"),  # outside the asked range
        {"workoutScheduleId": 1},
        "just text",
        None,
        42,
        {"workout": []},
    ],
)
def test_a_hostile_calendar_entry_is_left_out_and_never_repaired(
    bad: object, tmp_path: Path
) -> None:
    good = entry("fine", day="2026-03-12", schedule=33, workout=44)

    found = listed([bad, good], tmp_path)

    assert [w.name for w in found] == ["fine"]


@pytest.mark.parametrize(
    "response", [{"a": 1}, "text", None, 5, [[]] * 2001, {"workout": entry("x")}]
)
def test_a_response_that_is_not_a_calendar_is_a_definitive_error_not_a_crash(
    response: object, tmp_path: Path
) -> None:
    rig = Rig(tmp_path, HostileListing(response))
    try:
        outcome = rig.list(date(2026, 3, 1), date(2026, 3, 31))
    finally:
        rig.database.close()

    assert outcome.workouts == ()
    assert outcome.stop is StopReason.UNAVAILABLE


@pytest.mark.parametrize("name", ["tempo ‮gpj.exe", "⁦hidden⁩", "a\u200bb", "x‪y"])
def test_a_name_with_invisible_or_bidirectional_controls_is_left_out(
    name: str, tmp_path: Path
) -> None:
    """D-F7-03 (low): the name filter drops C0, C1 and U+2028/9 but not the format characters
    that reorder or hide text; the deletion dialog lists the names the user must recognise."""
    assert listed([entry(name)], tmp_path) == []


# --- Deleting: copy first, only the chosen, never the past ---------------------------------------
def test_a_deletion_saves_the_copy_before_it_removes_and_only_what_was_chosen(rig: Rig) -> None:
    plan = a_plan()
    rig.send(plan, WholePlan(), TODAY)
    day = session_days(plan)[2]
    foreign = rig.service.add_foreign("Athlete's own tempo", day)
    on_calendar = rig.on_calendar()
    chosen = [w for w in on_calendar if w.day == day]  # one of the app, one of the athlete
    assert len(chosen) == 2

    order: list[str] = []
    real_add = rig.backups.add

    def spy_add(backup: NewBackup):  # type: ignore[no-untyped-def]
        order.append("copy")
        return real_add(backup)

    rig.backups.add = spy_add  # type: ignore[method-assign]
    for name in ("unschedule_workout", "delete_workout"):
        setattr(rig.service, name, recording(order, name, getattr(rig.service, name)))

    outcome = rig.delete(chosen, TODAY)

    assert outcome.stop is StopReason.COMPLETED and outcome.failed == ()
    assert order.index("copy") < order.index("unschedule_workout")
    remaining = rig.on_calendar()
    assert [w for w in remaining if w.day == day] == []
    assert len(remaining) == len(on_calendar) - 2  # every other day is intact
    assert foreign[0] in rig.service.library_ids()  # the athlete's workout stays in the library
    app_workout = next(w for w in chosen if w.is_app_created)
    assert app_workout.workout_id not in rig.service.library_ids()  # the app's one is gone
    assert len(rig.backups.pending()) == 2


def test_a_deletion_asked_for_the_past_touches_nothing_and_asks_garmin_nothing(rig: Rig) -> None:
    plan = a_plan()
    rig.send(plan, WholePlan(), TODAY)
    before = rig.on_calendar()
    calls_before = len(rig.service.calls)
    yesterday = [
        ScheduledWorkout(5, 6, f"{WORKOUT_PREFIX}Easy 30 min", TODAY - timedelta(days=1)),
        ScheduledWorkout(7, 8, "Athlete's own", TODAY - timedelta(days=400)),
    ]

    outcome = rig.delete(yesterday, TODAY)
    calls_after = len(rig.service.calls)

    assert [f.reason for f in outcome.failed] == [FailureReason.NOT_FUTURE] * 2
    assert outcome.deleted == () and rig.backups.pending() == []
    assert calls_after == calls_before
    assert rig.on_calendar() == before


def test_today_itself_can_be_deleted_and_the_day_before_cannot(rig: Rig) -> None:
    plan = a_plan()
    rig.send(plan, WholePlan(), TODAY)
    today_entry = next(w for w in rig.on_calendar() if w.day >= TODAY)

    inside = rig.delete([today_entry], today_entry.day)
    outside = rig.delete([today_entry], today_entry.day + timedelta(days=1))

    assert inside.deleted == (today_entry,)
    assert [f.reason for f in outside.failed] == [FailureReason.NOT_FUTURE]


def test_a_deletion_of_more_than_the_cap_is_refused_before_anything_happens(rig: Rig) -> None:
    items = [ScheduledWorkout(i, i + 5000, f"{WORKOUT_PREFIX}x", TODAY) for i in range(1, 102)]

    with pytest.raises(ValueError, match="too many"):
        rig.delete(items, TODAY)

    assert rig.service.calls == []


def test_a_copy_that_cannot_be_saved_stops_the_deletion_and_nothing_is_deleted(
    rig: Rig,
) -> None:
    rig.send(a_plan(), WholePlan(), TODAY)
    for i in range(MAX_PENDING_BACKUPS):  # the store is full
        rig.backups.add(NewBackup(f"n{i}", TODAY, "{}", rig.clock.now()))
    before = rig.on_calendar()

    outcome = rig.delete(before[:3], TODAY)

    assert outcome.stop is StopReason.BACKUP_FAILED
    assert outcome.deleted == ()
    assert rig.on_calendar() == before


def test_a_changed_entry_is_not_removed_and_says_so(rig: Rig) -> None:
    rig.send(a_plan(), WholePlan(), TODAY)
    chosen = rig.on_calendar()[0]
    # between the list and the confirmation the athlete moved the workout to another day
    workout_id, _ = rig.service._schedule[chosen.schedule_id]
    rig.service._schedule[chosen.schedule_id] = (
        workout_id,
        (chosen.day + timedelta(days=1)).isoformat(),
    )

    outcome = rig.delete([chosen], TODAY)

    assert [f.reason for f in outcome.failed] == [FailureReason.REJECTED]
    assert chosen.schedule_id in rig.service.schedule_ids()  # still there


def test_a_refused_deletion_leaves_no_copy_to_create_again(rig: Rig) -> None:
    """D-F7-01 (medium): the copy is saved before the removal is tried; when the removal is
    refused (entry changed, Garmin rejects) the copy stays "pending". Creating "the deleted
    workouts" again then makes a workout that was never deleted (a duplicate)."""
    rig.send(a_plan(), WholePlan(), TODAY)
    chosen = rig.on_calendar()[0]
    workout_id, _ = rig.service._schedule[chosen.schedule_id]
    rig.service._schedule[chosen.schedule_id] = (
        workout_id,
        (chosen.day + timedelta(days=1)).isoformat(),
    )
    rig.delete([chosen], TODAY)

    assert rig.backups.pending() == []


# --- Creating again ------------------------------------------------------------------------------
def test_what_was_deleted_comes_back_with_the_same_steps_and_only_once(rig: Rig) -> None:
    plan = a_plan()
    rig.send(plan, WholePlan(), TODAY)
    chosen = [w for w in rig.on_calendar() if w.day <= TODAY + timedelta(days=9)]
    originals: dict[int, Any] = {
        w.workout_id: rig.service.get_workout(w.workout_id) for w in chosen
    }
    rig.delete(chosen, TODAY)
    assert not [w for w in rig.on_calendar() if w in chosen]

    outcome = rig.recreate(TODAY, plan)
    twice = rig.recreate(TODAY, plan)

    assert outcome.stop is StopReason.COMPLETED and outcome.failed == ()
    assert len(outcome.restored) == len(chosen)
    assert twice.restored == () and rig.recreate.pending() == ()
    by_day = {(w.day, w.name) for w in rig.on_calendar()}
    assert {(w.day, w.name) for w in chosen} <= by_day
    for original, restored in zip(
        sorted(chosen, key=lambda w: (w.day, w.name)),
        sorted(outcome.restored, key=lambda w: (w.day, w.name)),
        strict=True,
    ):
        old: dict[str, Any] = dict(originals[original.workout_id])
        new: dict[str, Any] = dict(
            cast("dict[str, Any]", rig.service.get_workout(restored.workout_id))
        )
        for key in ("workoutId", "ownerId"):
            old.pop(key, None)
            new.pop(key, None)
        assert new == old


def test_a_copy_whose_day_has_passed_stays_pending_and_is_reported_expired(rig: Rig) -> None:
    rig.send(a_plan(), SessionOfDay(session_days(a_plan())[0]), TODAY)
    chosen = rig.on_calendar()
    rig.delete(chosen, TODAY)

    outcome = rig.recreate(chosen[0].day + timedelta(days=1), a_plan())

    assert [f.reason for f in outcome.failed] == [FailureReason.EXPIRED]
    assert len(rig.recreate.pending()) == 1 and rig.on_calendar() == []


@pytest.mark.parametrize(
    "payload",
    [
        "not json",
        "[]",
        "42",
        '{"workoutName": ""}',
        '{"workoutName": "' + "x" * 300 + '"}',
        '{"workoutName": "a\\nb"}',
        '{"noName": 1}',
        '{"workoutName": 5}',
    ],
)
def test_a_hostile_copy_is_refused_without_a_request_and_stays_pending(
    rig: Rig, payload: str
) -> None:
    rig.backups.add(NewBackup("hostile", TODAY + timedelta(days=2), payload, rig.clock.now()))
    calls_before = len(rig.service.calls)

    outcome = rig.recreate(TODAY, None)

    assert [f.reason for f in outcome.failed] == [FailureReason.REJECTED]
    assert "upload_workout" not in rig.service.calls[calls_before:]
    assert len(rig.recreate.pending()) == 1


def test_a_copy_altered_in_the_database_is_not_created_again(rig: Rig) -> None:
    rig.backups.add(
        NewBackup("good", TODAY + timedelta(days=2), '{"workoutName": "good"}', rig.clock.now())
    )
    rig.database.connection.execute(
        "UPDATE workout_backup SET payload = ?", ('{"workoutName": "tampered"}',)
    )

    outcome = rig.recreate(TODAY, None)

    assert outcome.restored == () and outcome.failed == ()
    assert "upload_workout" not in rig.service.calls


def test_the_definition_of_a_deleted_workout_is_not_forwarded_with_ids_or_dates(rig: Rig) -> None:
    rig.send(a_plan(), SessionOfDay(session_days(a_plan())[0]), TODAY)
    chosen = rig.on_calendar()
    rig.delete(chosen, TODAY)
    rig.recreate(TODAY, None)

    resent = rig.service.uploaded[-1]

    for key in ("workoutId", "ownerId", "createdDate", "updatedDate", "workoutSourceId"):
        assert key not in resent
