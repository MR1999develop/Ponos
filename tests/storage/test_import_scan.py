# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Sources of import from files and folders, ZIPs included (real disk, fake archive and index).

T-IMP-01 (mixed tree, exact summary), T-IMP-02 (caps at the limit and one step beyond),
T-IMP-03 (idempotence), T-IMP-04 (ZIP), T-SEC-02 (hostile ZIPs), T-SEC-04 (paths).
"""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

from ponos_coach.activities.domain import MAX_ACTIVITY_FILE_BYTES
from ponos_coach.activities.import_limits import ImportLimits
from ponos_coach.activities.ports import (
    ActivityIndex,
    ImportEntry,
    ImportEntryError,
    ImportIssue,
    ImportLimit,
    ImportLimitExceededError,
    ImportScan,
    ImportScanCancelledError,
    ImportSource,
    ImportSourceUnreadableError,
)
from ponos_coach.activities.use_cases import ImportStatus, ImportSummary, import_activities
from ponos_coach.storage.import_scan import FilesImportSource, FolderImportSource
from tests.activities.import_fakes import FixedClock, new_index
from tests.fakes.archive import InMemoryActivityArchive
from tests.fixtures.generators import import_files as imp

ARCHIVE_PATH = re.compile(r"\d{4}/[0-9a-f]{64}\.fit")
IS_WINDOWS = os.name == "nt"
MIB = 1024 * 1024


# --- Helpers ------------------------------------------------------------------------------------


def raw(path: Path) -> Path:
    """Path suitable for creating reserved names or long paths on Windows (prefix `\\\\?\\`)."""
    return Path("\\\\?\\" + str(path)) if IS_WINDOWS else path


def write(root: Path, files: dict[str, bytes]) -> None:
    for name, content in files.items():
        path = raw(root / name)
        os.makedirs(path.parent, exist_ok=True)  # noqa: PTH103
        path.write_bytes(content)


def issues(scan: ImportScan) -> dict[str, ImportIssue]:
    return {item.name: item.issue for item in scan.skipped}


def entry_names(scan: ImportScan) -> list[str]:
    return [entry.name for entry in scan.entries]


def folder(root: Path, **limits: int) -> FolderImportSource:
    return FolderImportSource(root, ImportLimits(**limits) if limits else None)


def import_all(
    source: ImportSource,
) -> tuple[ImportSummary, InMemoryActivityArchive, ActivityIndex]:
    archive, index = InMemoryActivityArchive(), new_index()
    with_close = import_activities(source, archive, index, FixedClock())
    source.close()
    return with_close, archive, index


def results(summary: ImportSummary) -> dict[str, tuple[ImportStatus, ImportIssue | None]]:
    return {r.name: (r.status, r.issue) for r in summary.results}


def snapshot(root: Path) -> dict[str, tuple[int, int]]:
    """Names, sizes and modification dates of everything under `root`."""
    return {
        str(p.relative_to(root)): (p.stat().st_size, p.stat().st_mtime_ns) for p in root.rglob("*")
    }


def make_directory_link(link: Path, target: Path) -> None:
    """Junction on Windows (needs no privilege), symbolic link elsewhere."""
    try:
        if IS_WINDOWS:
            subprocess.run(  # noqa: S603
                [os.environ["COMSPEC"], "/c", "mklink", "/J", str(link), str(target)],
                check=True,
                capture_output=True,
                timeout=30,
            )
        else:
            link.symlink_to(target, target_is_directory=True)
    except (OSError, NotImplementedError, subprocess.CalledProcessError):
        pytest.skip("the system does not allow creating junctions or links")


def make_file_link(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("the system does not allow creating symbolic links")


@pytest.fixture
def root(tmp_path: Path) -> Path:
    """Folder to import, inside the test's temporary folder."""
    path = tmp_path / "origine"
    path.mkdir()
    return path


# --- T-IMP-01: mixed tree -------------------------------------------------------------------


