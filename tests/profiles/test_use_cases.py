# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Profile use cases, with the in-memory fakes of `tests/fakes/`."""

from __future__ import annotations

import itertools
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from ponos_coach.activities.ports import ActivityIndex, SyncJournal, TrackBoxIndex
from ponos_coach.assessment.ports import ThresholdTestStore
from ponos_coach.goals.ports import AvailabilityStore, GoalStore, SessionChoicesStore
from ponos_coach.injury.ports import InjuryStore
from ponos_coach.load.domain import PhysiologyEntry
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
    FolderKind,
    ProfileId,
    ProfileInfo,
    ProfileName,
    RegistryEntry,
)
from ponos_coach.profiles.ports import (
    FixedDataStore,
    FolderNotEmptyError,
    FolderUnavailableError,
    NotAProfileFolderError,
    OpenProfile,
    ProfileAlreadyExistsError,
    ProfileInUseError,
    ProfileNotFoundError,
    ProfileNotRegisteredError,
    ProfileRegistryError,
    ProfileStorageError,
    ThresholdPaceStore,
    WeightStore,
)
from ponos_coach.profiles.use_cases import (
    CreateProfile,
    DeleteProfileFolder,
    FolderWarning,
    ListProfiles,
    NewProfileRequest,
    OpenProfileFromFolder,
    OpenRegisteredProfile,
    PeekProfile,
    ProfileMismatchError,
    ProfilePorts,
    ProfileSession,
    RemoveProfileFromList,
)
from tests.builders import absolute_path, physiology, registry_entry
from tests.fakes.profile_storage import InMemoryDisk, InMemoryProfileStorage
from tests.fakes.profiles import InMemoryLock, InMemoryProfileFolders, InMemoryProfileRegistry

NOW = datetime(2026, 11, 5, 9, 30, tzinfo=UTC)
TODAY = date(2026, 11, 5)
NAME = ProfileName.parse("Atleta di prova")


class _FailingRegistry(InMemoryProfileRegistry):
    """List that cannot write new entries."""

    def add(self, entry: RegistryEntry) -> None:  # noqa: ARG002
        raise ProfileRegistryError("disco pieno")

    def upsert(self, entry: RegistryEntry) -> None:  # noqa: ARG002
        raise ProfileRegistryError("disco pieno")


class _FailingStorage(InMemoryProfileStorage):
    """Database that cannot be created."""

    def create(
        self,
        folder: Path,
        profile_id: ProfileId,
        name: ProfileName,
        timezone_name: str,
    ) -> OpenProfile:
        del folder, profile_id, name, timezone_name
        raise ProfileStorageError("disco pieno")


class _CountingStorage(InMemoryProfileStorage):
    """Database that counts closes, to prove the use cases do not forget them."""

    def __init__(self, disk: InMemoryDisk) -> None:
        super().__init__(disk)
        self.closed = 0
        self.opened = 0

    def create(
        self, folder: Path, profile_id: ProfileId, name: ProfileName, timezone_name: str
    ) -> OpenProfile:
        return self._counted(super().create(folder, profile_id, name, timezone_name))

    def open(self, folder: Path, *, check_integrity: bool = True) -> OpenProfile:
        return self._counted(super().open(folder, check_integrity=check_integrity))

    def _counted(self, opened: OpenProfile) -> OpenProfile:
        self.opened += 1
        return _CountingProfile(opened, self)


