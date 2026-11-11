# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Profile backup on disk: round trip (T-DATI-05), no leftovers, damaged and hostile files."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
import zipfile
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest

from ponos_coach.activities.domain import FileFormat
from ponos_coach.profiles.domain import (
    PROFILE_DATABASE_NAME,
    ProfileId,
    ProfileName,
)
from ponos_coach.profiles.domain.backup import (
    BACKUP_FORMAT_VERSION,
    MANIFEST_NAME,
    BackupLimits,
    is_backup_entry_path,
)
from ponos_coach.profiles.ports import (
    BackupFileExistsError,
    BackupInvalidError,
    BackupProblem,
    BackupTooNewError,
    BackupWriteError,
    FolderNotEmptyError,
)
from ponos_coach.storage.file_archive import FileArchive
from ponos_coach.storage.profile_backup import FileProfileBackup
from ponos_coach.storage.sqlite_profile import SqliteProfileStorage
from tests.builders import T0, fit_identity, make_record, physiology_entry

_NOW = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)
_FIT_BYTES = b"FIT-like bytes of a test run " * 40
_OTHER_FIT_BYTES = b"FIT-like bytes of another test run " * 20
_PROFILE_ID = ProfileId(uuid.UUID("0b3f4c1a-7d2e-4a8b-9c65-1e2f3a4b5c6d"))


@dataclass(frozen=True, slots=True)
class _Profile:
    folder: Path
    archive_paths: tuple[str, ...]


def _make_profile(root: Path, name: str = "source") -> _Profile:
    """A closed profile of "Atleta di prova" with one activity, two archive files and data."""
    folder = root / name
    folder.mkdir()
    opened = SqliteProfileStorage().create(
        folder, _PROFILE_ID, ProfileName.parse("Atleta di prova"), "Europe/Rome"
    )
    archive = FileArchive.in_profile_folder(folder)
    fit = archive.store(_FIT_BYTES, FileFormat.FIT, T0)
    other = archive.store(_OTHER_FIT_BYTES, FileFormat.FIT, datetime(2025, 3, 2, tzinfo=UTC))
    with opened.activities.transaction():
        opened.activities.add(make_record(fit_identity(1)))
        opened.physiology.put(physiology_entry(date(2026, 1, 1), 172))
    opened.close()
    paths = tuple(f"archivio/{file.relative_path.as_posix()}" for file in (fit, other))
    return _Profile(folder, paths)


def _backup(profile: _Profile, destination: Path, limits: BackupLimits | None = None) -> None:
    FileProfileBackup(limits).write(profile.folder, destination, _NOW, "0.1.0")


_Mutation = Callable[[dict[str, bytes]], object]


def _entries(path: Path) -> dict[str, bytes]:
    with zipfile.ZipFile(path) as archive:
        return {info.filename: archive.read(info) for info in archive.infolist()}


def _rewrite(path: Path, mutate: _Mutation, compression: int = zipfile.ZIP_DEFLATED) -> None:
    """Replaces the backup by another with the entries changed by `mutate`."""
    entries = _entries(path)
    mutate(entries)
    with zipfile.ZipFile(path, "w", compression) as archive:
        for name, data in entries.items():
            archive.writestr(name, data)


def _edit_manifest(entries: dict[str, bytes], change: Callable[[dict[str, Any]], object]) -> None:
    document = json.loads(entries[MANIFEST_NAME])
    change(document)
    entries[MANIFEST_NAME] = json.dumps(document).encode()


@pytest.fixture
def source(tmp_path: Path) -> _Profile:
    return _make_profile(tmp_path)


@pytest.fixture
def backup_file(tmp_path: Path, source: _Profile) -> Path:
    destination = tmp_path / "profile.ponosbackup"
    _backup(source, destination)
    return destination


def _restore_target(tmp_path: Path) -> Path:
    folder = tmp_path / "restored"
    folder.mkdir()
    return folder


