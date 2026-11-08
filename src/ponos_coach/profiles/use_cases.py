# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Profile use cases: create, open (from a folder or from the list), list, peek (`peek`),
remove from the list, delete the folder.

This is the API the interface uses. Each use case receives the ports (`ProfilePorts`) and, if
needed,
the clock in the constructor; it does no direct I/O. An open profile is a `ProfileSession`: it holds
the database and the lock until `close()` is called.

Operations that touch several things (create, open) either succeed or leave things as before: if
a step fails, those already done are undone, and an undo that itself fails is
added as a note to the original error (which remains the one raised).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from types import TracebackType
from typing import Self

from ponos_coach.activities.ports import ActivityIndex, SyncJournal, TrackBoxIndex
from ponos_coach.assessment.ports import ThresholdTestStore
from ponos_coach.goals.ports import AvailabilityStore, GoalStore, SessionChoicesStore
from ponos_coach.injury.ports import InjuryStore
from ponos_coach.load.domain import Physiology, PhysiologyEntry
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
    FolderKind,
    FolderState,
    ProfileId,
    ProfileInfo,
    ProfileName,
    ProfilePeek,
    RegistryEntry,
    Weight,
    WeightEntry,
)
from ponos_coach.profiles.ports import (
    FixedDataStore,
    FolderNotEmptyError,
    FolderUnavailableError,
    NotAProfileFolderError,
    OpenProfile,
    ProfileFolders,
    ProfileLock,
    ProfileNotFoundError,
    ProfileNotRegisteredError,
    ProfileRegistry,
    ProfileStorage,
    WeightStore,
)
from ponos_coach.profiles.timezones import require_known_timezone


class FolderWarning(Enum):
    """Non-blocking warning about a profile's folder: SQLite is at risk in these places."""

    NETWORK = "network"
    CLOUD_SYNCED = "cloud_synced"


class ProfileMismatchError(Exception):
    """The registered folder contains the database of another profile."""


@dataclass(frozen=True, slots=True)
class ProfilePorts:
    """The three ports that the profile use cases use."""

    registry: ProfileRegistry
    folders: ProfileFolders
    storage: ProfileStorage


def warnings_for(kind: FolderKind) -> tuple[FolderWarning, ...]:
    """Warnings for the folder kind (none for local or unknown)."""
    if kind is FolderKind.NETWORK:
        return (FolderWarning.NETWORK,)
    if kind is FolderKind.CLOUD_SYNCED:
        return (FolderWarning.CLOUD_SYNCED,)
    return ()


class ProfileSession:
    """An open profile: information, database stores and lock on the folder.

    `close()` closes the database and then releases the lock (always, even if closing the
    database fails); repeating it has no effect. It is also used as a context manager.
    """

    def __init__(
        self,
        opened: OpenProfile,
        lock: ProfileLock,
        folder: Path,
        warnings: Sequence[FolderWarning],
    ) -> None:
        self._opened = opened
        self._lock = lock
        self._closed = False
        self.folder = folder
        self.warnings = tuple(warnings)

    @property
    def info(self) -> ProfileInfo:
        """Profile information read from the database."""
        return self._opened.info

    @property
    def activities(self) -> ActivityIndex:
        """Activity index (valid while the profile is open)."""
        return self._opened.activities

    @property
    def track_boxes(self) -> TrackBoxIndex:
        """Track boxes of the index (for the map and its resumable fill)."""
        return self._opened.track_boxes

    @property
    def sync_journal(self) -> SyncJournal:
        """Synchronization journal (same connection and transaction as the index)."""
        return self._opened.sync_journal

    @property
    def physiology(self) -> PhysiologyStore:
        """Physiological parameters with validity date."""
        return self._opened.physiology

    @property
    def weights(self) -> WeightStore:
        """Weight series with validity date."""
        return self._opened.weights

    @property
    def fixed_data(self) -> FixedDataStore:
        """Fixed height and date of birth."""
        return self._opened.fixed_data

    @property
    def sport_weights(self) -> SportWeightStore:
        """Weight chosen by the user per sport."""
        return self._opened.sport_weights

    @property
    def load_cache(self) -> LoadCache:
        """Load cache."""
        return self._opened.load_cache

    @property
    def activity_levels(self) -> ActivityLevelStore:
        """Activity level outside sport with validity date."""
        return self._opened.activity_levels

    @property
    def lean_masses(self) -> LeanMassStore:
        """Measured lean mass series (optional)."""
        return self._opened.lean_masses

    @property
    def meal_times(self) -> MealTimesStore:
        """Typical breakfast, lunch and dinner times."""
        return self._opened.meal_times

    @property
    def goal_periods(self) -> GoalPeriodStore:
        """Nutrition goal periods."""
        return self._opened.goal_periods

    @property
    def threshold_tests(self) -> ThresholdTestStore:
        """History of the applied threshold tests."""
        return self._opened.threshold_tests

    @property
    def goals(self) -> GoalStore:
        """The goals (races and form goals)."""
        return self._opened.goals

    @property
    def availability(self) -> AvailabilityStore:
        """The weekly availability."""
        return self._opened.availability

    @property
    def session_choices(self) -> SessionChoicesStore:
        """The session types chosen for each sport."""
        return self._opened.session_choices

    @property
    def plans(self) -> PlanStore:
        """The current plan and the days of the past as they were planned."""
        return self._opened.plans

    @property
    def workout_backups(self) -> WorkoutBackupStore:
        """The local copy of the workouts deleted from the Garmin calendar."""
        return self._opened.workout_backups

    @property
    def injuries(self) -> InjuryStore:
        """The injuries and illnesses, with their check-ins."""
        return self._opened.injuries

    def close(self) -> None:
        """Closes the database and releases the lock."""
        if self._closed:
            return
        self._closed = True
        try:
            self._opened.close()
        finally:
            self._lock.release()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()