class _CountingProfile:
    def __init__(self, inner: OpenProfile, owner: _CountingStorage) -> None:
        self._inner = inner
        self._owner = owner

    @property
    def info(self) -> ProfileInfo:
        return self._inner.info

    @property
    def removed_unsupported_activities(self) -> int:
        return self._inner.removed_unsupported_activities

    @property
    def activities(self) -> ActivityIndex:
        return self._inner.activities

    @property
    def track_boxes(self) -> TrackBoxIndex:
        return self._inner.track_boxes

    @property
    def sync_journal(self) -> SyncJournal:
        return self._inner.sync_journal

    @property
    def physiology(self) -> PhysiologyStore:
        return self._inner.physiology

    @property
    def weights(self) -> WeightStore:
        return self._inner.weights

    @property
    def threshold_paces(self) -> ThresholdPaceStore:
        return self._inner.threshold_paces

    @property
    def fixed_data(self) -> FixedDataStore:
        return self._inner.fixed_data

    @property
    def load_cache(self) -> LoadCache:
        return self._inner.load_cache

    @property
    def activity_levels(self) -> ActivityLevelStore:
        return self._inner.activity_levels

    @property
    def lean_masses(self) -> LeanMassStore:
        return self._inner.lean_masses

    @property
    def meal_times(self) -> MealTimesStore:
        return self._inner.meal_times

    @property
    def goal_periods(self) -> GoalPeriodStore:
        return self._inner.goal_periods

    @property
    def threshold_tests(self) -> ThresholdTestStore:
        return self._inner.threshold_tests

    @property
    def goals(self) -> GoalStore:
        return self._inner.goals

    @property
    def availability(self) -> AvailabilityStore:
        return self._inner.availability

    @property
    def session_choices(self) -> SessionChoicesStore:
        return self._inner.session_choices

    @property
    def plans(self) -> PlanStore:
        return self._inner.plans

    @property
    def workout_backups(self) -> WorkoutBackupStore:
        return self._inner.workout_backups

    @property
    def injuries(self) -> InjuryStore:
        return self._inner.injuries

    def rename(self, name: ProfileName) -> None:
        self._inner.rename(name)

    def close(self) -> None:
        self._owner.closed += 1
        self._inner.close()


@dataclass
class World:
    """Fakes, ready-built use cases and helpers for the checks."""

    disk: InMemoryDisk = field(default_factory=InMemoryDisk)
    registry: InMemoryProfileRegistry = field(default_factory=InMemoryProfileRegistry)
    storage_class: type[InMemoryProfileStorage] = InMemoryProfileStorage
    ids: itertools.count[int] = field(default_factory=lambda: itertools.count(1))

    def ports(self, storage: InMemoryProfileStorage | None = None) -> ProfilePorts:
        return ProfilePorts(
            self.registry,
            InMemoryProfileFolders(self.disk),
            storage or self.storage_class(self.disk),
        )

    def create(self, ports: ProfilePorts | None = None) -> CreateProfile:
        return CreateProfile(ports or self.ports(), lambda: NOW, self._new_id)

    def _new_id(self) -> ProfileId:
        return ProfileId(uuid.UUID(f"00000000-0000-4000-8000-{next(self.ids):012d}"))


@pytest.fixture
def world() -> World:
    return World()


def request(folder: Path | None = None, name: str = "Atleta di prova") -> NewProfileRequest:
    return NewProfileRequest(
        ProfileName.parse(name),
        folder or absolute_path("profili", "atleta"),
        "Europe/Rome",
        physiology(),
        TODAY,
    )


# --- Create ----------------------------------------------------------------------------------


def test_create_makes_folder_database_parameters_and_registry_entry(world: World) -> None:
    folder = absolute_path("profili", "atleta")

    with world.create()(request(folder)) as session:
        assert session.info.name == NAME
        assert session.info.timezone_name == "Europe/Rome"
        assert session.folder == folder
        assert session.warnings == ()
        assert list(session.physiology.list_entries()) == [PhysiologyEntry(TODAY, physiology())]
        assert folder in world.disk.locks

    [entry] = world.registry.list_entries()
    assert (entry.profile_id, entry.folder, entry.cached_name, entry.last_used_utc) == (
        session.info.profile_id,
        folder,
        NAME,
        NOW,
    )
    assert folder not in world.disk.locks


def test_create_in_an_existing_empty_folder_reuses_it(world: World) -> None:
    folder = absolute_path("profili", "vuota")
    world.disk.folders[folder] = set()

    world.create()(request(folder)).close()

    assert PROFILE_DATABASE_NAME in world.disk.folders[folder]


