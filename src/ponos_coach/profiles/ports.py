# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Ports of the profiles feature: list, folders with lock, profile database.

The real implementations live in the adapters (`platform` for registry and folders,
`storage` for the database); in-memory fakes and contract tests are in `tests/`.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Protocol

from ponos_coach.activities.domain import SessionTokens
from ponos_coach.activities.ports import ActivityIndex, SyncJournal, TrackBoxIndex
from ponos_coach.assessment.ports import ThresholdTestStore
from ponos_coach.goals.ports import AvailabilityStore, GoalStore, SessionChoicesStore
from ponos_coach.injury.ports import InjuryStore
from ponos_coach.load.ports import LoadCache, PhysiologyStore, SportWeightStore
from ponos_coach.nutrition.ports import (
    ActivityLevelStore,
    GoalPeriodStore,
    LeanMassStore,
    MealTimesStore,
)
from ponos_coach.planning.ports import PlanStore
from ponos_coach.planning.workout_calendar import WorkoutBackupStore
from ponos_coach.profiles.domain import (
    FixedProfileData,
    FolderInspection,
    ProfileId,
    ProfileInfo,
    ProfileName,
    ProfilePeek,
    RegistryEntry,
    WeightEntry,
)
from ponos_coach.profiles.domain.backup import BackupSummary

# --- Profile list --------------------------------------------------------------------


class ProfileRegistryError(Exception):
    """Root of the profile list errors (unreadable or corrupt configuration)."""


class ProfileAlreadyRegisteredError(ProfileRegistryError):
    """A profile with the same id or the same folder is already in the list."""


class ProfileNotRegisteredError(ProfileRegistryError):
    """The profile is not in the list."""


class ProfileRegistry(Protocol):
    """List of the profiles known to the app and of the last used one (app configuration)."""

    def list_entries(self) -> Sequence[RegistryEntry]:
        """Entries from the most recently used; ties by id."""
        ...

    def add(self, entry: RegistryEntry) -> None:
        """Adds an entry.

        Raises:
            ProfileAlreadyRegisteredError: id or folder already present; nothing changes.
        """
        ...

    def upsert(self, entry: RegistryEntry) -> None:
        """Brings the list to the state "this profile is in this folder", in a single write.

        Replaces the entry with the same id (whatever its folder was), removes every other
        entry with the same folder and, if there was no entry with that id, adds the new one.
        All or nothing: if the write fails the list stays as it was.
        """
        ...

    def remove(self, profile_id: ProfileId) -> None:
        """Removes the entry from the list. The folder and the profile's data are not touched.

        Raises:
            ProfileNotRegisteredError: id not present.
        """
        ...

    def mark_used(self, profile_id: ProfileId, when_utc: datetime) -> None:
        """Marks the profile's last use (UTC).

        Raises:
            ProfileNotRegisteredError: id not present.
        """
        ...


# --- Folders and lock -----------------------------------------------------------------------


class ProfileFolderError(Exception):
    """Root of the errors about profile folders."""


class FolderUnavailableError(ProfileFolderError):
    """The folder cannot be read or written (permissions, I/O, invalid path)."""


class FolderHasOtherContentError(FolderUnavailableError):
    """The profile folder contains files or folders that the app did not create: it is not deleted.

    It is a `FolderUnavailableError`: whoever does not distinguish the case treats it as a failed
    deletion; whoever distinguishes it can tell the user to remove them by hand.
    """


class FolderNotEmptyError(ProfileFolderError):
    """The folder to create already exists and contains something."""


class NotAProfileFolderError(ProfileFolderError):
    """The folder does not contain a profile's database: it is not deleted."""


class ProfileInUseError(ProfileFolderError):
    """The profile is already open in another instance (lock taken)."""


class ProfileLock(Protocol):
    """Exclusive lock of a profile, held until released."""

    def release(self) -> None:
        """Releases the lock; calling it again has no effect."""
        ...


