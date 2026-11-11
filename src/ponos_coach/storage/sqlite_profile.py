# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""`ProfileStorage` on SQLite: a `ponos.sqlite3` file in the profile folder."""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path

from ponos_coach.activities.ports import (
    ActivityIndex,
    DeletedActivities,
    SyncJournal,
    TrackBoxIndex,
)
from ponos_coach.assessment.ports import ThresholdTestStore
from ponos_coach.goals.ports import AvailabilityStore, GoalStore, SessionChoicesStore
from ponos_coach.injury.ports import InjuryStore
from ponos_coach.load.ports import LoadCache, PhysiologyStore
from ponos_coach.nutrition.ports import (
    ActivityLevelStore,
    GoalPeriodStore,
    LeanMassStore,
    MealTimesStore,
)
from ponos_coach.planning.ports import PlanStore
from ponos_coach.planning.workout_calendar import WorkoutBackupStore
from ponos_coach.profiles.domain import (
    PROFILE_DATABASE_NAME,
    ProfileId,
    ProfileInfo,
    ProfileName,
    ProfilePeek,
)
from ponos_coach.profiles.ports import (
    FixedDataStore,
    OpenProfile,
    ProfileCorruptError,
    ProfileStorageError,
    ThresholdPaceStore,
    WeightStore,
)
from ponos_coach.profiles.timezones import require_known_timezone
from ponos_coach.storage.sqlite_assessment import SqliteThresholdTestStore
from ponos_coach.storage.sqlite_database import (
    SqliteDatabase,
    create_database,
    is_corruption,
    open_database,
    peek_database,
    read_meta,
)
from ponos_coach.storage.sqlite_deleted import SqliteDeletedActivities
from ponos_coach.storage.sqlite_goals import (
    SqliteAvailabilityStore,
    SqliteGoalStore,
    SqliteSessionChoicesStore,
)
from ponos_coach.storage.sqlite_injury import SqliteInjuryStore
from ponos_coach.storage.sqlite_nutrition import (
    SqliteActivityLevelStore,
    SqliteGoalPeriodStore,
    SqliteLeanMassStore,
    SqliteMealTimesStore,
)
from ponos_coach.storage.sqlite_planning import SqlitePlanStore
from ponos_coach.storage.sqlite_profile_data import (
    SqliteFixedDataStore,
    SqliteThresholdPaceStore,
    SqliteWeightStore,
)
from ponos_coach.storage.sqlite_stores import (
    SqliteActivityIndex,
    SqliteLoadCache,
    SqlitePhysiologyStore,
)
from ponos_coach.storage.sqlite_sync import SqliteSyncJournal
from ponos_coach.storage.sqlite_track_boxes import SqliteTrackBoxIndex
from ponos_coach.storage.sqlite_workouts import SqliteWorkoutBackupStore

_META_ID = "profile_id"
_META_NAME = "name"
_META_TIMEZONE = "timezone"


def _info_from(meta: Mapping[str, str], format_version: int) -> ProfileInfo:
    """Profile information from `meta`; missing or invalid → `ProfileCorruptError`."""
    try:
        timezone_name = meta[_META_TIMEZONE]
        require_known_timezone(timezone_name)
        return ProfileInfo(
            ProfileId.parse(meta[_META_ID]),
            ProfileName(meta[_META_NAME]),
            timezone_name,
            format_version,
        )
    except (KeyError, ValueError) as exc:
        raise ProfileCorruptError("profile information missing or invalid") from exc


def _read_info(db: SqliteDatabase) -> ProfileInfo:
    """Profile information from the open database."""
    try:
        return _info_from(read_meta(db), db.user_version())
    except sqlite3.Error as exc:
        if is_corruption(exc):
            raise ProfileCorruptError("the file is not an intact database") from exc
        raise ProfileStorageError("reading the profile information failed") from exc