class _Undo:
    """Undo steps, executed from last to first if the operation fails."""

    def __init__(self) -> None:
        self._steps: list[Callable[[], None]] = []

    def add(self, step: Callable[[], None]) -> None:
        self._steps.append(step)

    def run(self, failure: BaseException) -> None:
        for step in reversed(self._steps):
            try:
                step()
            except Exception as exc:
                failure.add_note(f"undo failed: {type(exc).__name__}")


def _require_absolute(folder: Path) -> None:
    if not folder.is_absolute():
        raise FolderUnavailableError("the profile folder must be an absolute path")


# --- Create --------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class NewProfileRequest:
    """Data to create a profile.

    `folder` absolute, missing or empty; `timezone_name` an IANA name; `physiology` and `weight`
    hold from the day `today` (today's date in the profile's time zone, decided by the caller).
    `fixed` (height and date of birth) and `weight` are personal data that the interface always
    asks for at creation; `None` leaves the profile without them, to be completed later (used by
    whoever
    creates profiles only for the load computation).
    """

    name: ProfileName
    folder: Path
    timezone_name: str
    physiology: Physiology
    today: date
    fixed: FixedProfileData | None = None
    weight: Weight | None = None

    def __post_init__(self) -> None:
        if not self.folder.is_absolute():
            raise ValueError("the profile folder must be an absolute path")


class CreateProfile:
    """Creates a profile: private folder, lock, database, initial measurements (physiological
    parameters,
    weight, height and date of birth), list entry.

    If a step fails no entry remains in the list, the lock is released and only what this
    operation did is undone. If it created the database (creation is
    exclusive: whoever loses did not create it), the folder is deleted with all its content;
    otherwise the folder is removed only if it is empty apart from the lock file. A database or
    a folder put there in the meantime by another instance is never touched. Parents
    created along the path remain.
    """

    def __init__(
        self,
        ports: ProfilePorts,
        now_utc: Callable[[], datetime],
        new_profile_id: Callable[[], ProfileId],
    ) -> None:
        self._ports = ports
        self._now_utc = now_utc
        self._new_profile_id = new_profile_id

    def __call__(self, request: NewProfileRequest) -> ProfileSession:
        """Creates the profile and returns it open.

        Raises:
            FolderNotEmptyError: the folder exists and is not empty; nothing was created.
            FolderUnavailableError: folder cannot be created or is not writable.
            ProfileInUseError: lock not obtained.
            ProfileAlreadyExistsError: another instance put a database in the folder
                before us; its profile is not touched.
            ProfileStorageError: database not created.
            ProfileRegistryError: profile list not writable.
            ValueError: unknown time zone (before touching the disk).
        """
        require_known_timezone(request.timezone_name)
        ports = self._ports
        inspection = ports.folders.inspect(request.folder)
        if inspection.state is FolderState.NOT_EMPTY:
            raise FolderNotEmptyError(request.folder.name)
        trace = _CreationTrace(ports.folders, request.folder)
        try:
            session = self._create(request, inspection.kind, trace)
        except BaseException as failure:
            trace.roll_back(failure)
            raise
        return session

    def _create(
        self, request: NewProfileRequest, kind: FolderKind, trace: _CreationTrace
    ) -> ProfileSession:
        ports = self._ports
        folder = request.folder
        ports.folders.create_private_folder(folder)
        trace.folder_made = True
        lock = ports.folders.acquire_lock(folder)
        trace.lock = lock
        profile_id = self._new_profile_id()
        opened = ports.storage.create(folder, profile_id, request.name, request.timezone_name)
        trace.opened = opened
        trace.database_created = True
        with opened.activities.transaction():  # parameters, weight and fixed data: all or none
            opened.physiology.put(PhysiologyEntry(request.today, request.physiology))
            if request.weight is not None:
                opened.weights.put(WeightEntry(request.today, request.weight))
            if request.fixed is not None:
                opened.fixed_data.set_once(request.fixed)
        ports.registry.add(RegistryEntry(profile_id, folder, request.name, self._now_utc()))
        return ProfileSession(opened, lock, folder, warnings_for(kind))


