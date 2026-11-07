# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""T-SEC-09: the profile folder is private (explicit descriptor on Windows, 0700 on POSIX)."""

from __future__ import annotations

import stat
import sys
from pathlib import Path

import pytest

from ponos_coach.platform import windows_api
from ponos_coach.platform.profile_folders import LocalProfileFolders
from ponos_coach.profiles.domain import PROFILE_LOCK_NAME, FolderKind
from ponos_coach.profiles.ports import FolderNotEmptyError, FolderUnavailableError
from tests.platform.windows_dacl import dacl_sddl, is_protected, normalize_sid, trustees

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="Windows permissions (DACL)")
posix_only = pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions")

_BROAD_ALIASES = (
    "WD",
    "BU",
    "AU",
    "IU",
    "NU",
    "AN",
    "LG",
    "BA",
)  # Everyone, Users, Auth., Admins...


@pytest.fixture
def folders() -> LocalProfileFolders:
    return LocalProfileFolders(lambda _folder: FolderKind.LOCAL)


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def _private_trustees() -> list[str]:
    return sorted([normalize_sid(windows_api.current_user_sid()), normalize_sid("SY")])


def _broad_trustees() -> set[str]:
    return {normalize_sid(alias) for alias in _BROAD_ALIASES}


@windows_only
def test_windows_folder_has_a_protected_dacl_for_the_current_user_and_system_only(
    folders: LocalProfileFolders, tmp_path: Path
) -> None:
    folder = tmp_path / "profilo"

    folders.create_private_folder(folder)

    sddl = dacl_sddl(folder)
    assert is_protected(sddl), sddl
    assert sorted(trustees(sddl)) == _private_trustees(), sddl
    assert not _broad_trustees() & set(trustees(sddl))


@windows_only
def test_windows_files_created_inside_inherit_only_the_private_entries(
    folders: LocalProfileFolders, tmp_path: Path
) -> None:
    folder = tmp_path / "profilo"
    folders.create_private_folder(folder)

    (folder / "ponos.sqlite3").write_bytes(b"x")
    (folder / "archivio").mkdir()

    for child in (folder / "ponos.sqlite3", folder / "archivio"):
        assert sorted(trustees(dacl_sddl(child))) == _private_trustees()


@windows_only
def test_windows_missing_parents_are_private_too(
    folders: LocalProfileFolders, tmp_path: Path
) -> None:
    folders.create_private_folder(tmp_path / "a" / "b" / "profilo")

    assert sorted(trustees(dacl_sddl(tmp_path / "a"))) == _private_trustees()
    assert sorted(trustees(dacl_sddl(tmp_path / "a" / "b"))) == _private_trustees()


@windows_only
def test_windows_an_existing_empty_folder_gets_the_private_dacl(
    folders: LocalProfileFolders, tmp_path: Path
) -> None:
    folder = tmp_path / "esistente"
    folder.mkdir()

    folders.create_private_folder(folder)

    sddl = dacl_sddl(folder)
    assert is_protected(sddl), sddl
    assert sorted(trustees(sddl)) == _private_trustees(), sddl


@windows_only
def test_windows_create_directory_refuses_an_existing_one(tmp_path: Path) -> None:
    with pytest.raises(FileExistsError):
        windows_api.create_private_directory(tmp_path)


@posix_only
def test_posix_folder_and_missing_parents_are_0700(
    folders: LocalProfileFolders, tmp_path: Path
) -> None:
    folders.create_private_folder(tmp_path / "a" / "b" / "profilo")

    for path in (tmp_path / "a", tmp_path / "a" / "b", tmp_path / "a" / "b" / "profilo"):
        assert _mode(path) == 0o700


@posix_only
def test_posix_an_existing_empty_folder_is_restricted_to_0700(
    folders: LocalProfileFolders, tmp_path: Path
) -> None:
    folder = tmp_path / "esistente"
    folder.mkdir(mode=0o755)
    folder.chmod(0o755)

    folders.create_private_folder(folder)

    assert _mode(folder) == 0o700


@posix_only
def test_posix_lock_file_is_0600(folders: LocalProfileFolders, tmp_path: Path) -> None:
    folder = tmp_path / "profilo"
    folders.create_private_folder(folder)

    folders.acquire_lock(folder).release()

    assert _mode(folder / PROFILE_LOCK_NAME) == 0o600


@windows_only
def test_windows_a_non_empty_folder_keeps_its_descriptor(
    folders: LocalProfileFolders, tmp_path: Path
) -> None:
    folder = tmp_path / "pieno"
    folder.mkdir()
    (folder / "nota.txt").write_bytes(b"x")
    before = dacl_sddl(folder)

    with pytest.raises(FolderNotEmptyError):
        folders.create_private_folder(folder)

    assert dacl_sddl(folder) == before


@posix_only
def test_posix_a_non_empty_folder_keeps_its_mode(
    folders: LocalProfileFolders, tmp_path: Path
) -> None:
    folder = tmp_path / "pieno"
    folder.mkdir()
    folder.chmod(0o755)
    (folder / "nota.txt").write_bytes(b"x")

    with pytest.raises(FolderNotEmptyError):
        folders.create_private_folder(folder)

    assert _mode(folder) == 0o755


def test_creating_a_folder_over_a_regular_file_fails_cleanly(
    folders: LocalProfileFolders, tmp_path: Path
) -> None:
    target = tmp_path / "file"
    target.write_bytes(b"x")

    with pytest.raises(FolderUnavailableError):
        folders.create_private_folder(target)

    assert target.read_bytes() == b"x"


def test_relative_paths_are_refused(folders: LocalProfileFolders) -> None:
    with pytest.raises(FolderUnavailableError):
        folders.create_private_folder(Path("relativa"))
