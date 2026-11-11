# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""T-DATI-02, T-DATI-04, T-IMP-03: a real kill halfway through an import of many files, then the
resume.

A subprocess opens the profile, takes the lock and imports a folder of FIT files with the real
archive
and index; the test kills it (without cleanup) at a precise point of the write, or at an
arbitrary point after N files, and then reopens the profile as the app would on restart.

Oracles: lock free; database openable and intact; every record has its file with the right SHA-256
and
size; the records are a prefix of the import order (no gap); at most one
orphan temporary file and one file not yet referenced; resuming the import completes everything
without
duplicates; a second import changes nothing.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from ponos_coach.activities.domain import ActivityRecord
from ponos_coach.activities.use_cases import ImportStatus
from ponos_coach.platform.profile_folders import LocalProfileFolders
from ponos_coach.profiles.domain import FolderKind
from ponos_coach.storage.file_archive import FileArchive
from ponos_coach.storage.sqlite_profile import SqliteProfileStorage
from tests.collaudo.processes import Worker
from tests.collaudo.support import (
    create_profile,
    open_profile_from_folder,
    real_ports,
    stored_files,
    temporaries,
)
from tests.fixtures.generators.import_files import fit_run, run_start

FILES = 6
# Archive write operations: the first file does 10 (creates two folders), the others 8.
OPS_IN_RUN = 10 + 8 * (FILES - 1)
REPRESENTATIVE_OPS = (4, 5, 7, 8, 14, 17)  # before write, fsync, rename, directory fsync...


def make_source(tmp_path: Path) -> Path:
    source = tmp_path / "da-importare"
    source.mkdir()
    for number in range(FILES):
        (source / f"corsa-{number:02d}.fit").write_bytes(fit_run(number))
    return source