class _CreationTrace:
    """What `CreateProfile` has done so far, to undo only that if something fails."""

    def __init__(self, folders: ProfileFolders, folder: Path) -> None:
        self._folders = folders
        self._folder = folder
        self.folder_made = False
        self.lock: ProfileLock | None = None
        self.opened: OpenProfile | None = None
        self.database_created = False

    def roll_back(self, failure: BaseException) -> None:
        """Undoes in reverse order: closes the database, releases the lock, then removes the
        folder. An undo that fails is added as a note to `failure`."""
        steps: list[Callable[[], object]] = []
        if self.opened is not None:
            steps.append(self.opened.close)
        if self.lock is not None:
            steps.append(self.lock.release)  # before deletion, which takes its own lock
        if self.database_created:
            steps.append(lambda: self._folders.delete_folder(self._folder))
        elif self.folder_made:
            steps.append(lambda: self._folders.remove_if_empty(self._folder))
        for step in steps:
            try:
                step()
            except Exception as exc:
                failure.add_note(f"undo failed: {type(exc).__name__}")


# --- Open ----------------------------------------------------------------------------------


class OpenProfileFromFolder:
    """Opens the profile that lives in a folder and puts it (or updates it) in the list.

    The list takes the name read from the database, which is the source of truth; a profile already
    listed with another folder (moved) or a folder already listed with another profile
    (replaced) are realigned.
    """

    def __init__(self, ports: ProfilePorts, now_utc: Callable[[], datetime]) -> None:
        self._ports = ports
        self._now_utc = now_utc

    def __call__(self, folder: Path) -> ProfileSession:
        """Opens the profile in `folder`.

        Raises:
            ProfileNotFoundError: the folder is missing or empty.
            NotAProfileFolderError: the folder has files but not a profile's database
                (the lock is not taken).
            ProfileInUseError: the profile is open in another instance.
            FolderUnavailableError: folder not readable.
            ProfileFormatTooNewError, ProfileCorruptError: database cannot be opened.
            ProfileRegistryError: profile list cannot be updated.
        """
        return _open_session(self._ports, self._now_utc, folder, expected_id=None)


class OpenRegisteredProfile:
    """Opens a profile from the list, checking that the folder really contains that profile."""

    def __init__(self, ports: ProfilePorts, now_utc: Callable[[], datetime]) -> None:
        self._ports = ports
        self._now_utc = now_utc

    def __call__(self, profile_id: ProfileId) -> ProfileSession:
        """Opens the profile `profile_id` from its registered folder.

        Raises:
            ProfileNotRegisteredError: id not in the list.
            ProfileMismatchError: the folder contains another profile (nothing changes).
            ProfileNotFoundError, NotAProfileFolderError, ProfileInUseError,
            FolderUnavailableError,
            ProfileFormatTooNewError, ProfileCorruptError, ProfileRegistryError: as
            `OpenProfileFromFolder`.
        """
        for entry in self._ports.registry.list_entries():
            if entry.profile_id == profile_id:
                return _open_session(
                    self._ports, self._now_utc, entry.folder, expected_id=profile_id
                )
        raise ProfileNotRegisteredError(str(profile_id))


