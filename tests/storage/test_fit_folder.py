# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Sorgente di file FIT da cartella: primo livello, dimensione controllata prima della lettura."""

from __future__ import annotations

from pathlib import Path

import pytest

from ponos_coach.activities.fit_decoder import decode_activity
from ponos_coach.activities.ports import (
    FitFileRef,
    FitFileSource,
    FitFileTooLargeError,
    FitFileUnreadableError,
)
from ponos_coach.storage.fit_folder import FitFolder

FIXTURE_RUN = Path(__file__).resolve().parents[1] / "fixtures" / "data" / "fit"


def test_lists_only_fit_files_of_the_first_level_sorted_by_name(tmp_path: Path) -> None:
    (tmp_path / "b.fit").write_bytes(b"12345")
    (tmp_path / "a.FIT").write_bytes(b"1")
    (tmp_path / "notes.txt").write_bytes(b"x")
    (tmp_path / "fit").mkdir()  # una cartella con nome simile
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "deep.fit").write_bytes(b"x")

    refs = FitFolder(tmp_path).list_files()

    assert refs == [FitFileRef("a.FIT", 1), FitFileRef("b.fit", 5)]


def test_empty_folder_lists_nothing(tmp_path: Path) -> None:
    assert FitFolder(tmp_path).list_files() == []


def test_missing_folder_is_unreadable(tmp_path: Path) -> None:
    with pytest.raises(FitFileUnreadableError):
        FitFolder(tmp_path / "non-esiste").list_files()


def test_read_returns_the_bytes_and_they_decode(tmp_path: Path) -> None:
    original = (FIXTURE_RUN / "run_one_second.fit").read_bytes()
    (tmp_path / "one.fit").write_bytes(original)
    folder = FitFolder(tmp_path)
    (ref,) = folder.list_files()

    data = folder.read(ref, 1024 * 1024)

    assert data == original
    assert decode_activity(data).duration_s == 1.0


def test_read_refuses_a_file_over_the_maximum_without_reading_it(tmp_path: Path) -> None:
    (tmp_path / "big.fit").write_bytes(b"x" * 101)
    folder = FitFolder(tmp_path)
    (ref,) = folder.list_files()

    with pytest.raises(FitFileTooLargeError):
        folder.read(ref, 100)


def test_read_checks_the_real_size_not_the_listed_one(tmp_path: Path) -> None:
    (tmp_path / "grown.fit").write_bytes(b"x" * 500)
    folder = FitFolder(tmp_path)

    with pytest.raises(FitFileTooLargeError):
        folder.read(FitFileRef("grown.fit", 1), 100)


def test_read_at_the_exact_maximum_is_accepted(tmp_path: Path) -> None:
    (tmp_path / "edge.fit").write_bytes(b"x" * 100)
    folder = FitFolder(tmp_path)

    assert len(folder.read(FitFileRef("edge.fit", 100), 100)) == 100


def test_read_of_a_vanished_file_is_unreadable(tmp_path: Path) -> None:
    with pytest.raises(FitFileUnreadableError):
        FitFolder(tmp_path).read(FitFileRef("gone.fit", 1), 100)


@pytest.mark.parametrize("name", ["../x.fit", "sub/x.fit", "..\\x.fit", "notes.txt", ""])
def test_read_refuses_names_that_are_not_plain_fit_files(tmp_path: Path, name: str) -> None:
    (tmp_path.parent / "x.fit").write_bytes(b"secret")

    with pytest.raises(FitFileUnreadableError):
        FitFolder(tmp_path).read(FitFileRef(name, 1), 100)


def test_folder_adapter_satisfies_the_port(tmp_path: Path) -> None:
    source: FitFileSource = FitFolder(tmp_path)

    assert list(source.list_files()) == []
