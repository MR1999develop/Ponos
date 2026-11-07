# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""T-DATI-03 and T-DATI-04 on the real stack; profile folder damaged while it is open.

Integrity: a FIT of the archive altered in every plausible way is reported (never read to
compute a load) and the other activities keep working. Lock: refusal and takeover of the
lock of a dead process, from the use case that uses the interface.
"""

from __future__ import annotations

import contextlib
import ctypes
import shutil
import sys
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from ponos_coach.activities.decoding import decode_stored_activity
from ponos_coach.activities.domain import Activity, ActivityRecord, FileFormat
from ponos_coach.activities.use_cases import ImportStatus
from ponos_coach.load.use_cases import ExclusionReason
from ponos_coach.profiles.ports import (
    NotAProfileFolderError,
    ProfileCorruptError,
    ProfileInUseError,
    ProfileNotFoundError,
)
from ponos_coach.profiles.use_cases import DeleteProfileFolder
from tests.collaudo.processes import Worker
from tests.collaudo.support import (
    RealProfile,
    create_profile,
    open_profile_from_folder,
    outcomes,
    stored_files,
)
from tests.fixtures.generators.import_files import fit_run

FILES = 4


def profile_with_runs(tmp_path: Path) -> tuple[RealProfile, Path]:
    profile = create_profile(tmp_path)
    source = tmp_path / "da-importare"
    source.mkdir()
    for number in range(FILES):
        (source / f"corsa-{number}.fit").write_bytes(fit_run(number))
    summary = profile.import_folder(source)
    assert summary.count(ImportStatus.IMPORTED) == FILES
    return profile, source


def file_of(profile: RealProfile, record: ActivityRecord) -> Path:
    return profile.folder / "archivio" / record.archived.relative_path


class CountingDecoder:
    """Decodes like the app and remembers how many times it was called."""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, data: bytes, file_format: FileFormat) -> Activity:
        self.calls += 1
        return decode_stored_activity(data, file_format)


def flip_a_byte(path: Path) -> None:
    data = bytearray(path.read_bytes())
    data[len(data) // 2] ^= 0x01
    path.write_bytes(bytes(data))


def truncate_half(path: Path) -> None:
    path.write_bytes(path.read_bytes()[: path.stat().st_size // 2])


def append_a_byte(path: Path) -> None:
    path.write_bytes(path.read_bytes() + b"\x00")


def empty_it(path: Path) -> None:
    path.write_bytes(b"")


def swap_for_another_valid_fit(path: Path) -> None:
    path.write_bytes(fit_run(99))


def replace_with_a_folder(path: Path) -> None:
    path.unlink()
    path.mkdir()


ALTERATIONS: list[tuple[str, Callable[[Path], None], ExclusionReason]] = [
    ("un-bit", flip_a_byte, ExclusionReason.FILE_ALTERED),
    ("troncato", truncate_half, ExclusionReason.FILE_ALTERED),
    ("un-byte-in-piu", append_a_byte, ExclusionReason.FILE_ALTERED),
    ("vuoto", empty_it, ExclusionReason.FILE_ALTERED),
    ("altro-fit-valido", swap_for_another_valid_fit, ExclusionReason.FILE_ALTERED),
    ("cartella-al-posto-del-file", replace_with_a_folder, ExclusionReason.FILE_ALTERED),
    ("cancellato", lambda p: p.unlink(), ExclusionReason.FILE_MISSING),
]


# --- T-DATI-03 -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("alter", "reason"), [(a, r) for _, a, r in ALTERATIONS], ids=[n for n, _, _ in ALTERATIONS]
)
def test_t_dati_03_an_altered_archived_file_is_reported_and_never_used(
    tmp_path: Path, alter: Callable[[Path], None], reason: ExclusionReason
) -> None:
    profile, _ = profile_with_runs(tmp_path)
    try:
        victim = profile.records()[1]
        alter(file_of(profile, victim))
        decoder = CountingDecoder()

        overview = profile.overview(decoder)

        assert [(e.identity, e.reason) for e in overview.excluded] == [(victim.identity, reason)]
        assert len(overview.activities) == FILES - 1
        assert victim.identity not in {a.identity for a in overview.activities}
        assert decoder.calls == FILES - 1  # the altered file never reached the decoder
    finally:
        profile.close()


def test_t_dati_03_the_reading_api_itself_refuses_an_altered_file(tmp_path: Path) -> None:
    profile, _ = profile_with_runs(tmp_path)
    try:
        victim = profile.records()[0]
        flip_a_byte(file_of(profile, victim))

        with pytest.raises(Exception, match=r"SHA-256|size") as raised:
            profile.archive.read(victim.archived, 10**9)
        assert type(raised.value).__name__ == "ArchiveIntegrityError"
        with pytest.raises(Exception, match=r"SHA-256|size") as verified:
            profile.archive.verify(victim.archived)
        assert type(verified.value).__name__ == "ArchiveIntegrityError"
    finally:
        profile.close()


def test_t_dati_03_a_file_altered_to_a_different_valid_activity_never_counts_for_the_old_one(
    tmp_path: Path,
) -> None:
    """Even if the new content is a valid FIT (and with the hash recalculated by hand in the index
    one does not get here), the decoded identity must stay the record's."""
    profile, _ = profile_with_runs(tmp_path)
    try:
        before = profile.overview()
        victim = profile.records()[2]
        swap_for_another_valid_fit(file_of(profile, victim))
        profile.session.load_cache.clear()  # without cache the archive is read again

        after = profile.overview()

        assert victim.identity in {e.identity for e in after.excluded}
        assert len(after.activities) == len(before.activities) - 1
    finally:
        profile.close()