class ProfileFolders(Protocol):
    """Profile folders: inspection, private creation, exclusive lock, deletion."""

    def inspect(self, folder: Path) -> FolderInspection:
        """State (missing, empty, not empty), kind (local, network, synchronized, unknown) and
        whether it contains a profile's database.

        Raises:
            FolderUnavailableError: the folder exists but cannot be read.
        """
        ...

    def create_private_folder(self, folder: Path) -> None:
        """Creates the folder (and the missing parents) readable and writable only by the user.

        If it exists and is empty it reuses it, restricting its permissions.

        Raises:
            FolderNotEmptyError: it exists and is not empty; nothing changes.
            FolderUnavailableError: creation or permissions failed.
        """
        ...

    def acquire_lock(self, folder: Path) -> ProfileLock:
        """Takes the profile's exclusive lock in `folder`, without waiting.

        A lock left by a dead process is recognized and overcome.

        Raises:
            ProfileInUseError: the lock is held by another instance (or by the caller).
            FolderUnavailableError: the folder does not exist or is not writable.
        """
        ...

    def remove_if_empty(self, folder: Path) -> bool:
        """Removes the folder if it is empty apart from the app's lock file; does not follow links.

        Used to undo a failed creation. It touches nothing (and gives `False`) if the folder
        is missing, is a link, contains anything besides the lock file or is locked by another
        instance.

        Raises:
            FolderUnavailableError: folder not readable or removal failed.
        """
        ...

    def delete_folder(self, folder: Path) -> None:
        """Deletes a profile's folder with all its content.

        Only if it contains a profile's database and if no instance holds it open; the
        lock is taken by the operation itself, so the caller must have released their own.
        Only the names the app puts in a profile folder are deleted (database with the
        `-wal`, `-shm` and `-journal` files, `archivio/`, `backup/`, lock): if there is anything
        else it is not
        touched.

        Raises:
            NotAProfileFolderError: the profile database is missing (even a nonexistent folder).
            ProfileInUseError: the profile is open.
            FolderHasOtherContentError: there is something else in the folder; nothing was deleted.
            FolderUnavailableError: deletion failed.
        """
        ...


# --- Garmin tokens -------------------------------------------------------------------------


class TokenStoreError(Exception):
    """Root of the token store errors. Never tokens in messages."""


class TokenStoreUnavailableError(TokenStoreError):
    """The keyring cannot be used (locked, absent, permissions, I/O)."""


class TokenStoreCorruptError(TokenStoreError):
    """There is content for the profile but it is not readable (altered, from another user or of an
    unknown version): the caller treats it as "no token" and asks for login."""


class TokenStore(Protocol):
    """Garmin tokens per profile, in the system keyring (never in the profile folder).

    Each profile has its own tokens, tied to its id: those of one profile cannot be read with the id
    of another. Tokens are protected by the system (DPAPI on Windows, Secret Service on
    Ubuntu) and do not appear in logs or messages.
    """

    def save(self, profile_id: ProfileId, tokens: SessionTokens) -> None:
        """Saves the profile's tokens, replacing those that were there (all or nothing).

        Raises:
            TokenStoreUnavailableError: save failed; the previous tokens stay.
        """
        ...

    def load(self, profile_id: ProfileId) -> SessionTokens | None:
        """The profile's tokens, or None if there are none.

        Raises:
            TokenStoreCorruptError: the content is there but cannot be read.
            TokenStoreUnavailableError: the keyring does not respond.
        """
        ...

    def delete(self, profile_id: ProfileId) -> None:
        """Deletes the profile's tokens (logout). Idempotent: no error if there are none.

        Raises:
            TokenStoreUnavailableError: deletion failed.
        """
        ...


# --- Profile database ------------------------------------------------------------------


class ProfileStorageError(Exception):
    """Root of the errors of a profile's database."""


class ProfileAlreadyExistsError(ProfileStorageError):
    """The folder already has a profile's database: `create` never overwrites it."""


class ProfileNotFoundError(ProfileStorageError):
    """The folder does not have a profile's database."""


class ProfileFormatTooNewError(ProfileStorageError):
    """The database is of a newer version than the one this app knows."""


class ProfileCorruptError(ProfileStorageError):
    """The database cannot be opened or does not pass the integrity checks."""


class ProfileDataError(ProfileStorageError):
    """Reading or writing the profile's personal data failed. Never values in messages."""


class FixedDataAlreadySetError(ProfileDataError):
    """Height and date of birth are already set: they are fixed and are not changed."""


