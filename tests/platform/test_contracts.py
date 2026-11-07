# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""The `ProfileRegistry` and `ProfileFolders` contracts applied to the real adapters."""

from __future__ import annotations

from pathlib import Path

import pytest

from ponos_coach.platform.profile_folders import LocalProfileFolders
from ponos_coach.platform.profile_registry import JsonProfileRegistry
from ponos_coach.profiles.domain import PROFILE_DATABASE_NAME, FolderKind
from ponos_coach.profiles.ports import ProfileFolders, ProfileRegistry
from tests.contracts.contract_profiles import ProfileFoldersContract, ProfileRegistryContract
from tests.contracts.harness import FoldersHarness, RegistryHarness


class _RealRegistryHarness:
    def __init__(self, config_dir: Path) -> None:
        self._config_dir = config_dir
        self._registry = JsonProfileRegistry(config_dir)

    @property
    def registry(self) -> ProfileRegistry:
        return self._registry

    def reopen(self) -> ProfileRegistry:
        return JsonProfileRegistry(self._config_dir)


class _RealFoldersHarness:
    def __init__(self, root: Path) -> None:
        root.mkdir()
        self._root = root
        # Fixed kind: real detection depends on the machine and has its own tests.
        self._folders = LocalProfileFolders(lambda _folder: FolderKind.LOCAL)

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
        (folder / name).write_bytes(b"x")

    def make_profile_folder(self, folder: Path) -> None:
        (folder / PROFILE_DATABASE_NAME).write_bytes(b"SQLite format 3\x00")


class TestJsonProfileRegistry(ProfileRegistryContract):
    @pytest.fixture
    def harness(self, tmp_path: Path) -> RegistryHarness:
        return _RealRegistryHarness(tmp_path / "config")


class TestLocalProfileFolders(ProfileFoldersContract):
    @pytest.fixture
    def harness(self, tmp_path: Path) -> FoldersHarness:
        return _RealFoldersHarness(tmp_path / "root")
