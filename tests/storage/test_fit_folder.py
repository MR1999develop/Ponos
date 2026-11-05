# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Sorgente di file FIT da cartella: primo livello, dimensione controllata prima della lettura."""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from ponos_coach.activities.fit_decoder import decode_activity
from ponos_coach.activities.ports import (
    FitFileRef,
    FitFileSource,
    FitFileTooLargeError,
    FitFileUnreadableError,
)
from ponos_coach.load.domain import HeartRateLimits, Physiology, Sex, ThresholdHeartRate
from ponos_coach.load.use_cases import (
    FitnessParameters,
    RejectionReason,
    compute_fitness_overview,
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
    assert decode_activity(data).elapsed_s == 1.0


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


# --- Voci che non sono file regolari, o che spariscono ---------------------------


def make_symlink(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("il sistema non permette di creare collegamenti simbolici")


def test_symlink_named_fit_is_neither_listed_nor_readable(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.fit").write_bytes(b"secret")
    folder = tmp_path / "folder"
    folder.mkdir()
    make_symlink(folder / "link.fit", outside / "secret.fit")

    source = FitFolder(folder)

    assert source.list_files() == []
    with pytest.raises(FitFileUnreadableError):
        source.read(FitFileRef("link.fit", 6), 100)


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="i FIFO esistono solo su POSIX")
def test_fifo_named_fit_does_not_block_and_is_unreadable(tmp_path: Path) -> None:
    os.mkfifo(tmp_path / "x.fit")  # type: ignore[attr-defined,unused-ignore]
    source = FitFolder(tmp_path)

    assert source.list_files() == []  # non è un file regolare
    with pytest.raises(FitFileUnreadableError):  # e se qualcuno lo chiede lo stesso, non blocca
        source.read(FitFileRef("x.fit", 0), 100)


def test_non_regular_file_opened_is_refused_on_the_handle(tmp_path: Path) -> None:
    (tmp_path / "dir.fit").mkdir()

    with pytest.raises(FitFileUnreadableError):
        FitFolder(tmp_path).read(FitFileRef("dir.fit", 0), 100)


def test_entry_that_vanishes_during_listing_is_listed_and_rejected_as_unreadable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "ok.fit").write_bytes(b"12")

    class VanishingEntry:
        name = "gone.fit"

        def is_file(self, **_options: bool) -> bool:
            return True

        def stat(self, **_options: bool) -> os.stat_result:
            raise FileNotFoundError(self.name)

    real_scandir = os.scandir

    @contextmanager
    def scandir(path: Path) -> Iterator[Iterator[object]]:
        with real_scandir(path) as entries:
            yield iter([*entries, VanishingEntry()])

    monkeypatch.setattr(os, "scandir", scandir)
    source = FitFolder(tmp_path)

    refs = source.list_files()

    assert [ref.name for ref in refs] == ["gone.fit", "ok.fit"]
    with pytest.raises(FitFileUnreadableError):
        source.read(refs[0], 100)


def test_file_deleted_between_listing_and_reading_is_rejected_by_the_use_case(
    tmp_path: Path,
) -> None:
    (tmp_path / "gone.fit").write_bytes((FIXTURE_RUN / "run_one_second.fit").read_bytes())
    (tmp_path / "kept.fit").write_bytes((FIXTURE_RUN / "run_constant_160.fit").read_bytes())
    folder = FitFolder(tmp_path)

    class Vanishing:
        def list_files(self) -> list[FitFileRef]:
            refs = folder.list_files()
            (tmp_path / "gone.fit").unlink()
            return refs

        def read(self, ref: FitFileRef, max_bytes: int) -> bytes:
            return folder.read(ref, max_bytes)

    params = FitnessParameters(
        Physiology(HeartRateLimits(50, 190), ThresholdHeartRate(170), Sex.MALE),
        ZoneInfo("UTC"),
    )

    overview = compute_fitness_overview(Vanishing(), params, date(2026, 3, 10))

    assert [e.file_name for e in overview.activities] == ["kept.fit"]
    assert [(r.file_name, r.reason) for r in overview.rejected] == [
        ("gone.fit", RejectionReason.UNREADABLE)
    ]