def mixed_tree(root: Path) -> None:
    broken = imp.fit_run(7)[:-1] + b"\x00"
    write(
        root,
        {
            "run1.fit": imp.fit_run(1),
            "copy_of_run1.fit": imp.fit_run(1),
            "notes.txt": b"non e' un file di attivita",
            "a/readme.md": b"x",
            "a/run2.fit": imp.fit_run(2),
            "a/b/c/garmin.zip": b"PK\x05\x06" + bytes(18),  # uno ZIP non si legge più
            "a/b/broken.fit": broken,
            "a/b/hostile.gpx": b"<gpx/>",
            "a/b/lap.tcx": b"<TrainingCenterDatabase/>",
            "a/b/gpx_named_fit.fit": b'<?xml version="1.0"?><gpx version="1.1"/>',
            "z/walk.gpx": b'<?xml version="1.0"?><gpx version="1.1"/>',
        },
    )


def test_t_imp_01_mixed_tree_gives_the_exact_summary(root: Path) -> None:
    mixed_tree(root)
    before = snapshot(root)

    summary, archive, index = import_all(folder(root))

    assert results(summary) == {
        "copy_of_run1.fit": (ImportStatus.IMPORTED, None),  # in ordine di nome arriva prima
        "run1.fit": (ImportStatus.ALREADY_PRESENT, None),
        "a/run2.fit": (ImportStatus.IMPORTED, None),
        "a/b/broken.fit": (ImportStatus.FAILED, ImportIssue.CORRUPT),
        "a/b/gpx_named_fit.fit": (ImportStatus.FAILED, ImportIssue.CORRUPT),
    }
    assert len(index.list_by_start()) == 2
    assert all(ARCHIVE_PATH.fullmatch(str(path)) for path in archive.files)
    assert snapshot(root) == before  # la cartella d'origine non si tocca


def test_results_come_in_name_order_after_the_skipped_ones(root: Path) -> None:
    write(root, {"b.fit": imp.fit_run(1), "a.fit": imp.fit_run(2), "c/d.fit": imp.fit_run(3)})

    scan = folder(root).scan()

    assert entry_names(scan) == ["a.fit", "b.fit", "c/d.fit"]
    assert [e.entry_id for e in scan.entries] == [0, 1, 2]
    assert scan.examined == 4  # three files and a folder


def test_t_imp_03_importing_the_same_folder_again_changes_nothing(root: Path) -> None:
    mixed_tree(root)
    archive, index = InMemoryActivityArchive(), new_index()
    import_activities(folder(root), archive, index, FixedClock())
    records, stored = index.list_by_start(), dict(archive.files)

    again = import_activities(folder(root), archive, index, FixedClock())

    imported = [r for r in again.results if r.status is ImportStatus.IMPORTED]
    assert not imported
    assert index.list_by_start() == records
    assert archive.files == stored


def test_scanning_twice_gives_the_same_answer(root: Path) -> None:
    mixed_tree(root)
    source = folder(root)

    assert source.scan() == source.scan()


# --- T-IMP-02: caps --------------------------------------------------------------------------


def test_default_limits_are_those_of_the_plan() -> None:
    limits = ImportLimits()

    assert limits.max_activity_files == 50_000
    assert limits.max_examined_entries == 500_000
    assert limits.max_depth == 32
    assert limits.max_file_bytes == 50 * MIB == MAX_ACTIVITY_FILE_BYTES


@pytest.mark.parametrize("name", ["max_activity_files", "max_examined_entries", "max_depth"])
def test_limits_must_be_positive(name: str) -> None:
    with pytest.raises(ValueError, match=name):
        ImportLimits(**{name: 0})


def test_t_imp_02_activity_file_cap_at_the_limit_and_one_over(root: Path) -> None:
    write(root, {f"{n}.fit": b"x" for n in range(3)})
    assert len(folder(root, max_activity_files=3).scan().entries) == 3

    write(root, {"3.fit": b"x"})
    with pytest.raises(ImportLimitExceededError) as caught:
        folder(root, max_activity_files=3).scan()

    assert (caught.value.limit, caught.value.maximum) == (ImportLimit.ACTIVITY_FILES, 3)