def test_create_in_a_non_empty_folder_is_refused_and_touches_nothing(world: World) -> None:
    folder = absolute_path("profili", "piena")
    world.disk.folders[folder] = {"tesi.docx"}

    with pytest.raises(FolderNotEmptyError):
        world.create()(request(folder))

    assert world.disk.folders[folder] == {"tesi.docx"}
    assert world.registry.list_entries() == []
    assert folder not in world.disk.locks


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        (FolderKind.NETWORK, (FolderWarning.NETWORK,)),
        (FolderKind.CLOUD_SYNCED, (FolderWarning.CLOUD_SYNCED,)),
        (FolderKind.LOCAL, ()),
        (FolderKind.UNKNOWN, ()),
    ],
)
def test_create_reports_warnings_for_the_kind_of_place_without_blocking(
    world: World, kind: FolderKind, expected: tuple[FolderWarning, ...]
) -> None:
    folder = absolute_path("profili", "p")
    world.disk.kinds[folder.parent] = kind

    session = world.create()(request(folder))

    assert session.warnings == expected
    session.close()


def test_create_failing_on_the_registry_leaves_no_entry_no_folder_and_no_lock(
    world: World,
) -> None:
    folder = absolute_path("profili", "atleta")
    world.registry = _FailingRegistry()

    with pytest.raises(ProfileRegistryError):
        world.create()(request(folder))

    assert world.registry.list_entries() == []
    assert folder not in world.disk.folders
    assert folder not in world.disk.databases
    assert folder not in world.disk.locks


def test_create_failing_on_the_registry_closes_the_database() -> None:
    disk = InMemoryDisk()
    storage = _CountingStorage(disk)
    world = World(disk=disk, registry=_FailingRegistry())

    with pytest.raises(ProfileRegistryError):
        world.create(world.ports(storage))(request())

    assert (storage.opened, storage.closed) == (1, 1)


def test_create_failing_on_the_database_leaves_no_entry_and_no_lock(world: World) -> None:
    folder = absolute_path("profili", "atleta")
    ports = world.ports(_FailingStorage(world.disk))

    with pytest.raises(ProfileStorageError):
        world.create(ports)(request(folder))

    assert world.registry.list_entries() == []
    assert folder not in world.disk.locks
    assert folder not in world.disk.folders  # the empty folder created earlier is removed too


def test_create_with_an_unknown_timezone_is_refused_before_touching_the_disk(
    world: World,
) -> None:
    folder = absolute_path("profili", "atleta")
    bad = NewProfileRequest(NAME, folder, "Marte/Olympus", physiology(), TODAY)

    with pytest.raises(ValueError, match="time zone"):
        world.create()(bad)

    assert world.registry.list_entries() == []
    assert folder not in world.disk.folders


class _LosingTheRace(InMemoryProfileStorage):
    """Another instance puts its database in the folder just before ours."""

    def create(
        self, folder: Path, profile_id: ProfileId, name: ProfileName, timezone_name: str
    ) -> OpenProfile:
        InMemoryProfileStorage(self.disk).create(
            folder, ProfileId.parse("99999999-9999-4999-8999-999999999999"), NAME, "UTC"
        ).close()
        return super().create(folder, profile_id, name, timezone_name)


def test_losing_the_creation_race_never_deletes_the_winners_database(world: World) -> None:
    folder = absolute_path("profili", "atleta")

    with pytest.raises(ProfileAlreadyExistsError):
        world.create(world.ports(_LosingTheRace(world.disk)))(request(folder))

    assert folder in world.disk.databases  # the winner's profile is safe
    assert PROFILE_DATABASE_NAME in world.disk.folders[folder]
    assert world.registry.list_entries() == []
    assert folder not in world.disk.locks


def test_a_request_needs_an_absolute_folder() -> None:
    with pytest.raises(ValueError, match="absolute"):
        NewProfileRequest(NAME, Path("relativa"), "UTC", physiology(), TODAY)


def test_closing_a_session_twice_is_harmless_and_releases_the_lock_once(world: World) -> None:
    session = world.create()(request())

    session.close()
    session.close()

    assert world.disk.locks == set()


class _BrokenClose(_CountingProfile):
    def close(self) -> None:
        raise ProfileStorageError("flush failed")


class _RecordingLock:
    def __init__(self) -> None:
        self.released = 0

    def release(self) -> None:
        self.released += 1