class SqliteOpenProfile:
    """An open profile: the stores share the `SqliteDatabase` connection."""

    def __init__(self, database: SqliteDatabase, info: ProfileInfo) -> None:
        self._db = database
        self._info = info
        self._activities = SqliteActivityIndex(database)
        self._deleted_activities = SqliteDeletedActivities(database)
        self._track_boxes = SqliteTrackBoxIndex(database)
        self._sync_journal = SqliteSyncJournal(database)
        self._physiology = SqlitePhysiologyStore(database)
        self._weights = SqliteWeightStore(database)
        self._threshold_paces = SqliteThresholdPaceStore(database)
        self._fixed_data = SqliteFixedDataStore(database)
        self._load_cache = SqliteLoadCache(database)
        self._activity_levels = SqliteActivityLevelStore(database)
        self._lean_masses = SqliteLeanMassStore(database)
        self._meal_times = SqliteMealTimesStore(database)
        self._goal_periods = SqliteGoalPeriodStore(database)
        self._threshold_tests = SqliteThresholdTestStore(database)
        self._goals = SqliteGoalStore(database)
        self._availability = SqliteAvailabilityStore(database)
        self._session_choices = SqliteSessionChoicesStore(database)
        self._plans = SqlitePlanStore(database)
        self._workout_backups = SqliteWorkoutBackupStore(database)
        self._injuries = SqliteInjuryStore(database)
        self._closed = False

    @property
    def info(self) -> ProfileInfo:
        """Information read from the database at opening."""
        return self._info

    @property
    def removed_unsupported_activities(self) -> int:
        """Non-FIT activities removed by the migration done at open time (0 if none)."""
        return self._db.migration_report.removed_activities

    @property
    def activities(self) -> ActivityIndex:
        """Activity index."""
        return self._activities

    @property
    def deleted_activities(self) -> DeletedActivities:
        """The activities the user deleted (tombstones)."""
        return self._deleted_activities

    @property
    def track_boxes(self) -> TrackBoxIndex:
        """Track boxes of the indexed activities (for the map)."""
        return self._track_boxes

    @property
    def sync_journal(self) -> SyncJournal:
        """Synchronization journal."""
        return self._sync_journal

    @property
    def physiology(self) -> PhysiologyStore:
        """Physiological parameters."""
        return self._physiology

    @property
    def weights(self) -> WeightStore:
        """Weight series."""
        return self._weights

    @property
    def threshold_paces(self) -> ThresholdPaceStore:
        """Threshold pace series."""
        return self._threshold_paces

    @property
    def fixed_data(self) -> FixedDataStore:
        """Height and date of birth."""
        return self._fixed_data

    @property
    def load_cache(self) -> LoadCache:
        """Load cache."""
        return self._load_cache

    @property
    def activity_levels(self) -> ActivityLevelStore:
        """Activity level outside sport."""
        return self._activity_levels

    @property
    def lean_masses(self) -> LeanMassStore:
        """Measured lean mass."""
        return self._lean_masses

    @property
    def meal_times(self) -> MealTimesStore:
        """Typical meal times."""
        return self._meal_times

    @property
    def goal_periods(self) -> GoalPeriodStore:
        """Nutrition goal periods."""
        return self._goal_periods

    @property
    def threshold_tests(self) -> ThresholdTestStore:
        """Threshold test history."""
        return self._threshold_tests

    @property
    def goals(self) -> GoalStore:
        """Goals (races and form goals)."""
        return self._goals

    @property
    def availability(self) -> AvailabilityStore:
        """Weekly availability."""
        return self._availability

    @property
    def session_choices(self) -> SessionChoicesStore:
        """Chosen session types."""
        return self._session_choices

    @property
    def plans(self) -> PlanStore:
        """Current plan and past days as they were planned."""
        return self._plans

    @property
    def workout_backups(self) -> WorkoutBackupStore:
        """Local copy of the workouts deleted from the Garmin calendar."""
        return self._workout_backups

    @property
    def injuries(self) -> InjuryStore:
        """Injuries and illnesses with their check-ins."""
        return self._injuries

    def rename(self, name: ProfileName) -> None:
        """Writes the name to `meta` in one transaction and updates `info` only once written.

        Raises:
            ProfileStorageError: the write failed; the name stays as it was.
            StorageBusyError: another process holds the database; the name stays as it was.
        """
        try:
            with self._db.atomic():
                self._db.connection.execute(
                    "INSERT INTO meta (key, value) VALUES (?, ?) "
                    "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
                    (_META_NAME, name.value),
                )
        except sqlite3.Error as exc:
            raise ProfileStorageError("writing the profile name failed") from exc
        self._info = replace(self._info, name=name)

    def close(self) -> None:
        """Closes the connection (checkpoint included); repeating it has no effect.

        Raises:
            ProfileStorageError: the checkpoint did not succeed; the connection is closed and the
                data stay intact in the WAL.
        """
        if self._closed:
            return
        self._closed = True
        try:
            self._db.close()
        except sqlite3.Error as exc:
            raise ProfileStorageError("checkpoint at closing failed") from exc


class SqliteProfileStorage:
    """Creates or opens the `ponos.sqlite3` database of a profile (`ProfileStorage` port)."""

    def create(
        self, folder: Path, profile_id: ProfileId, name: ProfileName, timezone_name: str
    ) -> OpenProfile:
        """Creates the database with the latest schema; never overwrites a database.

        Raises:
            ProfileAlreadyExistsError: there is already a database; it is not touched.
            ProfileStorageError: creation failed; the created file is removed.
            ValueError: unknown `timezone_name` (nothing is written).
        """
        require_known_timezone(timezone_name)  # before writing to disk
        info = ProfileInfo(profile_id, name, timezone_name, 1)
        db = create_database(
            folder / PROFILE_DATABASE_NAME,
            {
                _META_ID: str(info.profile_id),
                _META_NAME: info.name.value,
                _META_TIMEZONE: info.timezone_name,
            },
        )
        try:
            return SqliteOpenProfile(db, _read_info(db))
        except BaseException:
            db.close()
            raise

    def peek(self, folder: Path) -> ProfilePeek:
        """Information and number of activities read-only; see `ProfileStorage.peek`.

        Raises:
            ProfileNotFoundError: the database is missing.
            ProfileFormatTooNewError: version newer than the known one.
            ProfileCorruptError: database unreadable or not a profile's.
            ProfileStorageError: other failure.
        """
        peeked = peek_database(folder / PROFILE_DATABASE_NAME)
        return ProfilePeek(_info_from(peeked.meta, peeked.user_version), peeked.activity_count)

    def open(self, folder: Path, *, check_integrity: bool = True) -> OpenProfile:
        """Opens the database, migrating it if older (with a backup copy in `backup/`).

        `check_integrity=False` skips the full integrity checks: only for a second
        connection to an already open and verified profile (the synchronization).

        Raises:
            ProfileNotFoundError: the database is missing.
            ProfileFormatTooNewError: version newer than the known one; it is not touched.
            ProfileCorruptError: database unreadable, not intact or not a profile's.
            ProfileStorageError: other failure or failed migration (the database stays as it was).
        """
        db = open_database(folder / PROFILE_DATABASE_NAME, check_integrity=check_integrity)
        try:
            return SqliteOpenProfile(db, _read_info(db))
        except BaseException:
            db.close()
            raise