def test_t_imp_02_nothing_is_imported_when_a_cap_is_exceeded(root: Path) -> None:
    write(root, {f"{n}.fit": imp.fit_run(n) for n in range(4)})
    archive, index = InMemoryActivityArchive(), new_index()

    with pytest.raises(ImportLimitExceededError):
        import_activities(folder(root, max_activity_files=3), archive, index, FixedClock())

    assert not archive.files
    assert not index.list_by_start()


def test_t_imp_02_examined_entries_cap_counts_files_and_folders_of_any_type(root: Path) -> None:
    write(root, {"a/x.txt": b"x", "a/y.txt": b"x", "b/z.dat": b"x"})  # 2 folders + 3 files
    assert folder(root, max_examined_entries=5).scan().examined == 5

    with pytest.raises(ImportLimitExceededError) as caught:
        folder(root, max_examined_entries=4).scan()

    assert (caught.value.limit, caught.value.maximum) == (ImportLimit.EXAMINED_ENTRIES, 4)


def test_t_imp_02_depth_cap_at_the_limit_and_one_over(root: Path) -> None:
    write(root, {"d1/d2/d3/deep.fit": b"x"})
    assert entry_names(folder(root, max_depth=3).scan()) == ["d1/d2/d3/deep.fit"]

    write(root, {"d1/d2/d3/d4/deeper.fit": b"x"})
    with pytest.raises(ImportLimitExceededError) as caught:
        folder(root, max_depth=3).scan()

    assert (caught.value.limit, caught.value.maximum) == (ImportLimit.DEPTH, 3)


def test_t_imp_02_default_depth_is_32_levels(root: Path) -> None:
    levels = "/".join(f"d{n}" for n in range(32))
    write(root, {f"{levels}/deep.fit": b"x"})
    assert len(folder(root).scan().entries) == 1

    write(root, {f"{levels}/d32/deeper.fit": b"x"})
    with pytest.raises(ImportLimitExceededError) as caught:
        folder(root).scan()

    assert caught.value.limit is ImportLimit.DEPTH


def test_t_imp_02_file_over_the_size_cap_is_skipped_and_reported(root: Path) -> None:
    write(root, {"ok.fit": b"x" * 100, "big.fit": b"x" * 101, "also_ok.fit": b"x"})

    scan = folder(root, max_file_bytes=100).scan()

    assert entry_names(scan) == ["also_ok.fit", "ok.fit"]
    assert issues(scan) == {"big.fit": ImportIssue.TOO_LARGE}


def test_t_imp_02_default_size_cap_is_50_mib(root: Path) -> None:
    for name, size in (("at_limit.fit", 50 * MIB), ("over_limit.fit", 50 * MIB + 1)):
        with (root / name).open("wb") as handle:
            handle.truncate(size)  # sparse file: never read during the scan

    scan = folder(root).scan()

    assert entry_names(scan) == ["at_limit.fit"]
    assert issues(scan) == {"over_limit.fit": ImportIssue.TOO_LARGE}


# --- T-IMP-04: FIT only ------------------------------------------------------------------------


def test_t_imp_04_gpx_zip_and_other_files_in_a_folder_are_ignored_without_a_word(
    root: Path,
) -> None:
    write(
        root,
        {
            "run.fit": imp.fit_run(1),
            "walk.gpx": b"<gpx/>",
            "pack.zip": b"PK\x05\x06" + bytes(18),
            "hike.GPX": b"<gpx/>",
        },
    )

    scan = folder(root).scan()

    assert entry_names(scan) == ["run.fit"]
    assert not scan.skipped
    assert scan.examined == 4


def test_t_imp_04_chosen_gpx_and_zip_files_are_reported_as_unsupported(root: Path) -> None:
    write(root, {"run.fit": imp.fit_run(1), "walk.gpx": b"<gpx/>", "pack.zip": b"PK"})

    scan = FilesImportSource([root / "run.fit", root / "walk.gpx", root / "pack.zip"]).scan()

    assert entry_names(scan) == ["run.fit"]
    assert issues(scan) == {
        "walk.gpx": ImportIssue.UNSUPPORTED_FORMAT,
        "pack.zip": ImportIssue.UNSUPPORTED_FORMAT,
    }