class FixedDataStore(Protocol):
    """Height and date of birth: set only once, at profile creation or,
    for a profile that lacks them (created before they existed), once later."""

    def get(self) -> FixedProfileData | None:
        """The fixed data, or None if the profile does not have them yet.

        Raises:
            ProfileDataError: unreadable or invalid data.
        """
        ...

    def set_once(self, data: FixedProfileData) -> None:
        """Sets the fixed data.

        Raises:
            FixedDataAlreadySetError: they are already there; nothing changes.
            ProfileDataError: write failed.
        """
        ...


class WeightStore(Protocol):
    """Weight series with validity date."""

    def put(self, entry: WeightEntry) -> None:
        """Saves the weight valid from `entry.valid_from`; the same date replaces the entry.

        Raises:
            ProfileDataError: write failed.
        """
        ...

    def list_entries(self) -> Sequence[WeightEntry]:
        """All the entries by ascending validity date (only one per date).

        Raises:
            ProfileDataError: unreadable or invalid data.
        """
        ...


class OpenProfile(Protocol):
    """An open profile: its information and the stores that share a connection.

    The transactions of `activities.transaction()` also cover `track_boxes`, `sync_journal`,
    `physiology`, `weights`, `fixed_data`, `sport_weights`, `load_cache`, `activity_levels`,
    `lean_masses`, `meal_times`, `goal_periods`, `threshold_tests`, `goals`, `availability`,
    `session_choices`, `plans`, `workout_backups` and `injuries`.
    It must be closed with `close()`; after closing the stores are no longer used.
    """

    @property
    def info(self) -> ProfileInfo:
        """Profile information read from the database."""
        ...

    @property
    def activities(self) -> ActivityIndex:
        """Activity index."""
        ...

    @property
    def track_boxes(self) -> TrackBoxIndex:
        """Track boxes of the indexed activities (same connection and transaction)."""
        ...

    @property
    def sync_journal(self) -> SyncJournal:
        """Synchronization journal (same connection and transaction as the index)."""
        ...

    @property
    def physiology(self) -> PhysiologyStore:
        """Physiological parameters with validity date."""
        ...

    @property
    def weights(self) -> WeightStore:
        """Weight series with validity date."""
        ...

    @property
    def fixed_data(self) -> FixedDataStore:
        """Fixed height and date of birth."""
        ...

    @property
    def sport_weights(self) -> SportWeightStore:
        """Weight chosen by the user per sport."""
        ...

    @property
    def load_cache(self) -> LoadCache:
        """Load cache."""
        ...

    @property
    def lean_masses(self) -> LeanMassStore:
        """Measured lean mass series (optional)."""
        ...

    @property
    def activity_levels(self) -> ActivityLevelStore:
        """Activity level outside sport with validity date."""
        ...

    @property
    def meal_times(self) -> MealTimesStore:
        """Typical breakfast, lunch and dinner times."""
        ...

    @property
    def goal_periods(self) -> GoalPeriodStore:
        """Nutrition goal periods."""
        ...

    @property
    def threshold_tests(self) -> ThresholdTestStore:
        """History of the applied threshold tests."""
        ...

    @property
    def goals(self) -> GoalStore:
        """The goals (races and form goals)."""
        ...

    @property
    def availability(self) -> AvailabilityStore:
        """The weekly availability."""
        ...

    @property
    def session_choices(self) -> SessionChoicesStore:
        """The session types chosen for each sport."""
        ...

    @property
    def plans(self) -> PlanStore:
        """The current plan and the days of the past as they were planned."""
        ...

    @property
    def workout_backups(self) -> WorkoutBackupStore:
        """The local copy of the workouts deleted from the Garmin calendar."""

    @property
    def injuries(self) -> InjuryStore:
        """The injuries and illnesses, with their check-ins."""
        ...

    def close(self) -> None:
        """Closes the connection (checkpoint included); calling it again has no effect."""
        ...


