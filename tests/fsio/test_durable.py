# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Durable replacement of a file and subfolders: the shared `fsio` module."""

from __future__ import annotations

import stat
import sys
from pathlib import Path

import pytest

from ponos_coach import fsio
from ponos_coach.fsio import durable


def test_write_creates_and_replaces_without_leftovers(tmp_path: Path) -> None:
    target = tmp_path / "dati.bin"

    fsio.write_file_atomically(target, b"uno")
    fsio.write_file_atomically(target, b"due")

    assert target.read_bytes() == b"due"
    assert [p.name for p in tmp_path.iterdir()] == ["dati.bin"]


def test_a_failed_replace_keeps_the_old_file_and_removes_the_temporary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "dati.bin"
    target.write_bytes(b"vecchio")

    def refuse(source: Path, destination: Path) -> None:
        raise OSError("rename refused")

    monkeypatch.setattr(durable, "replace_file", refuse)

    with pytest.raises(OSError, match="refused"):
        fsio.write_file_atomically(target, b"nuovo")

    assert target.read_bytes() == b"vecchio"
    assert [p.name for p in tmp_path.iterdir()] == ["dati.bin"]


def test_replace_file_overwrites_the_destination(tmp_path: Path) -> None:
    source, destination = tmp_path / "a", tmp_path / "b"
    source.write_bytes(b"a")
    destination.write_bytes(b"b")

    fsio.replace_file(source, destination)
    fsio.sync_directory(tmp_path)

    assert destination.read_bytes() == b"a"
    assert not source.exists()


def test_replace_file_of_a_missing_source_fails_and_keeps_the_destination(tmp_path: Path) -> None:
    destination = tmp_path / "b"
    destination.write_bytes(b"b")

    with pytest.raises(OSError):
        fsio.replace_file(tmp_path / "manca", destination)

    assert destination.read_bytes() == b"b"


def test_make_subdirectory_reports_whether_it_created_it(tmp_path: Path) -> None:
    folder = tmp_path / "anno"

    assert fsio.make_subdirectory(folder) is True
    assert fsio.make_subdirectory(folder) is False
    assert folder.is_dir()


def test_make_subdirectory_refuses_a_file_in_the_way(tmp_path: Path) -> None:
    (tmp_path / "anno").write_bytes(b"x")

    with pytest.raises(FileExistsError):
        fsio.make_subdirectory(tmp_path / "anno")


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions")
def test_posix_subdirectory_is_private(tmp_path: Path) -> None:
    folder = tmp_path / "anno"

    fsio.make_subdirectory(folder)

    assert stat.S_IMODE(folder.stat().st_mode) & 0o077 == 0
