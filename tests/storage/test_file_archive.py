# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Archivio sul file system: contratto, interruzioni (T-DATI-02), integrità (T-DATI-03)."""

from __future__ import annotations

import hashlib
import os
import secrets
import sys
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from ponos_coach.activities.domain import ArchivedFile, FileFormat
from ponos_coach.activities.ports import (
    ArchiveFileMissingError,
    ArchiveIntegrityError,
    ArchiveWriteError,
)
from ponos_coach.storage.archive_files import OsWriteOps, WriteOps
from ponos_coach.storage.file_archive import FileArchive
from tests.contracts.contract_archive import CONTENT, START, ActivityArchiveContract
from tests.contracts.harness import ArchiveHarness

CAP = 1 << 30


class _DiskArchiveHarness:
    def __init__(self, root: Path) -> None:
        self.root = root
        self._archive = FileArchive(root)

    @property
    def archive(self) -> FileArchive:
        return self._archive

    def plant_orphan_temporary(self) -> None:
        folder = self.root / "2026"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f".tmp-{secrets.token_hex(16)}").write_bytes(b"meta")

    def corrupt(self, file: ArchivedFile) -> None:
        path = self.root / file.relative_path
        data = bytearray(path.read_bytes())
        data[0] ^= 0xFF
        path.write_bytes(bytes(data))

    def delete(self, file: ArchivedFile) -> None:
        (self.root / file.relative_path).unlink()


class TestFileArchiveContract(ActivityArchiveContract):
    @pytest.fixture
    def harness(self, tmp_path: Path) -> ArchiveHarness:
        return _DiskArchiveHarness(tmp_path / "archivio")


# --- Iniezione di guasti ----------------------------------------------------------------------


class SimulatedCrashError(BaseException):
    """Il processo "muore" in quel punto: nessun codice di pulizia dell'archivio lo cattura."""


class FaultyOps:
    """`OsWriteOps` che fallisce (o muore) alla `fail_at`-esima operazione, contandole tutte."""

    def __init__(
        self,
        fail_at: int | None = None,
        *,
        crash: bool = False,
        write_chunk: int | None = None,
        unlink_fails: bool = False,
    ) -> None:
        self._real = OsWriteOps()
        self._fail_at = fail_at
        self._crash = crash
        self._write_chunk = write_chunk
        self._unlink_fails = unlink_fails
        self.names: list[str] = []
        self._open: set[int] = set()
        self._dead = False

    def _release_descriptors(self) -> None:
        for descriptor in self._open:
            os.close(descriptor)
        self._open.clear()

    def _tick(self, name: str) -> None:
        if self._dead:  # i blocchi `finally` di un processo morto non fanno I/O
            raise SimulatedCrashError(name)
        self.names.append(name)
        if len(self.names) == self._fail_at:
            if self._crash:
                self._release_descriptors()  # un processo morto non tiene file aperti
                self._dead = True
                raise SimulatedCrashError(name)
            raise OSError(f"guasto simulato in {name}")

    def make_dir(self, path: Path) -> bool:
        self._tick("make_dir")
        return self._real.make_dir(path)

    def create_exclusive(self, path: Path) -> int:
        self._tick("create_exclusive")
        descriptor = self._real.create_exclusive(path)
        self._open.add(descriptor)
        return descriptor

    def write(self, descriptor: int, data: memoryview) -> int:
        self._tick("write")
        if self._write_chunk is not None:
            data = data[: self._write_chunk]
        return self._real.write(descriptor, data)

    def fsync(self, descriptor: int) -> None:
        self._tick("fsync")
        self._real.fsync(descriptor)

    def close(self, descriptor: int) -> None:
        if self._dead:
            raise SimulatedCrashError("close")
        self._open.discard(descriptor)
        self._real.close(descriptor)  # anche un close che fallisce rilascia il descrittore
        self._tick("close")

    def replace(self, source: Path, destination: Path) -> None:
        self._tick("replace")
        self._real.replace(source, destination)

    def sync_dir(self, path: Path) -> None:
        self._tick("sync_dir")
        self._real.sync_dir(path)

    def unlink(self, path: Path) -> None:
        if self._dead:
            raise SimulatedCrashError("unlink")
        if self._unlink_fails:
            raise OSError("unlink simulato fallito")
        self._real.unlink(path)