def tree_digest(folder: Path) -> dict[str, str]:
    return {
        str(p.relative_to(folder)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(folder.rglob("*"))
        if p.is_file()
    }


def kill_at(profile_folder: Path, source: Path, point: str) -> None:
    """Starts the import, lets it stop at the given point and kills it."""
    with Worker("import", profile_folder, source, point) as worker:
        worker.expect_ready()
        worker.read_until("blocked")
        worker.kill()


def committed_prefix(records: list[ActivityRecord]) -> int:
    """How many records there are, checking they really are the first ones in import order."""
    expected = [run_start(number) for number in range(len(records))]
    assert [record.start for record in records] == expected, "gap in the imported sequence"
    return len(records)


def check_after_crash(profile_folder: Path, source: Path, tmp_path: Path) -> int:
    """Reopens the profile as at restart, checks consistency and resumes the import."""
    folders = LocalProfileFolders(lambda _folder: FolderKind.LOCAL)
    folders.acquire_lock(profile_folder).release()  # T-DATI-04: the dead process's lock is free
    opened = SqliteProfileStorage().open(profile_folder)  # WAL recovered, integrity verified
    try:
        records = list(opened.activities.list_by_start())
    finally:
        opened.close()
    archive = FileArchive.in_profile_folder(profile_folder)
    for record in records:
        archive.verify(record.archived)  # no record without a file, no altered file
    done = committed_prefix(records)
    referenced = {profile_folder / "archivio" / r.archived.relative_path for r in records}
    assert referenced <= set(stored_files(profile_folder))
    assert len(set(stored_files(profile_folder)) - referenced) <= 1  # at most the one in flight
    assert len(temporaries(profile_folder)) <= 1  # at most the one in flight
    assert len(archive.list_orphan_temporaries()) == len(temporaries(profile_folder))
    return done


def resume_and_verify(profile_folder: Path, source: Path, tmp_path: Path, done: int) -> None:
    session = open_profile_from_folder(real_ports(tmp_path / "config"), profile_folder)
    try:
        archive = session.archive
        archive.remove_orphan_temporaries()
        resumed = session.import_folder(source)
        assert resumed.count(ImportStatus.ALREADY_PRESENT) == done
        assert resumed.count(ImportStatus.IMPORTED) == FILES - done
        assert resumed.count(ImportStatus.FAILED) == 0
        records = session.records()
        assert len(records) == FILES
        assert len({r.identity for r in records}) == FILES  # no duplicate
        assert committed_prefix(list(records)) == FILES
        for record in records:
            archive.verify(record.archived)
    finally:
        session.close()
    assert len(stored_files(profile_folder)) == FILES  # no orphan file, no duplicate
    assert temporaries(profile_folder) == []
    before = tree_digest(profile_folder / "archivio")

    again = open_profile_from_folder(real_ports(tmp_path / "config"), profile_folder)
    try:
        repeated = again.import_folder(source)
    finally:
        again.close()
    assert {r.status for r in repeated.results} == {ImportStatus.ALREADY_PRESENT}  # T-IMP-03
    assert tree_digest(profile_folder / "archivio") == before


def run_crash_case(tmp_path: Path, point: str) -> int:
    profile = create_profile(tmp_path)
    profile_folder = profile.folder
    profile.close()
    source = make_source(tmp_path)
    kill_at(profile_folder, source, point)
    done = check_after_crash(profile_folder, source, tmp_path)
    resume_and_verify(profile_folder, source, tmp_path, done)
    return done


@pytest.mark.parametrize("operation", REPRESENTATIVE_OPS)
def test_t_dati_02_kill_before_a_write_operation_then_resume(
    tmp_path: Path, operation: int
) -> None:
    done = run_crash_case(tmp_path, f"op:{operation}")

    # Up to operation 7 of the first file nothing is committed; from the 11th on the first is.
    if operation < 10:
        assert done == 0
    else:
        assert done >= 1


@pytest.mark.parametrize("point", ["before_add:3", "after_add:3"])
def test_t_dati_02_kill_between_the_archive_and_the_commit_loses_only_the_file_in_flight(
    tmp_path: Path, point: str
) -> None:
    done = run_crash_case(tmp_path, point)

    assert done == 2  # the third file is on disk, but its record was not committed


def test_t_dati_02_kill_after_some_files_at_an_arbitrary_instant_then_resume(
    tmp_path: Path,
) -> None:
    profile = create_profile(tmp_path)
    profile_folder = profile.folder
    profile.close()
    source = make_source(tmp_path)

    with Worker("import", profile_folder, source, "none") as worker:
        worker.expect_ready()
        worker.read_until("progress 2")
        worker.kill()  # the process keeps importing until it dies: arbitrary instant

    done = check_after_crash(profile_folder, source, tmp_path)
    assert done >= 2
    resume_and_verify(profile_folder, source, tmp_path, done)


@pytest.mark.collaudo
@pytest.mark.parametrize("operation", range(1, OPS_IN_RUN + 1))
def test_t_dati_02_collaudo_kill_before_every_write_operation_of_the_run(
    tmp_path: Path, operation: int
) -> None:
    run_crash_case(tmp_path, f"op:{operation}")


@pytest.mark.collaudo
@pytest.mark.parametrize(
    "point", [f"{kind}:{n}" for kind in ("before_add", "after_add") for n in range(1, FILES + 1)]
)
def test_t_dati_02_collaudo_kill_around_every_commit_of_the_run(tmp_path: Path, point: str) -> None:
    done = run_crash_case(tmp_path, point)

    assert done == int(point.split(":")[1]) - 1


@pytest.mark.collaudo
@pytest.mark.parametrize("after_files", range(1, FILES))
def test_t_dati_02_collaudo_kill_after_n_files_at_an_arbitrary_instant(
    tmp_path: Path, after_files: int
) -> None:
    profile = create_profile(tmp_path)
    profile_folder = profile.folder
    profile.close()
    source = make_source(tmp_path)

    with Worker("import", profile_folder, source, "none") as worker:
        worker.expect_ready()
        worker.read_until(f"progress {after_files}")
        worker.kill()

    done = check_after_crash(profile_folder, source, tmp_path)
    assert done >= after_files
    resume_and_verify(profile_folder, source, tmp_path, done)
