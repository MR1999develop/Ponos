# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Contracts of the workout calendar and of the local copy of deleted workouts (phase 7).

The same contract runs on the in-memory fake and on the real implementation: the calendar on
`GarminWorkoutCalendar` over a fake Garmin service, the backup store on SQLite.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import closing
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Protocol

import pytest

from ponos_coach.activities.domain import Sport
from ponos_coach.activities.ports import DefinitiveSourceError
from ponos_coach.garmin.workouts import GarminWorkoutCalendar
from ponos_coach.planning.domain import (
    WORKOUT_PREFIX,
    ScheduledWorkout,
    StepKind,
    WorkoutSpec,
    WorkoutStep,
)
from ponos_coach.planning.workout_calendar import (
    MAX_BACKUP_PAYLOAD_CHARS,
    MAX_PENDING_BACKUPS,
    NewBackup,
    WorkoutBackupStore,
    WorkoutBackupStoreError,
    WorkoutCalendar,
)
from ponos_coach.storage import schema_workout_backup
from ponos_coach.storage.sqlite_database import SqliteDatabase, create_database
from ponos_coach.storage.sqlite_workouts import SqliteWorkoutBackupStore
from tests.fakes.workout_calendar import InMemoryWorkoutBackupStore, InMemoryWorkoutCalendar
from tests.garmin.workout_fakes import FakeWorkoutService

DAY = date(2026, 6, 10)
NOW = datetime(2026, 6, 1, 8, 0, tzinfo=UTC)


def a_spec(minutes: int = 30) -> WorkoutSpec:
    step = WorkoutStep(StepKind.INTERVAL, minutes * 60, 120, 140, 330, 300)
    return WorkoutSpec(f"{WORKOUT_PREFIX}Easy {minutes} min", Sport.RUNNING, (step,))


# --- The calendar --------------------------------------------------------------------------------
class CalendarRig(Protocol):
    """A calendar under test and the ways to look inside it."""

    @property
    def calendar(self) -> WorkoutCalendar:
        """The calendar under test."""
        ...

    def add_foreign(self, name: str, day: date) -> ScheduledWorkout:
        """A workout of the athlete on `day`, as `list_scheduled` returns it."""
        ...

    def library_has(self, workout_id: int) -> bool:
        """Whether the workout is in the library."""
        ...


class _FakeRig:
    def __init__(self) -> None:
        self.fake = InMemoryWorkoutCalendar()

    @property
    def calendar(self) -> WorkoutCalendar:
        return self.fake

    def add_foreign(self, name: str, day: date) -> ScheduledWorkout:
        return self.fake.add_foreign(name, day)

    def library_has(self, workout_id: int) -> bool:
        return workout_id in self.fake.library_ids()


class _GarminRig:
    def __init__(self) -> None:
        self.service = FakeWorkoutService()
        self._calendar = GarminWorkoutCalendar(self.service)

    @property
    def calendar(self) -> WorkoutCalendar:
        return self._calendar

    def add_foreign(self, name: str, day: date) -> ScheduledWorkout:
        self.service.add_foreign(name, day)
        (found,) = [w for w in self._calendar.list_scheduled(day, day) if w.name == name]
        return found

    def library_has(self, workout_id: int) -> bool:
        return workout_id in self.service.library_ids()


