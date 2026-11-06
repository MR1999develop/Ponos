# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Riconoscimento di cartelle di rete e sincronizzate (logica pura, provata su ogni sistema)."""

from __future__ import annotations

import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath

import pytest

from ponos_coach.platform import folder_kind, windows_api
from ponos_coach.platform.atomic_file import FileTooLargeError
from ponos_coach.platform.folder_kind import (
    SystemFolderKindDetector,
    cloud_roots,
    kind_from_mounts,
    nearest_existing,
    parse_mountinfo,
    windows_kind,
)
from ponos_coach.profiles.domain import FolderKind

_BS = chr(92)  # backslash

_MOUNTINFO = r"""
22 28 0:21 / /proc rw,nosuid - proc proc rw
28 1 8:2 / / rw,relatime shared:1 - ext4 /dev/sda2 rw
41 28 0:35 / /mnt/nas rw,relatime shared:5 - nfs4 nas:/export rw,vers=4.2
42 28 0:36 / /mnt/win\040share rw,relatime - cifs //pc/share rw
43 28 0:37 / /home/me/remoto rw,nosuid - fuse.sshfs me@host:/ rw
44 28 0:38 / /mnt/nas/locale-sopra rw,relatime - ext4 /dev/sdb1 rw
riga senza separatore
"""


def _posix_kind(path: str) -> FolderKind:
    return kind_from_mounts(PurePosixPath(path), parse_mountinfo(_MOUNTINFO))


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        pytest.param("/home/me/profilo", FolderKind.LOCAL, id="root-fs"),
        pytest.param("/mnt/nas/profili/a", FolderKind.NETWORK, id="nfs"),
        pytest.param("/mnt/win share/x", FolderKind.NETWORK, id="cifs-with-escaped-space"),
        pytest.param("/home/me/remoto/p", FolderKind.NETWORK, id="fuse-sshfs"),
        pytest.param("/mnt/nas", FolderKind.NETWORK, id="mount-point-itself"),
        pytest.param("/mnt/nas/locale-sopra/p", FolderKind.LOCAL, id="more-specific-mount-wins"),
        pytest.param("/mnt/nasty/p", FolderKind.LOCAL, id="prefix-is-not-ancestor"),
    ],
)
def test_linux_kind_comes_from_the_most_specific_mount(path: str, expected: FolderKind) -> None:
    assert _posix_kind(path) is expected


def test_linux_without_any_mount_information_is_unknown() -> None:
    assert kind_from_mounts(PurePosixPath("/x"), []) is FolderKind.UNKNOWN


def _windows(
    path: str,
    *,
    env: dict[str, str] | None = None,
    drive: int = 3,
    attributes: int = 0,
    dropbox: str | None = None,
) -> FolderKind:
    return windows_kind(
        PureWindowsPath(path),
        cloud_roots(env or {}, dropbox),
        lambda _root: drive,
        lambda _path: attributes,
    )


@pytest.mark.parametrize(
    "path",
    [
        _BS * 2 + "server" + _BS + "share" + _BS + "profili",
        _BS * 2 + "?" + _BS + "UNC" + _BS + "server" + _BS + "share" + _BS + "profili",
    ],
)
def test_windows_unc_paths_are_network(path: str) -> None:
    assert _windows(path) is FolderKind.NETWORK


def test_windows_remote_drive_is_network() -> None:
    assert _windows(r"Z:\profili", drive=windows_api.DRIVE_REMOTE) is FolderKind.NETWORK


def test_windows_extended_local_path_is_not_network() -> None:
    assert _windows(_BS * 2 + "?" + _BS + "C:" + _BS + "profili") is FolderKind.LOCAL


def test_windows_fixed_drive_is_local() -> None:
    assert _windows(r"D:\Dati\Profili") is FolderKind.LOCAL


def test_windows_unknown_drive_is_unknown() -> None:
    assert _windows(r"Q:\x", drive=windows_api.DRIVE_NO_ROOT_DIR) is FolderKind.UNKNOWN


@pytest.mark.parametrize("variable", ["OneDrive", "ONEDRIVECOMMERCIAL", "OneDriveConsumer"])
def test_windows_folder_under_a_onedrive_variable_is_cloud_synced(variable: str) -> None:
    env = {variable: r"C:\Users\x\OneDrive - Azienda"}

    assert _windows(r"c:\users\X\onedrive - azienda\profili", env=env) is FolderKind.CLOUD_SYNCED


def test_windows_folder_next_to_the_onedrive_root_is_not_cloud_synced() -> None:
    env = {"OneDrive": r"C:\Users\x\OneDrive"}

    assert _windows(r"C:\Users\x\OneDrive2\p", env=env) is FolderKind.CLOUD_SYNCED  # per nome
    assert _windows(r"C:\Users\x\Documenti\p", env=env) is FolderKind.LOCAL


def test_windows_dropbox_root_comes_from_its_info_file() -> None:
    info = json.dumps({"personal": {"path": r"E:\Archivio\Cassetta"}, "business": "rotto"})

    assert _windows(r"E:\Archivio\Cassetta\p", dropbox=info) is FolderKind.CLOUD_SYNCED
    assert _windows(r"E:\Archivio\Altro\p", dropbox=info) is FolderKind.LOCAL


@pytest.mark.parametrize("info", ["non json", "[1]", json.dumps({"a": {"path": 3}}), "{}"])
def test_a_broken_dropbox_info_file_is_ignored(info: str) -> None:
    assert _windows(r"E:\x", dropbox=info) is FolderKind.LOCAL


@pytest.mark.parametrize("name", ["Dropbox", "Google Drive", "My Drive", "OneDrive - Altro"])
def test_windows_well_known_cloud_folder_names_are_cloud_synced(name: str) -> None:
    assert _windows(rf"G:\{name}\profili") is FolderKind.CLOUD_SYNCED


@pytest.mark.parametrize("attribute", [0x00040000, 0x00400000])
def test_windows_cloud_placeholder_attributes_on_an_ancestor_are_cloud_synced(
    attribute: int,
) -> None:
    assert _windows(r"D:\x\y", attributes=attribute) is FolderKind.CLOUD_SYNCED


def test_nearest_existing_walks_up_to_a_folder_that_exists(tmp_path: Path) -> None:
    assert nearest_existing(tmp_path / "a" / "b") == tmp_path
    assert nearest_existing(tmp_path) == tmp_path


def test_the_system_detector_answers_for_existing_and_missing_folders(tmp_path: Path) -> None:
    detector = SystemFolderKindDetector(os.environ)

    assert isinstance(detector(tmp_path), FolderKind)
    assert detector(tmp_path / "nuova" / "cartella") is detector(tmp_path)


@pytest.mark.parametrize("failure", [FileTooLargeError("mountinfo"), OSError("negato")])
def test_the_system_detector_never_raises_even_when_the_probes_do(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: Exception
) -> None:
    def broken(*_args: object) -> bytes:
        raise failure

    monkeypatch.setattr(folder_kind, "read_limited", broken)
    monkeypatch.setattr(folder_kind, "_read_dropbox_info", broken)

    assert SystemFolderKindDetector(os.environ)(tmp_path) in set(FolderKind)
