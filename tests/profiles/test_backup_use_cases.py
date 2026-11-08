# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""`BackupProfile` and `RestoreProfile`: what is allowed, and clean failure (T-DATI-05)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ponos_coach.platform.profile_folders import LocalProfileFolders
from ponos_coach.profiles.backup import BackupProfile, RestoreProfile
from ponos_coach.profiles.domain import FolderKind, ProfileId, ProfileName
from ponos_coach.profiles.domain.backup import BACKUP_SUFFIX, BackupSummary
from ponos_coach.profiles.ports import (
    BackupInvalidError,
    BackupProblem,
    BackupWriteError,
    FolderNotEmptyError,
)
from ponos_coach.storage.profile_backup import FileProfileBackup
from ponos_coach.storage.sqlite_profile import SqliteProfileStorage

_NOW = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)
_ID = ProfileId(uuid.UUID("0b3f4c1a-7d2e-4a8b-9c65-1e2f3a4b5c6d"))


class _RecordingStorage:
    """Records the calls; `restore` can fail the way the real one does: having cleaned up."""

    def __init__(self, *, fail_restore: bool = False) -> None:
        self.written: list[tuple[Path, Path, datetime, str]] = []
        self.fail_restore = fail_restore

    def write(
        self, folder: Path, destination: Path, created_utc: datetime, app_version: str
    ) -> BackupSummary:
        self.written.append((folder, destination, created_utc, app_version))
        return BackupSummary(_ID, created_utc, 1, 0, 0)

    def restore(self, source: Path, folder: Path) -> BackupSummary:
        del source, folder
        if self.fail_restore:
            raise BackupWriteError("disk full")
        return BackupSummary(_ID, _NOW, 1, 0, 0)


def _folders() -> LocalProfileFolders:
    return LocalProfileFolders(lambda _folder: FolderKind.LOCAL)


def _profile(root: Path) -> Path:
    folder = root / "profile"
    folder.mkdir()
    SqliteProfileStorage().create(folder, _ID, ProfileName.parse("Atleta di prova"), "UTC").close()
    return folder


class TestBackupProfile:
    def test_passes_the_clock_and_version_to_the_storage(self, tmp_path: Path) -> None:
        storage = _RecordingStorage()
        folder = tmp_path / "p"
        destination = tmp_path / f"copy{BACKUP_SUFFIX}"

        BackupProfile(storage, lambda: _NOW, "0.1.0")(folder, destination)

        assert storage.written == [(folder, destination, _NOW, "0.1.0")]

    def test_relative_destination_is_refused(self, tmp_path: Path) -> None:
        storage = _RecordingStorage()

        with pytest.raises(ValueError, match="absolute"):
            BackupProfile(storage, lambda: _NOW, "0.1.0")(
                tmp_path / "p", Path(f"relative{BACKUP_SUFFIX}")
            )

        assert storage.written == []

    def test_other_suffix_is_refused(self, tmp_path: Path) -> None:
        storage = _RecordingStorage()

        with pytest.raises(ValueError, match="ends with"):
            BackupProfile(storage, lambda: _NOW, "0.1.0")(tmp_path / "p", tmp_path / "copy.zip")

        assert storage.written == []

    def test_destination_inside_the_profile_is_refused(self, tmp_path: Path) -> None:
        storage = _RecordingStorage()
        folder = tmp_path / "p"

        with pytest.raises(ValueError, match="inside"):
            BackupProfile(storage, lambda: _NOW, "0.1.0")(folder, folder / f"x{BACKUP_SUFFIX}")

        assert storage.written == []


class TestRestoreProfile:
    def _backup(self, tmp_path: Path) -> Path:
        destination = tmp_path / f"copy{BACKUP_SUFFIX}"
        BackupProfile(FileProfileBackup(), lambda: _NOW, "0.1.0")(_profile(tmp_path), destination)
        return destination

    def test_restores_into_a_folder_that_does_not_exist_yet(self, tmp_path: Path) -> None:
        backup = self._backup(tmp_path)
        target = tmp_path / "new" / "restored"

        summary = RestoreProfile(FileProfileBackup(), _folders())(backup, target)

        assert summary.profile_id == _ID
        assert SqliteProfileStorage().peek(target).info.profile_id == _ID

    def test_the_lock_is_released_when_it_is_done(self, tmp_path: Path) -> None:
        backup = self._backup(tmp_path)
        target = tmp_path / "restored"

        RestoreProfile(FileProfileBackup(), _folders())(backup, target)

        _folders().acquire_lock(target).release()  # not held by the restore

    def test_never_restores_over_an_existing_profile(self, tmp_path: Path) -> None:
        backup = self._backup(tmp_path)
        existing = tmp_path / "profile"
        before = sorted(p.name for p in existing.iterdir())

        with pytest.raises(FolderNotEmptyError):
            RestoreProfile(FileProfileBackup(), _folders())(backup, existing)

        assert sorted(p.name for p in existing.iterdir()) == before

    def test_damaged_backup_leaves_no_folder(self, tmp_path: Path) -> None:
        backup = self._backup(tmp_path)
        data = bytearray(backup.read_bytes())
        data[len(data) // 2] ^= 0xFF
        backup.write_bytes(bytes(data))
        target = tmp_path / "restored"

        with pytest.raises(BackupInvalidError) as caught:
            RestoreProfile(FileProfileBackup(), _folders())(backup, target)

        assert caught.value.problem in set(BackupProblem)
        assert not target.exists()

    def test_failure_removes_the_folder_it_created_and_its_lock(self, tmp_path: Path) -> None:
        target = tmp_path / "restored"

        with pytest.raises(BackupWriteError):
            RestoreProfile(_RecordingStorage(fail_restore=True), _folders())(
                tmp_path / "x.ponosbackup", target
            )

        assert not target.exists()

    def test_relative_paths_are_refused(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="absolute"):
            RestoreProfile(_RecordingStorage(), _folders())(Path("a.ponosbackup"), tmp_path / "r")