def test_t_imp_04_the_extension_is_matched_without_regard_to_case(root: Path) -> None:
    write(root, {"UPPER.FIT": imp.fit_run(1), "Mixed.Fit": imp.fit_run(2)})

    summary, _, index = import_all(folder(root))

    assert summary.count(ImportStatus.IMPORTED) == 2
    assert len(index.list_by_start()) == 2


# --- T-SEC-04: paths -----------------------------------------------------------------------


def test_t_sec_04_linked_folder_leading_outside_is_not_followed(tmp_path: Path, root: Path) -> None:
    outside = tmp_path / "fuori"
    write(outside, {"secret.fit": imp.fit_run(9)})
    write(root, {"ok.fit": imp.fit_run(1)})
    make_directory_link(root / "scorciatoia", outside)

    scan = folder(root).scan()
    summary, _, index = import_all(folder(root))

    assert entry_names(scan) == ["ok.fit"]
    assert issues(scan) == {"scorciatoia": ImportIssue.NOT_REGULAR_FILE}
    assert len(index.list_by_start()) == 1
    assert "scorciatoia/secret.fit" not in results(summary)


def test_t_sec_04_symlinked_file_leading_outside_is_not_read(tmp_path: Path, root: Path) -> None:
    outside = tmp_path / "fuori"
    write(outside, {"secret.fit": imp.fit_run(9)})
    make_file_link(root / "link.fit", outside / "secret.fit")

    scan = folder(root).scan()

    assert not scan.entries
    assert issues(scan) == {"link.fit": ImportIssue.NOT_REGULAR_FILE}


def test_t_sec_04_folder_replaced_by_a_link_after_the_scan_is_refused_at_the_read(
    tmp_path: Path, root: Path
) -> None:
    outside = tmp_path / "fuori"
    write(outside, {"run.fit": imp.fit_run(9)})
    write(root, {"sub/run.fit": imp.fit_run(1)})
    source = folder(root)
    (entry,) = source.scan().entries
    (root / "sub" / "run.fit").unlink()
    (root / "sub").rmdir()
    make_directory_link(root / "sub", outside)

    with pytest.raises(ImportEntryError) as caught:
        source.read(entry, MIB)

    assert caught.value.issue is ImportIssue.NOT_REGULAR_FILE


def test_t_sec_04_file_replaced_by_a_link_after_the_scan_is_refused_at_the_read(
    tmp_path: Path, root: Path
) -> None:
    write(tmp_path / "fuori", {"run.fit": imp.fit_run(9)})
    write(root, {"run.fit": imp.fit_run(1)})
    source = folder(root)
    (entry,) = source.scan().entries
    (root / "run.fit").unlink()
    make_file_link(root / "run.fit", tmp_path / "fuori" / "run.fit")

    with pytest.raises(ImportEntryError) as caught:
        source.read(entry, MIB)

    assert caught.value.issue is ImportIssue.NOT_REGULAR_FILE


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFOs exist only on POSIX")
def test_t_sec_04_fifo_named_fit_is_reported_and_never_opened(root: Path) -> None:
    os.mkfifo(root / "pipe.fit")  # type: ignore[attr-defined,unused-ignore]

    scan = folder(root).scan()

    assert not scan.entries
    assert issues(scan) == {"pipe.fit": ImportIssue.NOT_REGULAR_FILE}


@pytest.mark.parametrize(
    "name", ["CON.fit", "Aux.fit", "prn.FIT", "COM1.fit", "lpt9.fit", "COM¹.fit", "con.tar.fit"]
)
def test_t_sec_04_windows_reserved_names_are_skipped_and_reported(root: Path, name: str) -> None:
    write(root, {name: imp.fit_run(1), "fine.fit": imp.fit_run(2)})

    scan = folder(root).scan()

    assert entry_names(scan) == ["fine.fit"]
    assert issues(scan) == {name: ImportIssue.RESERVED_NAME}


def test_t_sec_04_reserved_folder_names_are_not_entered(root: Path) -> None:
    write(root, {"PRN/inside.fit": imp.fit_run(1), "ok/inside.fit": imp.fit_run(2)})

    scan = folder(root).scan()

    assert entry_names(scan) == ["ok/inside.fit"]
    assert issues(scan) == {"PRN": ImportIssue.RESERVED_NAME}