def _open_session(
    ports: ProfilePorts,
    now_utc: Callable[[], datetime],
    folder: Path,
    expected_id: ProfileId | None,
) -> ProfileSession:
    _require_absolute(folder)
    inspection = ports.folders.inspect(folder)
    if inspection.state is not FolderState.NOT_EMPTY:
        raise ProfileNotFoundError(folder.name)  # no lock in an empty or missing folder
    if not inspection.has_profile_database:
        raise NotAProfileFolderError(folder.name)  # nor in one that lacks the database
    undo = _Undo()
    try:
        lock = ports.folders.acquire_lock(folder)
        undo.add(lock.release)
        opened = ports.storage.open(folder)
        undo.add(opened.close)
        _check_expected(opened.info, expected_id)
        _register_opened(ports.registry, opened.info, folder, now_utc())
        return ProfileSession(opened, lock, folder, warnings_for(inspection.kind))
    except BaseException as failure:
        undo.run(failure)
        raise


def _check_expected(info: ProfileInfo, expected_id: ProfileId | None) -> None:
    if expected_id is not None and info.profile_id != expected_id:
        raise ProfileMismatchError("the folder contains another profile")


def _register_opened(
    registry: ProfileRegistry, info: ProfileInfo, folder: Path, now: datetime
) -> None:
    """Brings the list to the state "this profile is in this folder, used now", with the
    name read from the database, in a single write (a moved profile or a replaced
    folder are realigned together)."""
    registry.upsert(RegistryEntry(info.profile_id, folder, info.name, now))


# --- List, removal, deletion ---------------------------------------------------------------


class ListProfiles:
    """Lists the known profiles, from the most recently used."""

    def __init__(self, ports: ProfilePorts) -> None:
        self._registry = ports.registry

    def __call__(self) -> Sequence[RegistryEntry]:
        """List entries (cached name, folder, last use).

        Raises:
            ProfileRegistryError: list unreadable or of a future version.
        """
        return self._registry.list_entries()


class PeekProfile:
    """Reads, read-only, the information and the number of activities of a profile in the list.

    Used by the profile list: it does not open the profile, take the lock, migrate or
    write anything (not even the last use), so it works even if the profile is open elsewhere.
    """

    def __init__(self, ports: ProfilePorts) -> None:
        self._registry = ports.registry
        self._storage = ports.storage

    def __call__(self, profile_id: ProfileId) -> ProfilePeek:
        """Information of the profile `profile_id` and number of its activities.

        Raises:
            ProfileNotRegisteredError: id not in the list.
            ProfileNotFoundError: the registered folder no longer has the database.
            ProfileFormatTooNewError, ProfileCorruptError, ProfileStorageError: database not
                readable.
        """
        for entry in self._registry.list_entries():
            if entry.profile_id == profile_id:
                return self._storage.peek(entry.folder)
        raise ProfileNotRegisteredError(str(profile_id))


class RemoveProfileFromList:
    """Removes a profile from the list without touching its folder or its data."""

    def __init__(self, ports: ProfilePorts) -> None:
        self._registry = ports.registry

    def __call__(self, profile_id: ProfileId) -> None:
        """Removes the entry.

        Raises:
            ProfileNotRegisteredError: id not present.
        """
        self._registry.remove(profile_id)


class DeleteProfileFolder:
    """Deletes a profile's folder from the list, with all its data, and the entry."""

    def __init__(self, ports: ProfilePorts) -> None:
        self._registry = ports.registry
        self._folders = ports.folders

    def __call__(self, profile_id: ProfileId) -> None:
        """Deletes the profile folder and removes it from the list.

        The confirmation with folder and content is asked by the interface before calling.

        Raises:
            ProfileNotRegisteredError: id not present.
            ProfileInUseError: the profile is open (even by this instance): close it first.
            NotAProfileFolderError: the folder does not have a profile's database; the entry
                remains (it can be removed from the list).
            FolderHasOtherContentError: there is something else in the folder besides the profile's
            data;
                nothing is deleted and the entry remains.
            FolderUnavailableError: deletion failed or link in the folder.
        """
        entry = next((e for e in self._registry.list_entries() if e.profile_id == profile_id), None)
        if entry is None:
            raise ProfileNotRegisteredError(str(profile_id))
        self._folders.delete_folder(entry.folder)
        self._registry.remove(profile_id)


__all__ = [
    "CreateProfile",
    "DeleteProfileFolder",
    "FolderWarning",
    "ListProfiles",
    "NewProfileRequest",
    "OpenProfileFromFolder",
    "OpenRegisteredProfile",
    "PeekProfile",
    "ProfileMismatchError",
    "ProfilePorts",
    "ProfileSession",
    "RemoveProfileFromList",
    "warnings_for",
]