def _final_path(root: Path, archived: ArchivedFile) -> Path:
    return root / archived.relative_path


def _store_with(root: Path, ops: WriteOps) -> ArchivedFile:
    return FileArchive(root, ops).store(CONTENT, FileFormat.FIT, START)


def _record_operations(root: Path) -> list[str]:
    ops = FaultyOps()
    _store_with(root, ops)
    return ops.names


def _temporaries(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob(".tmp-*"))


def test_a_full_write_runs_the_operations_in_the_durable_order(tmp_path: Path) -> None:
    names = _record_operations(tmp_path / "archivio")

    # cartelle, temporaneo, scrittura, fsync, close, rinomina, fsync delle cartelle
    assert names[:2] == ["make_dir", "make_dir"]
    body = names[2:]
    assert body[:5] == ["create_exclusive", "write", "fsync", "close", "replace"]
    assert set(body[5:]) == {"sync_dir"}


def test_interruption_with_an_error_at_every_step_leaves_nothing_half_written(
    tmp_path: Path,
) -> None:
    """T-DATI-02, errore di I/O all'N-esima operazione: la pulizia toglie il temporaneo."""
    total = len(_record_operations(tmp_path / "count"))
    replace_index = _record_operations(tmp_path / "count2").index("replace") + 1

    for step in range(1, total + 1):
        root = tmp_path / f"error-{step}"
        with pytest.raises(ArchiveWriteError):
            _store_with(root, FaultyOps(step))

        final = list(root.glob("*/*.fit"))
        assert bool(final) == (step > replace_index), step
        assert _temporaries(root) == [], step
        for path in final:  # se il file c'è, è intero
            assert path.read_bytes() == CONTENT
        assert FileArchive(root).list_orphan_temporaries() == []


def test_crash_at_every_step_leaves_at_most_an_orphan_temporary_that_is_removed(
    tmp_path: Path,
) -> None:
    """T-DATI-02, morte del processo all'N-esima operazione: niente file a metà."""
    total = len(_record_operations(tmp_path / "count"))
    replace_index = _record_operations(tmp_path / "count2").index("replace") + 1

    for step in range(1, total + 1):
        root = tmp_path / f"crash-{step}"
        with pytest.raises(SimulatedCrashError):
            _store_with(root, FaultyOps(step, crash=True))

        final = list(root.glob("*/*.fit"))
        assert bool(final) == (step > replace_index), step
        for path in final:
            assert path.read_bytes() == CONTENT
        orphans = _temporaries(root)
        assert len(orphans) <= 1
        if step > replace_index:
            assert orphans == []  # dopo la rinomina il temporaneo non esiste più

        reopened = FileArchive(root)  # "riavvio"
        assert len(reopened.list_orphan_temporaries()) == len(orphans)
        assert reopened.remove_orphan_temporaries() == len(orphans)
        assert reopened.list_orphan_temporaries() == []
        assert _temporaries(root) == []

        archived = reopened.store(CONTENT, FileFormat.FIT, START)  # e si riparte
        assert reopened.read(archived, len(CONTENT)) == CONTENT


def test_a_failed_cleanup_leaves_an_orphan_that_is_reported_and_removed(tmp_path: Path) -> None:
    root = tmp_path / "archivio"
    replace_step = _record_operations(tmp_path / "count").index("replace") + 1

    with pytest.raises(ArchiveWriteError):
        _store_with(root, FaultyOps(replace_step, unlink_fails=True))

    archive = FileArchive(root)
    assert len(archive.list_orphan_temporaries()) == 1
    assert archive.remove_orphan_temporaries() == 1