def test_t_sec_04_long_paths_are_handled(root: Path) -> None:
    parts = [f"{n}_" + "d" * 60 for n in range(6)]  # over 360 characters: more than MAX_PATH
    fit = imp.fit_run(1)
    write(root, {"/".join([*parts, "deep.fit"]): fit})
    assert len(str(root.joinpath(*parts))) > 260

    source = folder(root)
    (entry,) = source.scan().entries

    assert source.read(entry, MIB) == fit


def test_t_sec_04_path_over_the_displayed_length_cap_is_skipped(root: Path) -> None:
    write(root, {"short.fit": b"x", "a" * 30 + "/" + "b" * 30 + ".fit": b"x"})

    scan = folder(root, max_path_chars=40).scan()

    assert entry_names(scan) == ["short.fit"]
    assert set(issues(scan).values()) == {ImportIssue.PATH_TOO_LONG}


@pytest.mark.skipif(IS_WINDOWS, reason="control characters are not allowed on Windows")
def test_t_sec_04_control_characters_in_file_names_are_not_shown(root: Path) -> None:
    write(root, {"run" + chr(27) + "[31m.fit": imp.fit_run(1)})

    (entry,) = folder(root).scan().entries

    assert entry.name == "run?[31m.fit"


def test_t_sec_04_archive_names_are_generated_by_the_program(root: Path) -> None:
    write(root, {"strano nome (1) é.fit": imp.fit_run(1)})

    _, archive, _ = import_all(folder(root))

    assert all(ARCHIVE_PATH.fullmatch(str(path)) for path in archive.files)
    assert len(archive.files) == 1


# --- List of chosen files --------------------------------------------------------------------


def test_chosen_files_are_listed_by_name_and_wrong_choices_are_reported(root: Path) -> None:
    write(
        root,
        {
            "run.fit": imp.fit_run(1),
            "walk.fit": imp.fit_run(2),
            "notes.txt": b"x",
            "sub/inside.fit": imp.fit_run(3),
        },
    )
    chosen = [
        root / "walk.fit",
        root / "run.fit",
        root / "notes.txt",
        root / "sub",
        root / "missing.fit",
    ]

    scan = FilesImportSource(chosen).scan()

    assert entry_names(scan) == ["run.fit", "walk.fit"]
    assert issues(scan) == {
        "notes.txt": ImportIssue.UNSUPPORTED_FORMAT,
        "sub": ImportIssue.NOT_REGULAR_FILE,
        "missing.fit": ImportIssue.UNREADABLE,
    }
    assert scan.examined == 5


def test_chosen_files_obey_the_same_caps_and_name_rules(root: Path) -> None:
    write(root, {"a.fit": b"x", "b.fit": b"x", "CON.fit": b"x", "big.fit": b"x" * 11})
    chosen = [root / name for name in ("a.fit", "b.fit", "CON.fit", "big.fit")]

    scan = FilesImportSource(chosen, ImportLimits(max_file_bytes=10)).scan()

    assert entry_names(scan) == ["a.fit", "b.fit"]
    assert issues(scan) == {"CON.fit": ImportIssue.RESERVED_NAME, "big.fit": ImportIssue.TOO_LARGE}
    with pytest.raises(ImportLimitExceededError):
        FilesImportSource(chosen[:2], ImportLimits(max_activity_files=1)).scan()


def test_chosen_symlink_is_refused(tmp_path: Path, root: Path) -> None:
    write(tmp_path / "fuori", {"x.fit": imp.fit_run(1)})
    make_file_link(root / "l.fit", tmp_path / "fuori" / "x.fit")

    scan = FilesImportSource([root / "l.fit"]).scan()

    assert issues(scan) == {"l.fit": ImportIssue.NOT_REGULAR_FILE}


# --- Reading and resources ------------------------------------------------------------------------


def test_missing_or_non_folder_roots_are_unreadable(tmp_path: Path) -> None:
    (tmp_path / "file.txt").write_bytes(b"x")

    for path in (tmp_path / "non-esiste", tmp_path / "file.txt"):
        with pytest.raises(ImportSourceUnreadableError):
            folder(path).scan()


