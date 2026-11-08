# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
# ruff: noqa: ARG002, RUF001  # the fake ignores its arguments; the digits are the test data
"""Profile backup: exclusive publication, leftovers of dead runs, staged restore, ASCII years."""

from __future__ import annotations

import os
import stat
import subprocess
import sys
import time
import zipfile
from datetime import datetime
from pathlib import Path

import pytest

from ponos_coach.profiles.backup import BackupProfile
from ponos_coach.profiles.domain.backup import (
    MANIFEST_NAME,
    BackupSummary,
    is_archive_year,
    is_backup_entry_path,
)
from ponos_coach.profiles.ports import BackupFileExistsError, BackupWriteError, FolderNotEmptyError
from ponos_coach.storage import profile_backup
from ponos_coach.storage.profile_backup import (
    FileProfileBackup,
    remove_stale_temporaries,
    rename_exclusive,
)
from tests.storage.test_profile_backup import _NOW, _PROFILE_ID, _backup, _make_profile

_RANDOM = "abcd1234"
_SHA = "a" * 64


def _dead_pid() -> int:
    """The id of a process that has already ended."""
    done = subprocess.run(
        [sys.executable, "-I", "-c", "import os; print(os.getpid())"],
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    return int(done.stdout)


def _leftover(parent: Path, prefix: str, pid: int, *, age_s: float = 0) -> Path:
    folder = parent / f"{prefix}-{pid}-{_RANDOM}"
    folder.mkdir()
    (folder / "partial.bin").write_bytes(b"partial copy")
    if age_s:
        past = time.time() - age_s
        os.utime(folder, (past, past))
    return folder


class TestExclusiveRename:
    def test_a_free_name_is_taken(self, tmp_path: Path) -> None:
        source = tmp_path / "a.tmp"
        source.write_bytes(b"new")

        rename_exclusive(source, tmp_path / "b.bin")

        assert (tmp_path / "b.bin").read_bytes() == b"new"
        assert not source.exists()

    def test_an_existing_file_is_never_replaced(self, tmp_path: Path) -> None:
        source = tmp_path / "a.tmp"
        source.write_bytes(b"new")
        taken = tmp_path / "b.bin"
        taken.write_bytes(b"someone else's file")

        with pytest.raises(FileExistsError):
            rename_exclusive(source, taken)

        assert taken.read_bytes() == b"someone else's file"
        assert source.read_bytes() == b"new"

    def test_an_existing_folder_is_never_replaced(self, tmp_path: Path) -> None:
        source = tmp_path / "staging"
        source.mkdir()
        taken = tmp_path / "taken"
        taken.mkdir()
        (taken / "mine.txt").write_bytes(b"x")

        with pytest.raises(FileExistsError):
            rename_exclusive(source, taken)

        assert (taken / "mine.txt").is_file()
        assert source.is_dir()

    def test_a_folder_takes_a_free_name(self, tmp_path: Path) -> None:
        source = tmp_path / "staging"
        source.mkdir()
        (source / "x").write_bytes(b"1")

        rename_exclusive(source, tmp_path / "final")

        assert (tmp_path / "final" / "x").read_bytes() == b"1"
        assert not source.exists()

    @pytest.mark.skipif(sys.platform == "win32", reason="the fallback is for POSIX file systems")
    def test_without_hard_links_the_name_is_claimed_first(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def no_links(source: Path, destination: Path) -> None:
            raise PermissionError("no hard links here")

        monkeypatch.setattr(os, "link", no_links)
        source = tmp_path / "a.tmp"
        source.write_bytes(b"new")
        taken = tmp_path / "b.bin"
        taken.write_bytes(b"someone else's file")

        with pytest.raises(FileExistsError):
            rename_exclusive(source, taken)
        assert taken.read_bytes() == b"someone else's file"

        rename_exclusive(source, tmp_path / "c.bin")
        assert (tmp_path / "c.bin").read_bytes() == b"new"


class TestPublication:
    def test_a_file_created_while_the_backup_runs_is_not_replaced(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        profile = _make_profile(tmp_path)
        out = tmp_path / "out"
        out.mkdir()
        destination = out / "b.ponosbackup"
        real = profile_backup.rename_exclusive

        def someone_is_faster(source: Path, target: Path) -> None:
            target.write_bytes(b"created by someone else meanwhile")
            real(source, target)

        monkeypatch.setattr(profile_backup, "rename_exclusive", someone_is_faster)

        with pytest.raises(BackupFileExistsError):
            _backup(profile, destination)

        assert destination.read_bytes() == b"created by someone else meanwhile"
        assert [p.name for p in out.iterdir()] == ["b.ponosbackup"]

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX modes")
    def test_the_file_is_readable_only_by_its_owner(self, tmp_path: Path) -> None:
        profile = _make_profile(tmp_path)
        destination = tmp_path / "b.ponosbackup"

        _backup(profile, destination)

        assert stat.S_IMODE(destination.stat().st_mode) == 0o600


class TestLeftoversOfDeadRuns:
    def test_the_next_backup_removes_the_temporary_of_a_dead_one(self, tmp_path: Path) -> None:
        profile = _make_profile(tmp_path)
        out = tmp_path / "out"
        out.mkdir()
        _leftover(out, ".ponos-backup", _dead_pid())

        _backup(profile, out / "b.ponosbackup")

        assert [p.name for p in out.iterdir()] == ["b.ponosbackup"]

    def test_a_temporary_of_a_running_process_is_left_alone(self, tmp_path: Path) -> None:
        parent = tmp_path / "parent"
        parent.mkdir()
        mine = _leftover(parent, ".ponos-backup", os.getpid())

        remove_stale_temporaries(parent, ".ponos-backup-")

        assert mine.is_dir()

    def test_an_old_temporary_goes_even_if_its_process_id_is_in_use(self, tmp_path: Path) -> None:
        parent = tmp_path / "parent"
        parent.mkdir()
        old = _leftover(parent, ".ponos-backup", os.getpid(), age_s=3 * 24 * 3600)

        remove_stale_temporaries(parent, ".ponos-backup-")

        assert not old.exists()

    def test_only_what_the_program_generates_is_touched(self, tmp_path: Path) -> None:
        parent = tmp_path / "parent"
        parent.mkdir()
        dead = _dead_pid()
        keep = [
            parent / ".ponos-backup-notes",  # a folder of the user that looks alike
            parent / f".ponos-backup-{dead}-{_RANDOM.upper()}",  # not the generated alphabet
            parent / f".ponos-backup-{dead}-short",
            parent / f".ponos-backup-{dead}-{_RANDOM}x",
            parent / f".ponos-restore-{dead}-{_RANDOM}",  # the other kind, another prefix
        ]
        for folder in keep:
            folder.mkdir()
            (folder / "mine.txt").write_bytes(b"x")
        a_file = parent / f".ponos-backup-{dead}-efgh5678"
        a_file.write_bytes(b"a file, not a folder")

        remove_stale_temporaries(parent, ".ponos-backup-")

        assert all((folder / "mine.txt").is_file() for folder in keep)
        assert a_file.read_bytes() == b"a file, not a folder"

    def test_a_missing_parent_is_not_an_error(self, tmp_path: Path) -> None:
        remove_stale_temporaries(tmp_path / "missing", ".ponos-backup-")


class TestStagedRestore:
    def _backup_file(self, tmp_path: Path) -> Path:
        destination = tmp_path / "b.ponosbackup"
        _backup(_make_profile(tmp_path), destination)
        return destination

    def test_the_next_restore_removes_the_temporary_of_a_dead_one(self, tmp_path: Path) -> None:
        backup = self._backup_file(tmp_path)
        parent = tmp_path / "parent"
        parent.mkdir()
        _leftover(parent, ".ponos-restore", _dead_pid())

        FileProfileBackup().restore(backup, parent / "restored")

        assert [p.name for p in parent.iterdir()] == ["restored"]

    def test_a_failed_restore_leaves_the_destination_and_its_parent_as_they_were(
        self, tmp_path: Path
    ) -> None:
        parent = tmp_path / "parent"
        parent.mkdir()
        mine = parent / "mine"
        mine.mkdir()
        garbage = tmp_path / "garbage.ponosbackup"
        garbage.write_bytes(b"not a backup" * 20)

        with pytest.raises(Exception):  # noqa: B017 - the type is tested elsewhere
            FileProfileBackup().restore(garbage, mine)

        assert [p.name for p in parent.iterdir()] == ["mine"]
        assert list(mine.iterdir()) == []

    def test_a_folder_that_appears_during_the_restore_is_not_replaced(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        backup = self._backup_file(tmp_path)
        target = tmp_path / "restored"
        real = profile_backup.rename_exclusive

        def someone_is_faster(source: Path, destination: Path) -> None:
            destination.mkdir()
            (destination / "theirs.txt").write_bytes(b"x")
            real(source, destination)

        monkeypatch.setattr(profile_backup, "rename_exclusive", someone_is_faster)

        with pytest.raises(BackupWriteError):
            FileProfileBackup().restore(backup, target)

        assert [p.name for p in target.iterdir()] == ["theirs.txt"]
        assert [p.name for p in tmp_path.iterdir() if p.name.startswith(".ponos-")] == []

    def test_a_folder_with_content_is_refused_before_anything_is_written(
        self, tmp_path: Path
    ) -> None:
        backup = self._backup_file(tmp_path)
        target = tmp_path / "restored"
        target.mkdir()
        (target / "notes.txt").write_bytes(b"mine")

        with pytest.raises(FolderNotEmptyError):
            FileProfileBackup().restore(backup, target)

        assert sorted(p.name for p in tmp_path.iterdir() if p.name.startswith(".ponos-")) == []


class TestAsciiYears:
    @pytest.mark.parametrize("name", ["2026", "0000", "1999"])
    def test_four_ascii_digits_are_a_year(self, name: str) -> None:
        assert is_archive_year(name)

    @pytest.mark.parametrize(
        "name",
        [
            "٢٠٢٦",  # Arabic-Indic digits
            "２０２６",  # fullwidth digits
            "²⁰²⁶",  # superscripts
            "202",
            "20266",
            "20 6",
            "2026\n",
            "",
            "abcd",
        ],
    )
    def test_anything_else_is_not(self, name: str) -> None:
        assert not is_archive_year(name)

    def test_an_entry_with_unicode_digits_is_not_in_the_closed_list(self) -> None:
        assert is_backup_entry_path(f"archivio/2026/{_SHA}.fit")
        assert not is_backup_entry_path(f"archivio/٢٠٢٦/{_SHA}.fit")

    def test_the_writer_skips_a_year_folder_with_unicode_digits(self, tmp_path: Path) -> None:
        profile = _make_profile(tmp_path)
        odd = profile.folder / "archivio" / "٢٠٢٦"
        odd.mkdir()
        (odd / f"{_SHA}.fit").write_bytes(b"not part of a backup")
        destination = tmp_path / "b.ponosbackup"

        _backup(profile, destination)

        with zipfile.ZipFile(destination) as archive:
            names = set(archive.namelist())
        assert MANIFEST_NAME in names
        assert not any("٢" in name for name in names)


class TestDestinationOutsideTheProfile:
    @staticmethod
    def _link(target: Path, link: Path) -> None:
        """A symbolic link, or a junction on Windows (needs no privilege)."""
        try:
            link.symlink_to(target, target_is_directory=True)
        except (OSError, NotImplementedError):
            if sys.platform != "win32":
                pytest.skip("symbolic links are not available")
            made = subprocess.run(  # noqa: S603 - fixed program and paths made by the test
                ["cmd", "/c", "mklink", "/J", str(link), str(target)],  # noqa: S607
                capture_output=True,
                check=False,
                timeout=60,
            )
            if made.returncode != 0:
                pytest.skip("no link or junction available")

    def test_a_destination_reached_through_a_link_into_the_profile_is_refused(
        self, tmp_path: Path
    ) -> None:
        profile = _make_profile(tmp_path)
        alias = tmp_path / "alias"
        self._link(profile.folder, alias)
        storage = _RecordingWrite()

        with pytest.raises(ValueError, match="inside"):
            BackupProfile(storage, lambda: _NOW, "0.1.0")(profile.folder, alias / "b.ponosbackup")

        assert storage.calls == 0

    def test_a_link_to_another_place_is_fine(self, tmp_path: Path) -> None:
        profile = _make_profile(tmp_path)
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        alias = tmp_path / "alias"
        self._link(elsewhere, alias)
        storage = _RecordingWrite()

        BackupProfile(storage, lambda: _NOW, "0.1.0")(profile.folder, alias / "b.ponosbackup")

        assert storage.calls == 1


class _RecordingWrite:
    """A fake of the port that only counts the writes: the count is what the tests look at."""

    def __init__(self) -> None:
        self.calls = 0

    def write(
        self, folder: Path, destination: Path, created_utc: datetime, app_version: str
    ) -> BackupSummary:
        self.calls += 1
        return BackupSummary(_PROFILE_ID, created_utc, 1, 0, 0)

    def restore(self, source: Path, folder: Path) -> BackupSummary:
        raise AssertionError("not used")