def test_partial_writes_are_completed(tmp_path: Path) -> None:
    root = tmp_path / "archivio"
    ops = FaultyOps(write_chunk=7)

    archived = _store_with(root, ops)

    assert ops.names.count("write") == -(-len(CONTENT) // 7)
    assert FileArchive(root).read(archived, len(CONTENT)) == CONTENT


def test_a_failed_write_does_not_touch_the_files_already_archived(tmp_path: Path) -> None:
    root = tmp_path / "archivio"
    archive = FileArchive(root)
    kept = archive.store(b"gia archiviato", FileFormat.FIT, START)

    with pytest.raises(ArchiveWriteError):
        _store_with(root, FaultyOps(4))

    assert archive.read(kept, 100) == b"gia archiviato"
    assert archive.list_orphan_temporaries() == []


# --- Integrità (T-DATI-03) ----------------------------------------------------------------------


def test_altered_file_is_reported_never_used_silently(tmp_path: Path) -> None:
    harness = _DiskArchiveHarness(tmp_path / "archivio")
    archived = harness.archive.store(CONTENT, FileFormat.FIT, START)
    harness.corrupt(archived)

    with pytest.raises(ArchiveIntegrityError):
        harness.archive.read(archived, CAP)
    with pytest.raises(ArchiveIntegrityError):
        harness.archive.verify(archived)


@pytest.mark.parametrize("new_content", [b"", CONTENT[:-1], CONTENT + b"x"])
def test_file_with_a_different_size_is_reported(tmp_path: Path, new_content: bytes) -> None:
    harness = _DiskArchiveHarness(tmp_path / "archivio")
    archived = harness.archive.store(CONTENT, FileFormat.FIT, START)
    (harness.root / archived.relative_path).write_bytes(new_content)

    with pytest.raises(ArchiveIntegrityError):
        harness.archive.verify(archived)
    with pytest.raises(ArchiveIntegrityError):
        harness.archive.read(archived, CAP)


def test_storing_again_repairs_an_altered_file_atomically(tmp_path: Path) -> None:
    harness = _DiskArchiveHarness(tmp_path / "archivio")
    archived = harness.archive.store(CONTENT, FileFormat.FIT, START)
    harness.corrupt(archived)

    again = harness.archive.store(CONTENT, FileFormat.FIT, START)

    assert again == archived
    harness.archive.verify(archived)
    assert harness.archive.list_orphan_temporaries() == []


def test_file_missing_from_an_existing_year_folder_is_reported(tmp_path: Path) -> None:
    harness = _DiskArchiveHarness(tmp_path / "archivio")
    archived = harness.archive.store(CONTENT, FileFormat.FIT, START)
    harness.delete(archived)

    with pytest.raises(ArchiveFileMissingError):
        harness.archive.verify(archived)


def test_verify_streams_a_file_larger_than_one_block(tmp_path: Path) -> None:
    content = os.urandom(2 * 1024 * 1024 + 5)
    archive = FileArchive(tmp_path / "archivio")
    archived = archive.store(content, FileFormat.GPX, START)

    archive.verify(archived)
    assert archive.read(archived, len(content)) == content


def _make_symlink(link: Path, target: Path, *, directory: bool) -> None:
    try:
        link.symlink_to(target, target_is_directory=directory)
    except (OSError, NotImplementedError):
        pytest.skip("collegamenti simbolici non permessi su questo sistema")


def test_a_link_in_place_of_the_file_is_never_followed(tmp_path: Path) -> None:
    harness = _DiskArchiveHarness(tmp_path / "archivio")
    archived = harness.archive.store(CONTENT, FileFormat.FIT, START)
    path = harness.root / archived.relative_path
    outside = tmp_path / "fuori.bin"
    outside.write_bytes(CONTENT)
    path.unlink()
    _make_symlink(path, outside, directory=False)

    with pytest.raises(ArchiveIntegrityError):
        harness.archive.read(archived, CAP)
    with pytest.raises(ArchiveWriteError):
        harness.archive.store(CONTENT, FileFormat.FIT, START)
    assert outside.read_bytes() == CONTENT


def test_a_link_in_place_of_the_year_folder_is_never_followed(tmp_path: Path) -> None:
    root = tmp_path / "archivio"
    root.mkdir()
    elsewhere = tmp_path / "altrove"
    elsewhere.mkdir()
    _make_symlink(root / "2026", elsewhere, directory=True)

    with pytest.raises(ArchiveWriteError):
        FileArchive(root).store(CONTENT, FileFormat.FIT, START)
    assert list(elsewhere.iterdir()) == []


# --- Nomi, permessi, contorni -------------------------------------------------------------------


def test_final_name_is_year_sha256_extension_and_nothing_else_is_left(tmp_path: Path) -> None:
    root = tmp_path / "archivio"
    start = datetime(2024, 12, 31, 23, 59, tzinfo=UTC)

    archived = FileArchive(root).store(CONTENT, FileFormat.GPX, start)

    digest = hashlib.sha256(CONTENT).hexdigest()
    assert str(archived.relative_path) == f"2024/{digest}.gpx"
    assert sorted(p.relative_to(root).as_posix() for p in root.rglob("*")) == [
        "2024",
        f"2024/{digest}.gpx",
    ]


def test_start_that_is_not_utc_is_refused(tmp_path: Path) -> None:
    local = datetime(2026, 1, 1, 0, 30, tzinfo=timezone(timedelta(hours=2)))
    with pytest.raises(ValueError, match="UTC"):
        FileArchive(tmp_path / "archivio").store(CONTENT, FileFormat.FIT, local)


def test_empty_content_is_stored_and_read_back(tmp_path: Path) -> None:
    archive = FileArchive(tmp_path / "archivio")

    archived = archive.store(b"", FileFormat.FIT, START)

    assert archived.size_bytes == 0
    assert archive.read(archived, 0) == b""


@pytest.mark.skipif(sys.platform == "win32", reason="permessi POSIX")
def test_files_and_folders_are_private_to_the_user_on_posix(tmp_path: Path) -> None:
    root = tmp_path / "archivio"

    archived = FileArchive(root).store(CONTENT, FileFormat.FIT, START)

    assert (root / archived.relative_path).stat().st_mode & 0o777 == 0o600
    assert (root / "2026").stat().st_mode & 0o777 == 0o700
    assert root.stat().st_mode & 0o777 == 0o700


def test_removing_orphans_never_touches_other_files(tmp_path: Path) -> None:
    harness = _DiskArchiveHarness(tmp_path / "archivio")
    archived = harness.archive.store(CONTENT, FileFormat.FIT, START)
    stranger = harness.root / "2026" / ".tmp-non-valido"
    stranger.write_bytes(b"x")
    harness.plant_orphan_temporary()

    assert harness.archive.remove_orphan_temporaries() == 1

    assert stranger.exists()
    harness.archive.verify(archived)


def test_listing_an_archive_that_does_not_exist_yet_is_empty(tmp_path: Path) -> None:
    archive = FileArchive(tmp_path / "archivio")

    assert archive.list_orphan_temporaries() == []
    assert archive.remove_orphan_temporaries() == 0


def test_archive_in_profile_folder_uses_the_layout_name(tmp_path: Path) -> None:
    archived = FileArchive.in_profile_folder(tmp_path).store(CONTENT, FileFormat.FIT, START)

    assert (tmp_path / "archivio" / archived.relative_path).is_file()


@given(
    content=st.binary(max_size=4096),
    file_format=st.sampled_from(FileFormat),
    year=st.integers(min_value=1, max_value=9999),
)
def test_store_then_read_round_trips_and_is_idempotent(
    tmp_path_factory: pytest.TempPathFactory, content: bytes, file_format: FileFormat, year: int
) -> None:
    root = tmp_path_factory.mktemp("prop") / "archivio"
    archive = FileArchive(root)
    start = datetime(year, 6, 1, tzinfo=UTC)

    first = archive.store(content, file_format, start)
    second = archive.store(content, file_format, start)

    assert first == second
    assert first.sha256_hex == hashlib.sha256(content).hexdigest()
    assert archive.read(first, len(content)) == content
    assert archive.list_orphan_temporaries() == []