def test_closing_a_session_releases_the_lock_even_when_the_database_fails_to_close(
    world: World,
) -> None:
    session = world.create()(request())
    session.close()
    storage = _CountingStorage(world.disk)
    lock = _RecordingLock()
    broken = ProfileSession(
        _BrokenClose(storage.open(session.folder), storage), lock, session.folder, ()
    )

    with pytest.raises(ProfileStorageError):
        broken.close()
    broken.close()  # already closed: no second attempt

    assert lock.released == 1


# --- Open ----------------------------------------------------------------------------------


def _created(world: World, folder: Path | None = None, name: str = "Atleta di prova") -> ProfileId:
    session = world.create()(request(folder, name))
    session.close()
    return session.info.profile_id


def _open_from_folder(world: World) -> OpenProfileFromFolder:
    return OpenProfileFromFolder(world.ports(), lambda: NOW + timedelta(hours=1))


def _open_registered(world: World) -> OpenRegisteredProfile:
    return OpenRegisteredProfile(world.ports(), lambda: NOW + timedelta(hours=1))


def test_open_from_folder_registers_an_unknown_profile_with_the_name_in_its_database(
    world: World,
) -> None:
    folder = absolute_path("profili", "atleta")
    profile_id = _created(world, folder)
    world.registry.remove(profile_id)  # like a profile copied from another PC

    with _open_from_folder(world)(folder) as session:
        assert session.info.profile_id == profile_id

    [entry] = world.registry.list_entries()
    assert (entry.profile_id, entry.folder, entry.cached_name) == (profile_id, folder, NAME)
    assert entry.last_used_utc == NOW + timedelta(hours=1)


def test_open_from_folder_marks_a_known_profile_as_used(world: World) -> None:
    folder = absolute_path("profili", "atleta")
    _created(world, folder)

    _open_from_folder(world)(folder).close()

    [entry] = world.registry.list_entries()
    assert entry.last_used_utc == NOW + timedelta(hours=1)


def test_open_from_folder_refreshes_a_stale_cached_name(world: World) -> None:
    folder = absolute_path("profili", "atleta")
    profile_id = _created(world, folder)
    world.registry.remove(profile_id)
    world.registry.add(RegistryEntry(profile_id, folder, ProfileName.parse("Vecchio nome"), NOW))

    _open_from_folder(world)(folder).close()

    [entry] = world.registry.list_entries()
    assert entry.cached_name == NAME


def test_open_from_folder_repoints_a_profile_that_was_moved(world: World) -> None:
    folder = absolute_path("profili", "nuova-posizione")
    profile_id = _created(world, folder)
    world.registry.remove(profile_id)
    world.registry.add(
        RegistryEntry(profile_id, absolute_path("profili", "vecchia-posizione"), NAME, NOW)
    )

    _open_from_folder(world)(folder).close()

    [entry] = world.registry.list_entries()
    assert (entry.profile_id, entry.folder) == (profile_id, folder)


def test_open_from_folder_replaces_a_different_profile_registered_for_that_folder(
    world: World,
) -> None:
    folder = absolute_path("profili", "atleta")
    profile_id = _created(world, folder)
    world.registry.remove(profile_id)
    stranger = registry_entry(9, folder=folder)
    world.registry.add(stranger)

    _open_from_folder(world)(folder).close()

    assert [e.profile_id for e in world.registry.list_entries()] == [profile_id]


def test_a_second_open_of_the_same_profile_is_refused_until_the_first_is_closed(
    world: World,
) -> None:
    folder = absolute_path("profili", "atleta")
    _created(world, folder)
    first = _open_from_folder(world)(folder)

    with pytest.raises(ProfileInUseError):
        _open_from_folder(world)(folder)

    first.close()
    _open_from_folder(world)(folder).close()


@pytest.mark.parametrize("contents", [None, set()], ids=["missing", "empty"])
def test_open_from_a_folder_without_a_profile_fails_without_taking_the_lock(
    world: World, contents: set[str] | None
) -> None:
    folder = absolute_path("profili", "niente")
    if contents is not None:
        world.disk.folders[folder] = contents

    with pytest.raises(ProfileNotFoundError):
        _open_from_folder(world)(folder)

    assert world.disk.locks == set()
    assert world.registry.list_entries() == []


