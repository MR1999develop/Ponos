# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""The contract tests applied to the in-memory fakes (they verify the fakes, not production).

The real adapters (SQLite, disk, keyring) inherit the same `*Contract` classes.
"""

from __future__ import annotations

import itertools
from pathlib import Path

import pytest

from ponos_coach.activities.domain import ArchivedFile
from ponos_coach.profiles.domain import PROFILE_DATABASE_NAME, FolderKind, ProfileId
from ponos_coach.profiles.ports import (
    ProfileFolders,
    ProfileRegistry,
    ProfileStorage,
    TokenStore,
)
from tests.builders import absolute_path
from tests.contracts.contract_archive import ActivityArchiveContract
from tests.contracts.contract_assessment import (
    PhysiologyRemovalContract,
    ThresholdTestStoreContract,
)
from tests.contracts.contract_nutrition import (
    ActivityCaloriesContract,
    ActivityLevelStoreContract,
    GoalPeriodStoreContract,
    MealTimesStoreContract,
)
from tests.contracts.contract_planning import (
    AvailabilityStoreContract,
    GoalStoreContract,
    PlanStoreContract,
    SessionChoicesStoreContract,
)
from tests.contracts.contract_profiles import ProfileFoldersContract, ProfileRegistryContract
from tests.contracts.contract_storage import (
    NAME,
    PROFILE_ID,
    ActivityIndexContract,
    FixedDataStoreContract,
    LoadCacheContract,
    LoadCacheZonesContract,
    PhysiologyStoreContract,
    ProfileStorageContract,
    SportWeightStoreContract,
    WeightStoreContract,
)
from tests.contracts.contract_sync import ActivityIndexGarminIdContract, SyncJournalContract
from tests.contracts.contract_tokens import TokenStoreContract
from tests.contracts.harness import (
    ArchiveHarness,
    FoldersHarness,
    RegistryHarness,
    StorageHarness,
    TokenStoreHarness,
)
from tests.fakes.archive import InMemoryActivityArchive
from tests.fakes.profile_storage import InMemoryDisk, InMemoryProfileStorage
from tests.fakes.profiles import InMemoryProfileFolders, InMemoryProfileRegistry
from tests.fakes.tokens import InMemoryTokenStore


class _FakeArchiveHarness:
    def __init__(self) -> None:
        self._archive = InMemoryActivityArchive()

    @property
    def archive(self) -> InMemoryActivityArchive:
        return self._archive

    def plant_orphan_temporary(self) -> None:
        self._archive.plant_orphan_temporary()

    def corrupt(self, file: ArchivedFile) -> None:
        data = bytearray(self._archive.files[file.relative_path])
        data[0] ^= 0xFF
        self._archive.files[file.relative_path] = bytes(data)

    def delete(self, file: ArchivedFile) -> None:
        del self._archive.files[file.relative_path]


class _FakeRegistryHarness:
    def __init__(self) -> None:
        self._registry = InMemoryProfileRegistry()

    @property
    def registry(self) -> ProfileRegistry:
        return self._registry

    def reopen(self) -> ProfileRegistry:
        return self._registry  # the medium is the memory of the same object


class _FakeFoldersHarness:
    def __init__(self) -> None:
        self._disk = InMemoryDisk()
        self._folders = InMemoryProfileFolders(self._disk)
        self._root = absolute_path("fake-root")
        self._disk.folders[self._root] = set()

    @property
    def folders(self) -> ProfileFolders:
        return self._folders

    @property
    def root(self) -> Path:
        return self._root

    @property
    def root_kind(self) -> FolderKind:
        return FolderKind.LOCAL

    def put_file(self, folder: Path, name: str) -> None:
        self._disk.folders[folder].add(name)

    def make_profile_folder(self, folder: Path) -> None:
        self._disk.folders[folder].add(PROFILE_DATABASE_NAME)


class _FakeStorageHarness:
    def __init__(self) -> None:
        self._storage = InMemoryProfileStorage()
        self._numbers = itertools.count(1)

    @property
    def storage(self) -> ProfileStorage:
        return self._storage

    def new_folder(self) -> Path:
        folder = absolute_path("fake-profiles", f"p{next(self._numbers)}")
        self._storage.disk.folders[folder] = set()
        return folder


class TestFakeActivityArchive(ActivityArchiveContract):
    @pytest.fixture
    def harness(self) -> ArchiveHarness:
        return _FakeArchiveHarness()


class TestFakeProfileRegistry(ProfileRegistryContract):
    @pytest.fixture
    def harness(self) -> RegistryHarness:
        return _FakeRegistryHarness()


class TestFakeProfileFolders(ProfileFoldersContract):
    @pytest.fixture
    def harness(self) -> FoldersHarness:
        return _FakeFoldersHarness()


class _FakeStorageFixture:
    @pytest.fixture
    def harness(self) -> StorageHarness:
        return _FakeStorageHarness()


class TestFakeProfileStorage(_FakeStorageFixture, ProfileStorageContract):
    pass


class TestFakeActivityIndex(_FakeStorageFixture, ActivityIndexContract):
    pass


class TestFakeActivityIndexGarminIds(_FakeStorageFixture, ActivityIndexGarminIdContract):
    pass


class TestFakeSyncJournal(_FakeStorageFixture, SyncJournalContract):
    pass


class _FakeTokenStoreHarness:
    def __init__(self) -> None:
        self._store = InMemoryTokenStore()

    @property
    def store(self) -> TokenStore:
        return self._store

    def reopen(self) -> TokenStore:
        return self._store  # the medium is the memory of the same object

    def corrupt(self, profile_id: ProfileId) -> None:
        self._store.corrupt(profile_id)


class TestFakeTokenStore(TokenStoreContract):
    @pytest.fixture
    def harness(self) -> TokenStoreHarness:
        return _FakeTokenStoreHarness()


class TestFakePhysiologyStore(_FakeStorageFixture, PhysiologyStoreContract):
    pass


class TestFakeLoadCache(_FakeStorageFixture, LoadCacheContract):
    pass


class TestFakeLoadCacheZones(_FakeStorageFixture, LoadCacheZonesContract):
    pass


class TestFakeWeightStore(_FakeStorageFixture, WeightStoreContract):
    pass


class TestFakeFixedDataStore(_FakeStorageFixture, FixedDataStoreContract):
    pass


class TestFakeSportWeightStore(_FakeStorageFixture, SportWeightStoreContract):
    pass


def test_fake_folders_and_storage_share_one_disk_for_delete() -> None:
    """The folders fake recognizes a profile created by the database fake."""
    storage = InMemoryProfileStorage()
    folders = InMemoryProfileFolders(storage.disk)
    folder = absolute_path("shared")
    folders.create_private_folder(folder)
    storage.create(folder, PROFILE_ID, NAME, "UTC").close()

    folders.delete_folder(folder)

    assert folder not in storage.disk.databases
    assert folder not in storage.disk.folders


class TestFakeActivityCalories(_FakeStorageFixture, ActivityCaloriesContract):
    pass


class TestFakeActivityLevelStore(_FakeStorageFixture, ActivityLevelStoreContract):
    pass


class TestFakeMealTimesStore(_FakeStorageFixture, MealTimesStoreContract):
    pass


class TestFakeGoalPeriodStore(_FakeStorageFixture, GoalPeriodStoreContract):
    pass


class TestFakeThresholdTestStore(_FakeStorageFixture, ThresholdTestStoreContract):
    pass


class TestFakePhysiologyRemoval(_FakeStorageFixture, PhysiologyRemovalContract):
    pass


class TestFakeGoalStore(_FakeStorageFixture, GoalStoreContract):
    pass


class TestFakeAvailabilityStore(_FakeStorageFixture, AvailabilityStoreContract):
    pass


class TestFakeSessionChoicesStore(_FakeStorageFixture, SessionChoicesStoreContract):
    pass


class TestFakePlanStore(_FakeStorageFixture, PlanStoreContract):
    pass