class TestRoundTrip:
    def test_restored_profile_has_the_same_data(
        self, tmp_path: Path, source: _Profile, backup_file: Path
    ) -> None:
        target = _restore_target(tmp_path)

        summary = FileProfileBackup().restore(backup_file, target)

        storage = SqliteProfileStorage()
        assert storage.peek(target) == storage.peek(source.folder)
        assert summary.profile_id == _PROFILE_ID
        assert summary.archive_files == 2
        restored, original = storage.open(target), storage.open(source.folder)
        try:
            assert restored.activities.list_by_start() == original.activities.list_by_start()
            assert restored.physiology.list_entries() == original.physiology.list_entries()
            assert restored.info == original.info
        finally:
            restored.close()
            original.close()

    def test_activity_files_are_byte_identical(
        self, tmp_path: Path, source: _Profile, backup_file: Path
    ) -> None:
        target = _restore_target(tmp_path)

        FileProfileBackup().restore(backup_file, target)

        for relative in source.archive_paths:
            assert (target / relative).read_bytes() == (source.folder / relative).read_bytes()

    def test_restore_leaves_only_profile_names(self, tmp_path: Path, backup_file: Path) -> None:
        target = _restore_target(tmp_path)

        FileProfileBackup().restore(backup_file, target)

        assert {item.name for item in target.iterdir()} <= {PROFILE_DATABASE_NAME, "archivio"}

    def test_backup_of_an_open_profile_is_consistent(self, tmp_path: Path) -> None:
        profile = _make_profile(tmp_path)
        opened = SqliteProfileStorage().open(profile.folder)
        with opened.activities.transaction():
            opened.activities.add(make_record(fit_identity(2)))
        destination = tmp_path / "open.ponosbackup"

        _backup(profile, destination)  # the app still has the profile open
        opened.close()

        target = _restore_target(tmp_path)
        FileProfileBackup().restore(destination, target)
        assert SqliteProfileStorage().peek(target).activity_count == 2

    def test_backup_contains_only_the_closed_list_of_names(
        self, tmp_path: Path, source: _Profile
    ) -> None:
        (source.folder / "ponos.lock").write_bytes(b"")
        (source.folder / "backup").mkdir()
        (source.folder / "backup" / "old.sqlite3").write_bytes(b"older copy")
        (source.folder / "archivio" / "2026" / ".partial.tmp").write_bytes(b"orphan")
        (source.folder / "token.json").write_bytes(b"secret")
        destination = tmp_path / "again.ponosbackup"

        _backup(source, destination)

        names = set(_entries(destination))
        assert names == {MANIFEST_NAME, PROFILE_DATABASE_NAME, *source.archive_paths}
        assert all(is_backup_entry_path(name) for name in names - {MANIFEST_NAME})

    def test_manifest_describes_the_files(self, backup_file: Path) -> None:
        entries = _entries(backup_file)
        document = json.loads(entries[MANIFEST_NAME])

        assert document["format_version"] == BACKUP_FORMAT_VERSION
        assert document["profile_id"] == str(_PROFILE_ID)
        for item in document["files"]:
            assert item["sha256"] == hashlib.sha256(entries[item["path"]]).hexdigest()
            assert item["size_bytes"] == len(entries[item["path"]])

    def test_profile_without_archive_files_round_trips(self, tmp_path: Path) -> None:
        folder = tmp_path / "bare"
        folder.mkdir()
        SqliteProfileStorage().create(
            folder, _PROFILE_ID, ProfileName.parse("Atleta di prova"), "UTC"
        ).close()
        destination = tmp_path / "bare.ponosbackup"
        FileProfileBackup().write(folder, destination, _NOW, "0.1.0")

        target = _restore_target(tmp_path)
        summary = FileProfileBackup().restore(destination, target)

        assert summary.archive_files == 0
        assert SqliteProfileStorage().peek(target).info.profile_id == _PROFILE_ID