class WorkoutCalendarContract:
    """What every `WorkoutCalendar` does."""

    @pytest.fixture
    def rig(self) -> CalendarRig:
        raise NotImplementedError

    def test_a_created_workout_is_listed_on_its_day_with_its_name(self, rig: CalendarRig) -> None:
        created = rig.calendar.create_and_schedule(a_spec(), DAY)

        assert (created.name, created.day) == (a_spec().name, DAY)
        assert created.is_app_created
        assert list(rig.calendar.list_scheduled(DAY, DAY)) == [created]

    def test_the_list_covers_the_range_both_ends_included_and_is_ordered(
        self, rig: CalendarRig
    ) -> None:
        late = rig.calendar.create_and_schedule(a_spec(40), DAY + timedelta(days=2))
        first = rig.calendar.create_and_schedule(a_spec(30), DAY)
        rig.calendar.create_and_schedule(a_spec(20), DAY + timedelta(days=3))
        athletes = rig.add_foreign("Tempo run", DAY)

        found = rig.calendar.list_scheduled(DAY, DAY + timedelta(days=2))

        assert list(found) == [first, athletes, late] or list(found) == [athletes, first, late]
        assert [w.day for w in found] == sorted(w.day for w in found)

    def test_an_inverted_or_too_long_range_is_a_value_error(self, rig: CalendarRig) -> None:
        with pytest.raises(ValueError, match="range"):
            rig.calendar.list_scheduled(DAY, DAY - timedelta(days=1))
        with pytest.raises(ValueError, match="range"):
            rig.calendar.list_scheduled(DAY, DAY + timedelta(days=4000))

    def test_removing_with_the_workout_leaves_the_rest_of_the_calendar(
        self, rig: CalendarRig
    ) -> None:
        gone = rig.calendar.create_and_schedule(a_spec(30), DAY)
        kept = rig.calendar.create_and_schedule(a_spec(40), DAY)

        rig.calendar.remove(gone, delete_workout=True)

        assert list(rig.calendar.list_scheduled(DAY, DAY)) == [kept]
        assert not rig.library_has(gone.workout_id)
        assert rig.library_has(kept.workout_id)

    def test_removing_without_deleting_keeps_the_workout_in_the_library(
        self, rig: CalendarRig
    ) -> None:
        item = rig.add_foreign("Tempo run", DAY)

        rig.calendar.remove(item, delete_workout=False)

        assert rig.calendar.list_scheduled(DAY, DAY) == []
        assert rig.library_has(item.workout_id)

    def test_removing_what_is_already_gone_is_not_an_error(self, rig: CalendarRig) -> None:
        item = rig.calendar.create_and_schedule(a_spec(), DAY)
        rig.calendar.remove(item, delete_workout=True)

        rig.calendar.remove(item, delete_workout=True)

        assert rig.calendar.list_scheduled(DAY, DAY) == []

    def test_an_exported_definition_creates_a_new_workout_with_the_same_name(
        self, rig: CalendarRig
    ) -> None:
        original = rig.calendar.create_and_schedule(a_spec(), DAY)
        text = rig.calendar.export_definition(original.workout_id)
        rig.calendar.remove(original, delete_workout=True)

        again = rig.calendar.create_from_definition(text, DAY + timedelta(days=1))

        assert again.name == original.name
        assert again.day == DAY + timedelta(days=1)
        assert again.workout_id != original.workout_id
        assert list(rig.calendar.list_scheduled(DAY, DAY + timedelta(days=1))) == [again]
        assert 0 < len(text) <= MAX_BACKUP_PAYLOAD_CHARS

    def test_exporting_a_workout_that_is_not_there_is_a_definitive_error(
        self, rig: CalendarRig
    ) -> None:
        with pytest.raises(DefinitiveSourceError):
            rig.calendar.export_definition(987_654)


class TestFakeWorkoutCalendar(WorkoutCalendarContract):
    @pytest.fixture
    def rig(self) -> CalendarRig:
        return _FakeRig()


class TestGarminWorkoutCalendar(WorkoutCalendarContract):
    @pytest.fixture
    def rig(self) -> CalendarRig:
        return _GarminRig()


# --- The local copy ------------------------------------------------------------------------------
def a_backup(name: str = "Ponos · Easy 30 min", at: datetime = NOW) -> NewBackup:
    return NewBackup(name, DAY, '{"workoutName": "x"}', at)


