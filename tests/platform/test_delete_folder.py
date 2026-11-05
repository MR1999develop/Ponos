# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Cancellazione della cartella di un profilo: mai attraverso link o giunzioni."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from ponos_coach.platform.profile_folders import LocalProfileFolders
from ponos_coach.profiles.domain import PROFILE_DATABASE_NAME, FolderKind
from ponos_coach.profiles.ports import FolderUnavailableError

_IS_WINDOWS: bool = sys.platform == "win32"  # bool, non Literal: mypy non scarta un ramo


@pytest.fixture
def folders() -> LocalProfileFolders:
    return LocalProfileFolders(lambda _folder: FolderKind.LOCAL)


@pytest.fixture
def profile(tmp_path: Path, folders: LocalProfileFolders) -> Path:
    """Cartella di profilo con database, archivio annidato, un file di sola lettura e un -wal."""
    folder = tmp_path / "profilo"
    folders.create_private_folder(folder)
    (folder / PROFILE_DATABASE_NAME).write_bytes(b"db")
    (folder / f"{PROFILE_DATABASE_NAME}-wal").write_bytes(b"wal")
    archive = folder / "archivio" / "2026"
    archive.mkdir(parents=True)
    (archive / "a.fit").write_bytes(b"fit")
    readonly = folder / "backup"
    readonly.mkdir()
    (readonly / "vecchio.zip").write_bytes(b"zip")
    (readonly / "vecchio.zip").chmod(0o444)
    return folder


@pytest.fixture
def outside(tmp_path: Path) -> Path:
    """Cartella estranea con un file che nessuna cancellazione deve toccare."""
    folder = tmp_path / "estranea"
    folder.mkdir()
    (folder / "preziosa.txt").write_bytes(b"da non perdere")
    return folder


def _make_directory_link(link: Path, target: Path) -> None:
    """Giunzione su Windows (non richiede privilegi), link simbolico altrove."""
    if _IS_WINDOWS:
        result = subprocess.run(  # noqa: S603 - comando fisso, percorsi come argomenti
            ["cmd.exe", "/c", "mklink", "/J", str(link), str(target)],  # noqa: S607
            capture_output=True,
            check=False,
            timeout=30,
        )
        if result.returncode != 0:
            pytest.skip("giunzione non creabile qui")
        return
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("link simbolici non creabili qui")


def test_whole_tree_is_removed_including_readonly_files_and_the_lock_file(
    folders: LocalProfileFolders, profile: Path
) -> None:
    folders.delete_folder(profile)

    assert not profile.exists()


def test_a_junction_or_symlink_to_a_folder_inside_blocks_the_deletion_and_is_not_followed(
    folders: LocalProfileFolders, profile: Path, outside: Path
) -> None:
    _make_directory_link(profile / "archivio" / "collegamento", outside)

    with pytest.raises(FolderUnavailableError):
        folders.delete_folder(profile)

    assert (outside / "preziosa.txt").read_bytes() == b"da non perdere"
    assert (profile / PROFILE_DATABASE_NAME).exists()  # nulla è stato cancellato
    assert (profile / "archivio" / "2026" / "a.fit").exists()


def test_a_file_symlink_inside_blocks_the_deletion(
    folders: LocalProfileFolders, profile: Path, outside: Path
) -> None:
    try:
        (profile / "file-link").symlink_to(outside / "preziosa.txt")
    except OSError:
        pytest.skip("link simbolici non creabili qui")

    with pytest.raises(FolderUnavailableError):
        folders.delete_folder(profile)

    assert (outside / "preziosa.txt").exists()
    assert (profile / PROFILE_DATABASE_NAME).exists()


def test_a_profile_folder_that_is_itself_a_link_is_not_deleted(
    folders: LocalProfileFolders, profile: Path, tmp_path: Path
) -> None:
    link = tmp_path / "scorciatoia"
    _make_directory_link(link, profile)

    with pytest.raises(FolderUnavailableError):
        folders.delete_folder(link)

    assert (profile / PROFILE_DATABASE_NAME).exists()


# --- remove_if_empty: mai attraverso link ------------------------------------------------


def test_remove_if_empty_does_not_follow_a_link_to_an_empty_folder(
    folders: LocalProfileFolders, tmp_path: Path
) -> None:
    empty = tmp_path / "vuota"
    folders.create_private_folder(empty)
    link = tmp_path / "scorciatoia"
    _make_directory_link(link, empty)

    assert not folders.remove_if_empty(link)

    assert empty.is_dir()


def test_remove_if_empty_keeps_a_folder_whose_only_content_is_a_link(
    folders: LocalProfileFolders, tmp_path: Path, outside: Path
) -> None:
    folder = tmp_path / "con-link"
    folders.create_private_folder(folder)
    _make_directory_link(folder / "collegamento", outside)

    assert not folders.remove_if_empty(folder)

    assert (outside / "preziosa.txt").read_bytes() == b"da non perdere"
    assert folder.is_dir()