class TestWriteFailures:
    def test_existing_destination_is_never_replaced(self, tmp_path: Path, source: _Profile) -> None:
        destination = tmp_path / "mine.ponosbackup"
        destination.write_bytes(b"precious")

        with pytest.raises(BackupFileExistsError):
            _backup(source, destination)

        assert destination.read_bytes() == b"precious"

    def test_missing_database_leaves_nothing_behind(self, tmp_path: Path) -> None:
        empty = tmp_path / "empty"
        empty.mkdir()
        out = tmp_path / "out"
        out.mkdir()

        with pytest.raises(BackupWriteError):
            FileProfileBackup().write(empty, out / "x.ponosbackup", _NOW, "0.1.0")

        assert list(out.iterdir()) == []
        assert list(empty.iterdir()) == []  # the source is only read

    def test_database_that_is_not_sqlite_is_refused(self, tmp_path: Path) -> None:
        folder = tmp_path / "bad"
        folder.mkdir()
        (folder / PROFILE_DATABASE_NAME).write_bytes(b"not a database at all" * 100)
        out = tmp_path / "out"
        out.mkdir()

        with pytest.raises(BackupWriteError):
            FileProfileBackup().write(folder, out / "x.ponosbackup", _NOW, "0.1.0")

        assert list(out.iterdir()) == []

    def test_missing_destination_folder(self, tmp_path: Path, source: _Profile) -> None:
        with pytest.raises(BackupWriteError):
            _backup(source, tmp_path / "nowhere" / "x.ponosbackup")

    def test_too_many_files_is_refused_without_a_file(
        self, tmp_path: Path, source: _Profile
    ) -> None:
        out = tmp_path / "limited.ponosbackup"

        with pytest.raises(BackupWriteError):
            _backup(source, out, BackupLimits(max_entries=1))

        assert not out.exists()
        assert [p.name for p in tmp_path.iterdir() if p.name.startswith(".ponos-backup")] == []

    def test_oversized_file_is_refused(self, tmp_path: Path, source: _Profile) -> None:
        with pytest.raises(BackupWriteError):
            _backup(source, tmp_path / "x.ponosbackup", BackupLimits(max_file_bytes=10))


class TestRestoreRefusals:
    def test_folder_with_content_is_not_touched(self, tmp_path: Path, backup_file: Path) -> None:
        target = _restore_target(tmp_path)
        (target / "notes.txt").write_bytes(b"mine")

        with pytest.raises(FolderNotEmptyError):
            FileProfileBackup().restore(backup_file, target)

        assert [p.name for p in target.iterdir()] == ["notes.txt"]

    def test_existing_profile_is_never_overwritten(
        self, source: _Profile, backup_file: Path
    ) -> None:
        before = (source.folder / PROFILE_DATABASE_NAME).read_bytes()

        with pytest.raises(FolderNotEmptyError):
            FileProfileBackup().restore(backup_file, source.folder)

        assert (source.folder / PROFILE_DATABASE_NAME).read_bytes() == before

    def test_missing_backup_file(self, tmp_path: Path) -> None:
        target = _restore_target(tmp_path)

        with pytest.raises(BackupWriteError):
            FileProfileBackup().restore(tmp_path / "gone.ponosbackup", target)

    def test_lock_file_alone_does_not_count_as_content(
        self, tmp_path: Path, backup_file: Path
    ) -> None:
        target = _restore_target(tmp_path)
        (target / "ponos.lock").write_bytes(b"")

        FileProfileBackup().restore(backup_file, target)

        assert (target / PROFILE_DATABASE_NAME).is_file()


def _flip_byte(data: bytes) -> bytes:
    middle = len(data) // 2
    return data[:middle] + bytes([data[middle] ^ 0xFF]) + data[middle + 1 :]


def _fix_manifest_hash(entries: dict[str, bytes], path: str) -> None:
    """Makes the manifest agree with the (changed) bytes of `path`."""

    def change(document: dict[str, Any]) -> None:
        for item in document["files"]:
            if item["path"] == path:
                item["sha256"] = hashlib.sha256(entries[path]).hexdigest()
                item["size_bytes"] = len(entries[path])

    _edit_manifest(entries, change)


def _garbage_database(entries: dict[str, bytes]) -> None:
    entries[PROFILE_DATABASE_NAME] = b"garbage" * 500
    _fix_manifest_hash(entries, PROFILE_DATABASE_NAME)


