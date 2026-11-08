# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
# ruff: noqa: E501, ANN002, ANN003, ANN202
"""Phase 8 acceptance campaign, interrupted backup and restore: T-DATI for the backup file.

Two kinds of interruption. A failure that raises (a disk that fails at the k-th file, a full disk)
is injected in-process and must leave nothing. A death of the process (power-off, kill) cannot run
any cleanup: a worker process dies at the k-th file and the test looks at what is left and at what
the next attempt can do.
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from ponos_coach.profiles.domain.backup import BACKUP_SUFFIX
from ponos_coach.profiles.ports import BackupWriteError
from ponos_coach.storage import profile_backup
from tests.collaudo.fase5_support import World
from tests.collaudo.fase8_support import (
    backup_use_case,
    database_name,
    make_rich_world,
    restore_use_case,
    without_residue,
)
from tests.fixtures.generators.collaudo_fase8_worker import DIED

WORKER = (
    Path(__file__).resolve().parents[1] / "fixtures" / "generators" / "collaudo_fase8_worker.py"
)


@pytest.fixture(scope="module")
def rich(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[World, Path]]:
    base = tmp_path_factory.mktemp("f8-interrupt")
    world = make_rich_world(base / "p", runs=4)
    destination = base / f"good{BACKUP_SUFFIX}"
    backup_use_case()(world.profile.folder, destination)
    yield world, destination
    world.close()


def run_worker(
    mode: str, source: Path, target: Path, kill_at: int
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed interpreter and script
        [sys.executable, "-I", str(WORKER), mode, str(source), str(target), str(kill_at)],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def files_written(mode: str, source: Path, target: Path) -> int:
    done = run_worker(mode, source, target, 0)
    assert done.returncode == 0, done.stderr
    return int(done.stdout.split()[-1])


# --- Failures that raise ---------------------------------------------------------------------------
def test_a_restore_that_fails_at_any_file_leaves_no_folder_and_no_residue(
    rich: tuple[World, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, backup = rich
    probe = tmp_path / "probe"
    calls = {"n": 0}
    real = vars(profile_backup)["replace_file"]

    def counting(source: Path, destination: Path) -> None:
        calls["n"] += 1
        real(source, destination)

    monkeypatch.setattr(profile_backup, "replace_file", counting)
    restore_use_case()(backup, probe)
    total = calls["n"]
    assert total >= 5

    for failing in range(1, total + 1):
        calls["n"] = 0
        target = tmp_path / f"t{failing}"

        def broken(source: Path, destination: Path, failing: int = failing) -> None:
            calls["n"] += 1
            if calls["n"] == failing:
                raise OSError("simulated disk failure")
            real(source, destination)

        monkeypatch.setattr(profile_backup, "replace_file", broken)
        with pytest.raises(BackupWriteError):
            restore_use_case()(backup, target)
        assert without_residue(target), f"file {failing} of {total}"
        assert not (target / database_name()).exists()


def test_a_backup_that_fails_at_any_step_leaves_no_file_and_no_temporary_folder(
    rich: tuple[World, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    world, _ = rich
    real_add = profile_backup._add_file
    calls = {"n": 0}

    def counting(*args, **kwargs):  # type: ignore[no-untyped-def]
        calls["n"] += 1
        return real_add(*args, **kwargs)

    monkeypatch.setattr(profile_backup, "_add_file", counting)
    backup_use_case()(world.profile.folder, tmp_path / f"probe{BACKUP_SUFFIX}")
    total = calls["n"]

    for failing in range(1, total + 1):
        calls["n"] = 0
        out = tmp_path / f"o{failing}"
        out.mkdir()

        def broken(*args, failing: int = failing, **kwargs):  # type: ignore[no-untyped-def]
            calls["n"] += 1
            if calls["n"] == failing:
                raise OSError("simulated full disk")
            return real_add(*args, **kwargs)

        monkeypatch.setattr(profile_backup, "_add_file", broken)
        with pytest.raises(BackupWriteError):
            backup_use_case()(world.profile.folder, out / f"b{BACKUP_SUFFIX}")
        assert list(out.iterdir()) == [], f"file {failing} of {total}"


def test_a_backup_whose_publication_fails_leaves_no_file_and_no_temporary_folder(
    rich: tuple[World, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    world, _ = rich

    def broken(source: Path, destination: Path) -> None:
        raise OSError("simulated rename failure")

    monkeypatch.setattr(profile_backup, "rename_exclusive", broken)
    with pytest.raises(BackupWriteError):
        backup_use_case()(world.profile.folder, tmp_path / f"b{BACKUP_SUFFIX}")

    assert list(tmp_path.iterdir()) == []


# --- The process dies --------------------------------------------------------------------------------
def test_a_backup_killed_at_every_file_never_leaves_a_file_with_the_backup_name(
    rich: tuple[World, Path], tmp_path: Path
) -> None:
    world, _ = rich
    total = files_written("backup", world.profile.folder, tmp_path / f"probe{BACKUP_SUFFIX}")

    for kill in range(1, total + 1):
        out = tmp_path / f"k{kill}"
        out.mkdir()
        result = run_worker("backup", world.profile.folder, out / f"b{BACKUP_SUFFIX}", kill)
        assert result.returncode == DIED, result.stderr
        assert not (out / f"b{BACKUP_SUFFIX}").exists(), f"killed at file {kill} of {total}"


def test_a_backup_killed_midway_leaves_nothing_next_to_the_destination_after_the_next_backup(
    rich: tuple[World, Path], tmp_path: Path
) -> None:
    """D-F8-02 (low): the ZIP is built in `.ponos-backup-*` next to the destination; a process
    that dies leaves it (a partial copy of the athlete's database and files, in the folder the
    user chose, for ever). A later backup into the same folder does not clean it up."""
    world, _ = rich
    out = tmp_path / "out"
    out.mkdir()
    killed = run_worker("backup", world.profile.folder, out / f"b{BACKUP_SUFFIX}", 2)
    assert killed.returncode == DIED

    backup_use_case()(world.profile.folder, out / f"again{BACKUP_SUFFIX}")

    assert sorted(p.name for p in out.iterdir()) == [f"again{BACKUP_SUFFIX}"]


def test_a_restore_killed_at_every_file_never_leaves_an_openable_profile(
    rich: tuple[World, Path], tmp_path: Path
) -> None:
    """The database goes last, so a dead restore has no `ponos.sqlite3`: it is not a profile."""
    _, backup = rich
    total = files_written("restore", backup, tmp_path / "probe")

    for kill in range(1, total + 1):
        target = tmp_path / f"k{kill}"
        result = run_worker("restore", backup, target, kill)
        assert result.returncode == DIED, result.stderr
        assert not (target / database_name()).exists(), f"killed at file {kill} of {total}"


def test_a_restore_killed_midway_can_be_repeated_into_the_same_folder(
    rich: tuple[World, Path], tmp_path: Path
) -> None:
    """D-F8-01 (medium): after a power-off during a restore the target holds some archive files
    and no database; `RestoreProfile` refuses a non-empty folder, so the user cannot simply retry
    (the files are not a profile and nothing says what they are). The folder should be a
    temporary sibling renamed when complete, or the restore should recognise its own leftovers."""
    _, backup = rich
    target = tmp_path / "target"
    killed = run_worker("restore", backup, target, 3)
    assert killed.returncode == DIED

    restore_use_case()(backup, target)

    assert (target / database_name()).is_file()