@pytest.mark.parametrize("alter", [flip_a_byte, lambda p: p.unlink()], ids=["alterato", "mancante"])
def test_d_f1_03_importing_the_original_again_repairs_a_bad_archived_file(
    tmp_path: Path, alter: Callable[[Path], None]
) -> None:
    profile, source = profile_with_runs(tmp_path)
    try:
        victim = profile.records()[1]
        alter(file_of(profile, victim))

        profile.import_folder(source)

        profile.archive.verify(victim.archived)  # the archive is intact again
    finally:
        profile.close()


def test_t_dati_03_storing_the_same_bytes_again_repairs_the_archive_atomically(
    tmp_path: Path,
) -> None:
    """The archive repair mechanism works; D-F1-03 is that nobody calls it."""
    profile, _ = profile_with_runs(tmp_path)
    try:
        victim = profile.records()[1]
        flip_a_byte(file_of(profile, victim))

        profile.archive.store(fit_run(1), FileFormat.FIT, victim.start)

        profile.archive.verify(victim.archived)
    finally:
        profile.close()


# --- T-DATI-04 -----------------------------------------------------------------------------------


def test_t_dati_04_a_second_open_of_the_same_profile_is_refused_and_changes_nothing(
    tmp_path: Path,
) -> None:
    first, _ = profile_with_runs(tmp_path)
    try:
        ports = first.ports
        registry_before = [(e.profile_id, e.last_used_utc) for e in ports.registry.list_entries()]
        database = first.folder / "ponos.sqlite3"
        size_before = database.stat().st_size

        with pytest.raises(ProfileInUseError):
            open_profile_from_folder(ports, first.folder)

        assert [(e.profile_id, e.last_used_utc) for e in ports.registry.list_entries()] == (
            registry_before
        )
        assert database.stat().st_size == size_before
        assert len(first.records()) == FILES  # the first instance is not disturbed
    finally:
        first.close()


def test_t_dati_04_a_profile_held_by_another_process_cannot_be_opened_or_deleted(
    tmp_path: Path,
) -> None:
    profile, _source = profile_with_runs(tmp_path)
    folder, ports = profile.folder, profile.ports
    profile_id = profile.session.info.profile_id
    profile.close()
    other = tmp_path / "altro-file"
    other.mkdir()
    (other / "extra.fit").write_bytes(fit_run(50))

    with Worker(
        "import", folder, other, "op:4"
    ) as worker:  # holds the lock, stopped halfway through a write
        worker.expect_ready()
        worker.read_until("blocked")

        with pytest.raises(ProfileInUseError):
            open_profile_from_folder(ports, folder)
        with pytest.raises(ProfileInUseError):
            DeleteProfileFolder(ports)(profile_id)
        assert (folder / "ponos.sqlite3").exists()  # the deletion touched nothing
        assert len(stored_files(folder)) == FILES

        worker.kill()  # crash: the lock stays in the file but not in the system

    reopened = open_profile_from_folder(ports, folder)  # lock taken over, WAL recovered
    try:
        assert len(reopened.records()) == FILES
        assert reopened.import_folder(other).count(ImportStatus.IMPORTED) == 1
    finally:
        reopened.close()


# --- Profile folder damaged while the profile is open --------------------------------------------