class _LockCountingFolders(InMemoryProfileFolders):
    """Folders that count the locks taken."""

    locks_taken = 0

    def acquire_lock(self, folder: Path) -> InMemoryLock:
        self.locks_taken += 1
        return super().acquire_lock(folder)


def test_open_from_a_non_empty_folder_that_is_not_a_profile_never_takes_the_lock(
    world: World,
) -> None:
    folder = absolute_path("profili", "documenti")
    world.disk.folders[folder] = {"tesi.docx"}
    folders = _LockCountingFolders(world.disk)
    ports = ProfilePorts(world.registry, folders, InMemoryProfileStorage(world.disk))

    with pytest.raises(NotAProfileFolderError):
        OpenProfileFromFolder(ports, lambda: NOW)(folder)

    assert folders.locks_taken == 0
    assert world.registry.list_entries() == []


def test_open_registered_of_a_non_empty_folder_that_is_not_a_profile_is_refused(
    world: World,
) -> None:
    folder = absolute_path("profili", "documenti")
    world.disk.folders[folder] = {"tesi.docx"}
    entry = registry_entry(1, folder=folder)
    world.registry.add(entry)

    with pytest.raises(NotAProfileFolderError):
        _open_registered(world)(entry.profile_id)

    assert world.disk.locks == set()


def test_open_from_folder_reports_the_warnings_of_the_place(world: World) -> None:
    folder = absolute_path("profili", "atleta")
    _created(world, folder)
    world.disk.kinds[folder] = FolderKind.CLOUD_SYNCED

    with _open_from_folder(world)(folder) as session:
        assert session.warnings == (FolderWarning.CLOUD_SYNCED,)


def test_open_failing_on_the_registry_closes_everything() -> None:
    disk = InMemoryDisk()
    storage = _CountingStorage(disk)
    world = World(disk=disk)
    folder = absolute_path("profili", "atleta")
    world.create(world.ports(storage))(request(folder)).close()
    world.registry.remove(world.registry.list_entries()[0].profile_id)
    world.registry = _FailingRegistry()
    storage.closed = storage.opened = 0

    with pytest.raises(ProfileRegistryError):
        OpenProfileFromFolder(world.ports(storage), lambda: NOW)(folder)

    assert (storage.opened, storage.closed) == (1, 1)
    assert disk.locks == set()


def test_open_failing_on_the_registry_leaves_the_old_entries_as_they_were() -> None:
    disk = InMemoryDisk()
    registry = _FailingRegistry()
    world = World(disk=disk, registry=registry)
    folder = absolute_path("profili", "atleta")
    profile_id = _created(World(disk=disk), folder)
    old = RegistryEntry(profile_id, absolute_path("profili", "vecchia-posizione"), NAME, NOW)
    InMemoryProfileRegistry.add(registry, old)

    with pytest.raises(ProfileRegistryError):
        _open_from_folder(world)(folder)

    assert registry.list_entries() == [old]
    assert disk.locks == set()


def test_a_relative_folder_is_refused(world: World) -> None:
    with pytest.raises(FolderUnavailableError):
        _open_from_folder(world)(Path("relativa"))


def test_open_registered_opens_by_id_and_marks_it_used(world: World) -> None:
    profile_id = _created(world)

    with _open_registered(world)(profile_id) as session:
        assert session.info.profile_id == profile_id

    assert world.registry.list_entries()[0].last_used_utc == NOW + timedelta(hours=1)


def test_open_registered_of_an_unknown_id_fails(world: World) -> None:
    with pytest.raises(ProfileNotRegisteredError):
        _open_registered(world)(registry_entry(5).profile_id)


def test_open_registered_refuses_a_folder_that_now_holds_another_profile(world: World) -> None:
    folder = absolute_path("profili", "atleta")
    real_id = _created(world, folder)
    world.registry.remove(real_id)
    impostor = registry_entry(9, folder=folder)
    world.registry.add(impostor)

    with pytest.raises(ProfileMismatchError):
        _open_registered(world)(impostor.profile_id)

    assert world.registry.list_entries() == [impostor]
    assert world.disk.locks == set()