class ProfileStorage(Protocol):
    """Creates or opens a profile's database in a folder."""

    def create(
        self, folder: Path, profile_id: ProfileId, name: ProfileName, timezone_name: str
    ) -> OpenProfile:
        """Creates the profile's database with the latest schema.

        The folder already exists (`ProfileFolders` creates it).

        Raises:
            ProfileAlreadyExistsError: there is already a database; it is not touched.
            ProfileStorageError: creation failed; nothing is left half-done.
            ValueError: unknown `timezone_name`.
        """
        ...

    def peek(self, folder: Path) -> ProfilePeek:
        """Reads information and number of activities without opening the profile: read-only.

        It does not migrate, write or take the lock: a profile open elsewhere can be looked at.
        A database of an earlier version is read as it is (`info.format_version` says so).

        Raises:
            ProfileNotFoundError: the database is missing.
            ProfileFormatTooNewError: version newer than the known one.
            ProfileCorruptError: database unreadable or not a profile's.
            ProfileStorageError: other failure (I/O, lock).
        """
        ...

    def open(self, folder: Path, *, check_integrity: bool = True) -> OpenProfile:
        """Opens the database, bringing it to the latest version if it is older.

        `check_integrity=False` skips the full integrity checks (they read the whole
        file): only for a second connection to the same already open and verified profile,
        like that of the synchronization thread. Normal opening always does them; a
        database to migrate is checked anyway.

        Before migrating it puts a consistent copy of the database in `backup/`, with the starting
        and target versions in the name.

        Raises:
            ProfileNotFoundError: the database is missing.
            ProfileFormatTooNewError: version newer than the known one; it is not touched.
            ProfileCorruptError: database unreadable, not intact or not a profile's.
            ProfileStorageError: other failure; if the migration fails the database stays
                as it was and the backup copy is in place.
        """
        ...


# --- Backup and restore --------------------------------------------------------------------


class BackupProblem(Enum):
    """Why a backup file cannot be restored. The value is stable."""

    NOT_A_BACKUP = "not_a_backup"  # not a ZIP, no manifest, wrong format or inconsistent entries
    LIMIT = "limit"  # too many entries, too many bytes, compression ratio, size per entry
    CHECKSUM = "checksum"  # a file differs from the size or SHA-256 in the manifest
    UNSAFE_ENTRY = "unsafe_entry"  # an entry outside the closed list of names, or encrypted
    DATABASE_INVALID = "database_invalid"  # the restored database is not an intact profile


class BackupError(Exception):
    """Root of the backup and restore errors. Never paths or contents in messages."""


class BackupFileExistsError(BackupError):
    """The backup file to write already exists: a backup never overwrites anything."""


class BackupWriteError(BackupError):
    """Writing the backup or the restored files failed (I/O, source database not readable)."""


class BackupInvalidError(BackupError):
    """The backup file cannot be restored; `problem` says why. Nothing was restored."""

    def __init__(self, problem: BackupProblem) -> None:
        super().__init__(problem.value)
        self.problem = problem


class BackupTooNewError(BackupError):
    """The backup is of a newer format, or holds a database newer than this app knows."""


class ProfileBackupStorage(Protocol):
    """Writes and reads backup files of a profile (format in `profiles.domain.backup`)."""

    def write(
        self, folder: Path, destination: Path, created_utc: datetime, app_version: str
    ) -> BackupSummary:
        """Writes a backup of the profile in `folder` to the new file `destination`.

        Works on an open profile: the database is copied with the SQLite backup API (a
        consistent snapshot), checked for integrity, and the activity files are added with the
        SHA-256 of their bytes. The file appears under its name only when complete
        (temporary file, flush, rename).

        Raises:
            BackupFileExistsError: `destination` exists; it is not touched.
            BackupWriteError: the database or a file cannot be read, a limit is exceeded or
                writing failed; no file is left behind.
        """
        ...

    def restore(self, source: Path, folder: Path) -> BackupSummary:
        """Restores the backup `source` into `folder`, which must hold nothing but the lock file.

        Every size, entry count and ratio is checked before extracting, every file against its
        SHA-256, the database against its integrity and its profile id. The database is written
        last. If anything fails everything this call wrote is removed.

        Raises:
            FolderNotEmptyError: `folder` holds something besides the lock file.
            BackupInvalidError: the file is not a restorable backup.
            BackupTooNewError: format or database newer than this app knows.
            BackupWriteError: reading `source` or writing `folder` failed.
        """
        ...