@contextlib.contextmanager
def unreadable(files: list[Path]) -> Iterator[None]:
    """Makes the files unreadable: on Windows it keeps them open without sharing, elsewhere
    0o000."""
    if sys.platform == "win32":
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateFileW.restype = ctypes.c_void_p
        kernel32.CreateFileW.argtypes = [
            ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p,
            ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p,
        ]  # fmt: skip
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        generic_read, open_existing, invalid = 0x80000000, 3, ctypes.c_void_p(-1).value
        handles: list[int] = []
        try:
            for path in files:
                handle = kernel32.CreateFileW(
                    str(path), generic_read, 0, None, open_existing, 0, None
                )
                assert handle != invalid, ctypes.WinError(ctypes.get_last_error())
                handles.append(handle)
            yield
        finally:
            for handle in handles:
                kernel32.CloseHandle(handle)
    else:
        modes = {path: path.stat().st_mode & 0o777 for path in files}
        for path in files:
            path.chmod(0)
        try:
            yield
        finally:
            for path, mode in modes.items():
                path.chmod(mode)


def test_the_archive_folder_deleted_while_open_excludes_the_activities_without_crashing(
    tmp_path: Path,
) -> None:
    profile, source = profile_with_runs(tmp_path)
    try:
        shutil.rmtree(profile.folder / "archivio")
        profile.session.load_cache.clear()

        overview = profile.overview()

        assert overview.activities == ()
        assert {e.reason for e in overview.excluded} == {ExclusionReason.FILE_MISSING}
        assert len(overview.excluded) == FILES

        again = profile.import_folder(source)  # the archive recreates itself

        assert {r.status for r in again.results} == {ImportStatus.REPAIRED}  # D-F1-03
    finally:
        profile.close()


def test_a_new_file_imported_after_the_archive_folder_vanished_is_stored_and_listed(
    tmp_path: Path,
) -> None:
    profile, _ = profile_with_runs(tmp_path)
    try:
        shutil.rmtree(profile.folder / "archivio")
        extra = tmp_path / "nuovo.fit"
        extra.write_bytes(fit_run(70))

        summary = profile.import_files([extra])

        assert outcomes(summary) == {"nuovo.fit": (ImportStatus.IMPORTED, None)}
        assert len(stored_files(profile.folder)) == 1
        assert len(profile.records()) == FILES + 1
    finally:
        profile.close()


def test_the_archive_root_replaced_by_a_file_makes_imports_fail_with_a_reason_not_a_crash(
    tmp_path: Path,
) -> None:
    profile, _ = profile_with_runs(tmp_path)
    try:
        shutil.rmtree(profile.folder / "archivio")
        (profile.folder / "archivio").write_bytes(b"non sono una cartella")
        extra = tmp_path / "nuovo.fit"
        extra.write_bytes(fit_run(71))

        summary = profile.import_files([extra])

        ((status, issue),) = outcomes(summary).values()
        assert status is ImportStatus.FAILED
        assert issue is not None
        assert issue.value == "storage_failed"
        assert len(profile.records()) == FILES  # no record without a file
        profile.session.load_cache.clear()
        assert len(profile.overview().excluded) == FILES  # and the rest reports, does not fall
    finally:
        (profile.folder / "archivio").unlink(missing_ok=True)
        profile.close()


def test_archived_files_made_unreadable_while_open_are_excluded_then_count_again(
    tmp_path: Path,
) -> None:
    profile, _ = profile_with_runs(tmp_path)
    try:
        profile.session.load_cache.clear()
        extra = tmp_path / "nuovo.fit"
        extra.write_bytes(fit_run(72))

        with unreadable(stored_files(profile.folder)):
            overview = profile.overview()
            summary = profile.import_files([extra])

        assert overview.activities == ()
        assert len(overview.excluded) == FILES  # no load computed from unreadable files
        assert {e.reason for e in overview.excluded} <= {
            ExclusionReason.UNREADABLE,
            ExclusionReason.FILE_MISSING,
            ExclusionReason.FILE_ALTERED,
        }
        assert outcomes(summary) == {"nuovo.fit": (ImportStatus.IMPORTED, None)}  # new file
        assert len(profile.records()) == FILES + 1
        # Once readable again, all the activities count again.
        assert len(profile.overview().activities) == FILES + 1
    finally:
        profile.close()


def test_a_profile_folder_deleted_from_outside_while_open_leaves_the_session_closable(
    tmp_path: Path,
) -> None:
    profile, _ = profile_with_runs(tmp_path)
    folder, ports = profile.folder, profile.ports
    shutil.rmtree(folder, ignore_errors=True)  # on Windows the open files (database, lock) remain

    try:
        extra = tmp_path / "nuovo.fit"
        extra.write_bytes(fit_run(73))
        summary = profile.import_files([extra])
        assert len(summary.results) == 1  # an outcome, never an exception outside the summary
    finally:
        profile.close()  # closing does not even raise if the folder no longer exists

    try:
        reopened = open_profile_from_folder(ports, folder)
    except (ProfileNotFoundError, NotAProfileFolderError, ProfileCorruptError):
        return  # port errors: the interface knows how to describe them
    reopened.close()