def test_open_registered_of_a_vanished_folder_fails(world: World) -> None:
    entry = registry_entry(3)
    world.registry.add(entry)

    with pytest.raises(ProfileNotFoundError):
        _open_registered(world)(entry.profile_id)

    assert world.registry.list_entries() == [entry]


# --- List, removal, deletion ------------------------------------------------------


def test_list_profiles_starts_from_the_most_recently_used(world: World) -> None:
    older = registry_entry(1, last_used=NOW - timedelta(days=2))
    newer = registry_entry(2, last_used=NOW)
    world.registry.add(older)
    world.registry.add(newer)

    assert list(ListProfiles(world.ports())()) == [newer, older]


def test_remove_from_list_keeps_the_folder_and_its_data(world: World) -> None:
    folder = absolute_path("profili", "atleta")
    profile_id = _created(world, folder)

    RemoveProfileFromList(world.ports())(profile_id)

    assert world.registry.list_entries() == []
    assert PROFILE_DATABASE_NAME in world.disk.folders[folder]
    assert folder in world.disk.databases


def test_remove_from_list_of_an_unknown_profile_fails(world: World) -> None:
    with pytest.raises(ProfileNotRegisteredError):
        RemoveProfileFromList(world.ports())(registry_entry(4).profile_id)


def test_delete_folder_removes_the_data_and_the_entry(world: World) -> None:
    folder = absolute_path("profili", "atleta")
    profile_id = _created(world, folder)

    DeleteProfileFolder(world.ports())(profile_id)

    assert folder not in world.disk.folders
    assert world.registry.list_entries() == []


def test_delete_folder_of_an_open_profile_is_refused_and_changes_nothing(world: World) -> None:
    folder = absolute_path("profili", "atleta")
    session = world.create()(request(folder))

    with pytest.raises(ProfileInUseError):
        DeleteProfileFolder(world.ports())(session.info.profile_id)

    assert folder in world.disk.databases
    assert len(world.registry.list_entries()) == 1
    session.close()


def test_delete_folder_of_an_unknown_profile_fails(world: World) -> None:
    with pytest.raises(ProfileNotRegisteredError):
        DeleteProfileFolder(world.ports())(registry_entry(4).profile_id)


def test_delete_folder_that_is_not_a_profile_keeps_the_entry_and_the_files(world: World) -> None:
    entry = registry_entry(6, folder=absolute_path("profili", "documenti"))
    world.registry.add(entry)
    world.disk.folders[entry.folder] = {"tesi.docx"}

    with pytest.raises(NotAProfileFolderError):
        DeleteProfileFolder(world.ports())(entry.profile_id)

    assert world.disk.folders[entry.folder] == {"tesi.docx"}
    assert world.registry.list_entries() == [entry]


# --- Peek (read-only) -----------------------------------------------------------------


def test_peek_reports_the_info_and_the_count_without_locking_or_marking_use(world: World) -> None:
    folder = absolute_path("profili", "atleta")
    profile_id = _created(world, folder)
    before = world.registry.list_entries()

    peeked = PeekProfile(world.ports())(profile_id)

    assert peeked.info.profile_id == profile_id
    assert peeked.info.name == NAME
    assert peeked.activity_count == 0
    assert world.registry.list_entries() == before
    assert world.disk.locks == set()


def test_peek_works_on_a_profile_that_is_open(world: World) -> None:
    profile_id = _created(world)
    with _open_registered(world)(profile_id):
        assert PeekProfile(world.ports())(profile_id).info.profile_id == profile_id


def test_peek_of_an_unknown_profile_fails(world: World) -> None:
    with pytest.raises(ProfileNotRegisteredError):
        PeekProfile(world.ports())(registry_entry(7).profile_id)


def test_peek_of_a_vanished_folder_fails(world: World) -> None:
    entry = registry_entry(1)
    world.registry.add(entry)

    with pytest.raises(ProfileNotFoundError):
        PeekProfile(world.ports())(entry.profile_id)