def test_read_returns_the_bytes_and_refuses_what_is_too_big(root: Path) -> None:
    fit = imp.fit_run(1)
    write(root, {"run.fit": fit})
    source = folder(root)
    (entry,) = source.scan().entries

    assert source.read(entry, len(fit)) == fit
    with pytest.raises(ImportEntryError) as caught:
        source.read(entry, len(fit) - 1)
    assert caught.value.issue is ImportIssue.TOO_LARGE


def test_read_checks_the_real_size_not_the_listed_one(root: Path) -> None:
    write(root, {"run.fit": b"x" * 10})
    source = folder(root)
    (entry,) = source.scan().entries
    (root / "run.fit").write_bytes(b"x" * 500)

    with pytest.raises(ImportEntryError) as caught:
        source.read(entry, 100)

    assert caught.value.issue is ImportIssue.TOO_LARGE


def test_read_of_a_vanished_file_or_unknown_entry_is_unreadable(root: Path) -> None:
    write(root, {"run.fit": imp.fit_run(1)})
    source = folder(root)
    (entry,) = source.scan().entries
    (root / "run.fit").unlink()

    for target in (entry, ImportEntry(999, "x.fit", 1)):
        with pytest.raises(ImportEntryError) as caught:
            source.read(target, MIB)
        assert caught.value.issue is ImportIssue.UNREADABLE


def test_close_is_repeatable_and_the_source_stays_readable(root: Path) -> None:
    fit = imp.fit_run(1)
    write(root, {"run.fit": fit})
    source = folder(root)
    (entry,) = source.scan().entries

    source.close()
    source.close()

    assert source.read(entry, MIB) == fit


def iter_sizes(scan: ImportScan) -> Iterator[int]:
    return (entry.size_bytes for entry in scan.entries)


def test_sizes_are_the_declared_ones(root: Path) -> None:
    fit = imp.fit_run(1)
    write(root, {"run.fit": fit, "other.fit": fit * 2})

    assert sorted(iter_sizes(folder(root).scan())) == [len(fit), 2 * len(fit)]


# --- Counting while reading and cancellation (R6) --------------------------------------------


class _CountingListing:
    """Listing of a folder that counts the entries handed to the caller."""

    def __init__(self, inner: object, consumed: list[int]) -> None:
        self._inner = inner
        self._consumed = consumed

    def __enter__(self) -> _CountingListing:
        self._inner.__enter__()  # type: ignore[attr-defined]
        return self

    def __exit__(self, *args: object) -> None:
        self._inner.__exit__(*args)  # type: ignore[attr-defined]

    def __iter__(self) -> _CountingListing:
        return self

    def __next__(self) -> os.DirEntry[str]:
        entry: os.DirEntry[str] = next(self._inner)  # type: ignore[call-overload]
        self._consumed[0] += 1
        return entry


def test_the_examined_cap_stops_a_single_huge_folder_before_reading_it_all(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write(root, {f"f{n:03d}.fit": b"x" for n in range(40)})
    consumed = [0]
    real = os.scandir
    monkeypatch.setattr(os, "scandir", lambda p: _CountingListing(real(p), consumed))

    with pytest.raises(ImportLimitExceededError) as caught:
        folder(root, max_examined_entries=5).scan()

    assert caught.value.limit is ImportLimit.EXAMINED_ENTRIES
    assert consumed[0] == 6  # the cap plus one: not the 40 entries


def test_a_scan_can_be_cancelled_between_entries(root: Path) -> None:
    write(root, {f"f{n:02d}.fit": imp.fit_run(n) for n in range(1, 11)})
    calls = [0]

    def cancel_after_three() -> bool:
        calls[0] += 1
        return calls[0] > 3

    with pytest.raises(ImportScanCancelledError):
        folder(root).scan(cancel_after_three)

    assert calls[0] == 4  # it stopped at the first cancel request


def test_a_scan_without_cancellation_is_unchanged(root: Path) -> None:
    write(root, {"a.fit": imp.fit_run(1)})

    assert len(folder(root).scan(lambda: False).entries) == 1