def _damage_archive_file(source: _Profile) -> _Mutation:
    def mutate(entries: dict[str, bytes]) -> None:
        path = source.archive_paths[0]
        entries[path] = _flip_byte(entries[path])  # the manifest still has the old hash

    return mutate


class TestDamagedBackups:
    @pytest.mark.parametrize(
        ("mutation", "problem"),
        [
            ("flip_archive_byte", BackupProblem.CHECKSUM),
            ("manifest_wrong_size", BackupProblem.CHECKSUM),
            ("manifest_missing", BackupProblem.NOT_A_BACKUP),
            ("extra_entry", BackupProblem.UNSAFE_ENTRY),
            ("entry_missing", BackupProblem.UNSAFE_ENTRY),
            ("traversal_entry", BackupProblem.UNSAFE_ENTRY),
            ("manifest_lists_traversal", BackupProblem.NOT_A_BACKUP),
            ("manifest_not_json", BackupProblem.NOT_A_BACKUP),
            ("wrong_format_name", BackupProblem.NOT_A_BACKUP),
            ("database_garbage", BackupProblem.DATABASE_INVALID),
            ("other_profile_id", BackupProblem.DATABASE_INVALID),
            ("other_schema_version", BackupProblem.DATABASE_INVALID),
        ],
    )
    def test_rejected_and_nothing_is_left(
        self,
        tmp_path: Path,
        source: _Profile,
        backup_file: Path,
        mutation: str,
        problem: BackupProblem,
    ) -> None:
        archive_path = source.archive_paths[0]
        mutations: dict[str, _Mutation] = {
            "flip_archive_byte": _damage_archive_file(source),
            "manifest_wrong_size": lambda e: _edit_manifest(
                e,
                lambda d: d["files"][0].update(size_bytes=1),
            ),
            "manifest_missing": lambda e: e.pop(MANIFEST_NAME),
            "extra_entry": lambda e: e.update({"archivio/2026/extra.fit": b"x"}),
            "entry_missing": lambda e: e.pop(archive_path),
            "traversal_entry": lambda e: e.update({"../outside.txt": b"x"}),
            "manifest_lists_traversal": lambda e: _edit_manifest(
                e,
                lambda d: d["files"].append(
                    {"path": "../outside", "size_bytes": 1, "sha256": "0" * 64}
                ),
            ),
            "manifest_not_json": lambda e: e.update({MANIFEST_NAME: b"not json"}),
            "wrong_format_name": lambda e: _edit_manifest(e, lambda d: d.update(format="zip")),
            "database_garbage": _garbage_database,
            "other_profile_id": lambda e: _edit_manifest(
                e, lambda d: d.update(profile_id=str(uuid.UUID(int=1 << 70 | 4 << 76 | 2 << 62)))
            ),
            "other_schema_version": lambda e: _edit_manifest(
                e, lambda d: d.update(schema_version=1)
            ),
        }
        _rewrite(backup_file, mutations[mutation])
        target = _restore_target(tmp_path)

        with pytest.raises(BackupInvalidError) as caught:
            FileProfileBackup().restore(backup_file, target)

        assert caught.value.problem is problem
        assert list(target.iterdir()) == []

    def test_newer_manifest_format(self, tmp_path: Path, backup_file: Path) -> None:
        _rewrite(
            backup_file,
            lambda e: _edit_manifest(
                e, lambda d: d.update(format_version=BACKUP_FORMAT_VERSION + 1)
            ),
        )
        target = _restore_target(tmp_path)

        with pytest.raises(BackupTooNewError):
            FileProfileBackup().restore(backup_file, target)

        assert list(target.iterdir()) == []

    def test_database_newer_than_the_app(self, tmp_path: Path, backup_file: Path) -> None:
        def newer(entries: dict[str, bytes]) -> None:
            scratch = tmp_path / "scratch.sqlite3"
            scratch.write_bytes(entries[PROFILE_DATABASE_NAME])
            with closing(sqlite3.connect(scratch)) as connection:
                connection.execute("PRAGMA user_version = 999")
            entries[PROFILE_DATABASE_NAME] = scratch.read_bytes()
            _fix_manifest_hash(entries, PROFILE_DATABASE_NAME)
            _edit_manifest(entries, lambda d: d.update(schema_version=999))

        _rewrite(backup_file, newer)
        target = _restore_target(tmp_path)

        with pytest.raises(BackupTooNewError):
            FileProfileBackup().restore(backup_file, target)

        assert list(target.iterdir()) == []

    @pytest.mark.parametrize("keep", [0, 10, 100, 1000, -30])
    def test_truncated_file(self, tmp_path: Path, backup_file: Path, keep: int) -> None:
        data = backup_file.read_bytes()
        backup_file.write_bytes(data[:keep])
        target = _restore_target(tmp_path)

        with pytest.raises(BackupInvalidError):
            FileProfileBackup().restore(backup_file, target)

        assert list(target.iterdir()) == []

    def test_a_damaged_byte_inside_the_compressed_stream(
        self, tmp_path: Path, backup_file: Path
    ) -> None:
        data = bytearray(backup_file.read_bytes())
        data[len(data) // 3] ^= 0xFF
        backup_file.write_bytes(bytes(data))
        target = _restore_target(tmp_path)

        with pytest.raises(BackupInvalidError):
            FileProfileBackup().restore(backup_file, target)

        assert list(target.iterdir()) == []

    @pytest.mark.parametrize("content", [b"", b"PK\x05\x06" + bytes(18), b"x" * 5000])
    def test_files_that_are_not_zips(self, tmp_path: Path, content: bytes) -> None:
        fake = tmp_path / "fake.ponosbackup"
        fake.write_bytes(content)
        target = _restore_target(tmp_path)

        with pytest.raises(BackupInvalidError) as caught:
            FileProfileBackup().restore(fake, target)

        assert caught.value.problem is BackupProblem.NOT_A_BACKUP

    def test_a_backup_inside_a_backup_is_not_a_backup(
        self, tmp_path: Path, backup_file: Path
    ) -> None:
        outer = tmp_path / "outer.ponosbackup"
        with zipfile.ZipFile(outer, "w") as archive:
            archive.write(backup_file, "inner.ponosbackup")
        target = _restore_target(tmp_path)

        with pytest.raises(BackupInvalidError):
            FileProfileBackup().restore(outer, target)

        assert list(target.iterdir()) == []


class TestRestoreLimits:
    def test_more_entries_than_allowed(self, tmp_path: Path, backup_file: Path) -> None:
        target = _restore_target(tmp_path)

        with pytest.raises(BackupInvalidError) as caught:
            FileProfileBackup(BackupLimits(max_entries=2)).restore(backup_file, target)

        assert caught.value.problem is BackupProblem.LIMIT
        assert list(target.iterdir()) == []

    def test_archive_file_larger_than_allowed(self, tmp_path: Path, backup_file: Path) -> None:
        target = _restore_target(tmp_path)

        with pytest.raises(BackupInvalidError):
            FileProfileBackup(BackupLimits(max_file_bytes=100)).restore(backup_file, target)

        assert list(target.iterdir()) == []

    def test_total_size_beyond_the_limit(self, tmp_path: Path, backup_file: Path) -> None:
        target = _restore_target(tmp_path)

        with pytest.raises(BackupInvalidError):
            FileProfileBackup(BackupLimits(max_total_bytes=1000)).restore(backup_file, target)

        assert list(target.iterdir()) == []

    def test_decompression_bomb_is_refused_before_extracting(
        self, tmp_path: Path, backup_file: Path
    ) -> None:
        def bomb(entries: dict[str, bytes]) -> None:
            path = next(name for name in entries if name.endswith(".fit"))
            entries[path] = bytes(20_000_000)  # compresses to about 20 KB: ratio 1000
            _fix_manifest_hash(entries, path)

        _rewrite(backup_file, bomb)
        target = _restore_target(tmp_path)

        with pytest.raises(BackupInvalidError) as caught:
            FileProfileBackup(BackupLimits(max_ratio=100)).restore(backup_file, target)

        assert caught.value.problem is BackupProblem.LIMIT
        assert list(target.iterdir()) == []