class WorkoutBackupStoreContract:
    """What every `WorkoutBackupStore` does."""

    @pytest.fixture
    def store(self) -> Iterator[WorkoutBackupStore]:
        raise NotImplementedError

    def test_a_kept_copy_is_pending_with_everything_it_was_given(
        self, store: WorkoutBackupStore
    ) -> None:
        kept = store.add(a_backup())

        (pending,) = store.pending()

        assert pending == kept
        assert (pending.name, pending.day, pending.payload) == (
            "Ponos · Easy 30 min",
            DAY,
            '{"workoutName": "x"}',
        )
        assert pending.saved_at == NOW
        assert pending.restored_at is None

    def test_pending_copies_come_oldest_first(self, store: WorkoutBackupStore) -> None:
        later = store.add(a_backup("b", NOW + timedelta(hours=1)))
        earlier = store.add(a_backup("a", NOW))

        assert [b.backup_id for b in store.pending()] == [earlier.backup_id, later.backup_id]

    def test_a_restored_copy_is_no_longer_pending_and_marking_twice_is_harmless(
        self, store: WorkoutBackupStore
    ) -> None:
        kept = store.add(a_backup())

        store.mark_restored(kept.backup_id, NOW + timedelta(minutes=5))
        store.mark_restored(kept.backup_id, NOW + timedelta(minutes=9))

        assert store.pending() == []

    def test_a_discarded_copy_is_gone_but_a_restored_one_is_left_alone(
        self, store: WorkoutBackupStore
    ) -> None:
        dropped = store.add(a_backup("dropped"))
        done = store.add(a_backup("done"))
        store.mark_restored(done.backup_id, NOW + timedelta(minutes=5))

        store.discard(dropped.backup_id)
        store.discard(done.backup_id)
        store.discard(12345)

        assert store.pending() == []
        store.mark_restored(done.backup_id, NOW + timedelta(minutes=9))  # still known

    def test_marking_an_unknown_copy_fails(self, store: WorkoutBackupStore) -> None:
        with pytest.raises(WorkoutBackupStoreError):
            store.mark_restored(12345, NOW)

    def test_the_store_refuses_new_copies_when_too_many_are_waiting(
        self, store: WorkoutBackupStore
    ) -> None:
        for index in range(MAX_PENDING_BACKUPS):
            store.add(a_backup(f"w{index}"))

        with pytest.raises(WorkoutBackupStoreError, match="too many"):
            store.add(a_backup("one more"))
        assert len(store.pending()) == MAX_PENDING_BACKUPS

    def test_a_restored_copy_is_forgotten_after_a_month_and_makes_room(
        self, store: WorkoutBackupStore
    ) -> None:
        old = store.add(a_backup("old"))
        store.mark_restored(old.backup_id, NOW)

        store.add(a_backup("new", NOW + timedelta(days=31)))

        assert [b.name for b in store.pending()] == ["new"]


class TestFakeWorkoutBackupStore(WorkoutBackupStoreContract):
    @pytest.fixture
    def store(self) -> Iterator[WorkoutBackupStore]:
        yield InMemoryWorkoutBackupStore()


class TestSqliteWorkoutBackupStore(WorkoutBackupStoreContract):
    @pytest.fixture
    def store(self, tmp_path: Path) -> Iterator[WorkoutBackupStore]:
        database = create_database(tmp_path / "profile.sqlite3", {})
        try:
            yield SqliteWorkoutBackupStore(database)
        finally:
            database.close()

    def test_a_copy_whose_text_was_altered_is_left_out_of_the_pending_ones(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "profile.sqlite3"
        database: SqliteDatabase = create_database(path, {})
        store = SqliteWorkoutBackupStore(database)
        store.add(a_backup("good"))
        store.add(a_backup("bad"))
        database.connection.execute(
            "UPDATE workout_backup SET payload = ? WHERE name = 'bad'", ('{"workoutName": "y"}',)
        )

        assert [b.name for b in store.pending()] == ["good"]
        database.close()

    def test_the_database_itself_refuses_a_restore_before_the_save_and_a_huge_text(
        self, tmp_path: Path
    ) -> None:
        database = create_database(tmp_path / "profile.sqlite3", {})
        connection = database.connection
        insert = (
            "INSERT INTO workout_backup (name, calendar_date, payload, payload_sha256, saved_at, "
            "restored_at) VALUES ('n', '2026-06-10', ?, ?, 1000, ?)"
        )

        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(insert, ("{}", "0" * 64, 5))
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(insert, ("x" * (MAX_BACKUP_PAYLOAD_CHARS + 1), "0" * 64, None))
        with closing(connection):
            pass

    def test_the_schema_ceiling_is_the_one_of_the_port(self) -> None:
        # The migration is frozen text (it has no imports), so the two constants are tied here.
        assert schema_workout_backup.MAX_PAYLOAD_CHARS == MAX_BACKUP_PAYLOAD_CHARS
