# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Sorgenti dell'import da file e cartelle, ZIP compresi (disco vero, archivio e indice finti).

T-IMP-01 (albero misto, riepilogo esatto), T-IMP-02 (tetti al limite e un passo oltre),
T-IMP-03 (idempotenza), T-IMP-04 (ZIP), T-SEC-02 (ZIP ostili), T-SEC-04 (percorsi).
"""

from __future__ import annotations

import os
import re
import struct
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
from tests.fixtures.generators import gpx_writer as gw
from tests.fixtures.generators import import_files as imp
from tests.fixtures.generators import zip_writer as zw

ARCHIVE_PATH = re.compile(r"\d{4}/[0-9a-f]{64}\.(fit|gpx)")
IS_WINDOWS = os.name == "nt"
MIB = 1024 * 1024


# --- Aiuti ------------------------------------------------------------------------------------


def raw(path: Path) -> Path:
    """Percorso adatto a creare nomi riservati o percorsi lunghi su Windows (prefisso `\\\\?\\`)."""
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
    """Nomi, dimensioni e date di modifica di tutto ciò che sta sotto `root`."""
    return {
        str(p.relative_to(root)): (p.stat().st_size, p.stat().st_mtime_ns) for p in root.rglob("*")
    }


def make_directory_link(link: Path, target: Path) -> None:
    """Giunzione su Windows (non serve alcun privilegio), collegamento simbolico altrove."""
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
        pytest.skip("il sistema non permette di creare giunzioni o collegamenti")


def make_file_link(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("il sistema non permette di creare collegamenti simbolici")


def patch_end_record(
    zip_bytes: bytes, *, entries: int | None = None, size: int | None = None
) -> bytes:
    """Ritocca il record finale (22 byte, senza commento): numero di voci e byte della directory."""
    data = bytearray(zip_bytes)
    end = len(data) - 22
    if entries is not None:
        struct.pack_into("<HH", data, end + 8, entries, entries)
    if size is not None:
        struct.pack_into("<I", data, end + 12, size)
    return bytes(data)


@pytest.fixture
def root(tmp_path: Path) -> Path:
    """Cartella da importare, dentro la cartella temporanea del test."""
    path = tmp_path / "origine"
    path.mkdir()
    return path


# --- T-IMP-01: albero misto -------------------------------------------------------------------


def mixed_tree(root: Path) -> None:
    broken = imp.fit_run(7)[:-1] + b"\x00"
    no_times = gw.build_gpx(gw.GpxSpec([[gw.GpxPoint(1.0, 1.0), gw.GpxPoint(1.0, 1.1)]]))
    write(
        root,
        {
            "run1.fit": imp.fit_run(1),
            "copy_of_run1.fit": imp.fit_run(1),
            "notes.txt": b"non e' un file di attivita",
            "a/readme.md": b"x",
            "a/run2.gpx": imp.gpx_run(2),
            "a/b/c/garmin.zip": zw.garmin_download(imp.fit_run(3)),
            "a/b/broken.fit": broken,
            "a/b/hostile.gpx": gw.billion_laughs(),
            "a/b/no_times.gpx": no_times,
            "a/b/lap.tcx": b"<TrainingCenterDatabase/>",
            "a/b/wrong.gpx": imp.fit_run(8),
            "z/same_as_fit_but_gpx.gpx": imp.same_run_as_fit_and_gpx(1)[1],
        },
    )


def test_t_imp_01_mixed_tree_gives_the_exact_summary(root: Path) -> None:
    mixed_tree(root)
    before = snapshot(root)

    summary, archive, index = import_all(folder(root))

    assert results(summary) == {
        "copy_of_run1.fit": (ImportStatus.IMPORTED, None),  # in ordine di nome arriva prima
        "run1.fit": (ImportStatus.ALREADY_PRESENT, None),
        "a/run2.gpx": (ImportStatus.IMPORTED, None),
        "a/b/c/garmin.zip!12345678901_ACTIVITY.fit": (ImportStatus.IMPORTED, None),
        "a/b/broken.fit": (ImportStatus.FAILED, ImportIssue.CORRUPT),
        "a/b/hostile.gpx": (ImportStatus.FAILED, ImportIssue.CORRUPT),
        "a/b/no_times.gpx": (ImportStatus.IGNORED, ImportIssue.NOT_AN_ACTIVITY),
        "a/b/wrong.gpx": (ImportStatus.FAILED, ImportIssue.EXTENSION_MISMATCH),
        "z/same_as_fit_but_gpx.gpx": (ImportStatus.DUPLICATE_OF_OTHER_FORMAT, None),
    }
    assert len(index.list_by_start()) == 3
    assert all(ARCHIVE_PATH.fullmatch(str(path)) for path in archive.files)
    assert snapshot(root) == before  # la cartella d'origine non si tocca


def test_results_come_in_name_order_after_the_skipped_ones(root: Path) -> None:
    write(root, {"b.fit": imp.fit_run(1), "a.fit": imp.fit_run(2), "c/d.fit": imp.fit_run(3)})

    scan = folder(root).scan()

    assert entry_names(scan) == ["a.fit", "b.fit", "c/d.fit"]
    assert [e.entry_id for e in scan.entries] == [0, 1, 2]
    assert scan.examined == 4  # tre file e una cartella


def test_t_imp_03_importing_the_same_folder_again_changes_nothing(root: Path) -> None:
    mixed_tree(root)
    archive, index = InMemoryActivityArchive(), new_index()
    with folder(root) as first:
        import_activities(first, archive, index, FixedClock())
    records, stored = index.list_by_start(), dict(archive.files)

    with folder(root) as second:
        again = import_activities(second, archive, index, FixedClock())

    imported = [r for r in again.results if r.status is ImportStatus.IMPORTED]
    assert not imported
    assert index.list_by_start() == records
    assert archive.files == stored


def test_scanning_twice_gives_the_same_answer(root: Path) -> None:
    mixed_tree(root)
    source = folder(root)

    assert source.scan() == source.scan()


# --- T-IMP-02: tetti --------------------------------------------------------------------------


def test_default_limits_are_those_of_the_plan() -> None:
    limits = ImportLimits()

    assert limits.max_activity_files == 50_000
    assert limits.max_examined_entries == 500_000
    assert limits.max_depth == 32
    assert limits.max_file_bytes == 50 * MIB == MAX_ACTIVITY_FILE_BYTES
    assert limits.max_zip_entries == 50_000
    assert limits.max_zip_total_bytes == 5 * 1024 * MIB
    assert limits.max_zip_ratio == 100


@pytest.mark.parametrize("name", ["max_activity_files", "max_examined_entries", "max_depth"])
def test_limits_must_be_positive(name: str) -> None:
    with pytest.raises(ValueError, match=name):
        ImportLimits(**{name: 0})


def test_t_imp_02_activity_file_cap_at_the_limit_and_one_over(root: Path) -> None:
    write(root, {f"{n}.fit": b"x" for n in range(3)})
    assert len(folder(root, max_activity_files=3).scan().entries) == 3

    write(root, {"3.gpx": b"x"})
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


def test_t_imp_02_zip_entries_count_as_activity_files(root: Path) -> None:
    zipped = zw.build_zip([("1.fit", b"x"), ("2.gpx", b"x"), ("notes.txt", b"x")])
    write(root, {"loose.fit": b"x", "pack.zip": zipped})
    assert len(folder(root, max_activity_files=3).scan().entries) == 3

    with pytest.raises(ImportLimitExceededError) as caught:
        folder(root, max_activity_files=2).scan()

    assert caught.value.limit is ImportLimit.ACTIVITY_FILES


def test_t_imp_02_examined_entries_cap_counts_files_and_folders_of_any_type(root: Path) -> None:
    write(root, {"a/x.txt": b"x", "a/y.txt": b"x", "b/z.dat": b"x"})  # 2 cartelle + 3 file
    assert folder(root, max_examined_entries=5).scan().examined == 5

    with pytest.raises(ImportLimitExceededError) as caught:
        folder(root, max_examined_entries=4).scan()

    assert (caught.value.limit, caught.value.maximum) == (ImportLimit.EXAMINED_ENTRIES, 4)


def test_t_imp_02_zip_entries_of_any_type_are_examined_entries(root: Path) -> None:
    write(root, {"pack.zip": zw.many_entries(10)})
    assert folder(root, max_examined_entries=11).scan().examined == 11  # lo ZIP e le sue 10 voci

    with pytest.raises(ImportLimitExceededError) as caught:
        folder(root, max_examined_entries=10).scan()

    assert caught.value.limit is ImportLimit.EXAMINED_ENTRIES


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
    write(root, {"ok.fit": b"x" * 100, "big.fit": b"x" * 101, "also_ok.gpx": b"x"})

    scan = folder(root, max_file_bytes=100).scan()

    assert entry_names(scan) == ["also_ok.gpx", "ok.fit"]
    assert issues(scan) == {"big.fit": ImportIssue.TOO_LARGE}


def test_t_imp_02_default_size_cap_is_50_mib(root: Path) -> None:
    for name, size in (("at_limit.fit", 50 * MIB), ("over_limit.fit", 50 * MIB + 1)):
        with (root / name).open("wb") as handle:
            handle.truncate(size)  # file sparso: non si legge mai nell'esame

    scan = folder(root).scan()

    assert entry_names(scan) == ["at_limit.fit"]
    assert issues(scan) == {"over_limit.fit": ImportIssue.TOO_LARGE}


def test_t_imp_02_default_cap_of_50000_activity_files_counts_zip_entries(root: Path) -> None:
    write(root, {"pack.zip": zw.many_entries(50_000, suffix=".fit")})
    assert len(folder(root).scan().entries) == 50_000

    write(root, {"one_more.gpx": b"x"})
    with pytest.raises(ImportLimitExceededError) as caught:
        folder(root).scan()

    assert (caught.value.limit, caught.value.maximum) == (ImportLimit.ACTIVITY_FILES, 50_000)


def test_t_imp_02_default_cap_of_500000_examined_entries(root: Path) -> None:
    pack = zw.many_entries(50_000)  # 50.000 voci .txt: esaminate, non attività
    for n in range(9):
        write(root, {f"pack{n}.zip": pack})
    assert folder(root).scan().examined == 9 * 50_001  # 450.009

    for n in range(9, 11):
        write(root, {f"pack{n}.zip": pack})
    with pytest.raises(ImportLimitExceededError) as caught:
        folder(root).scan()

    assert (caught.value.limit, caught.value.maximum) == (ImportLimit.EXAMINED_ENTRIES, 500_000)


# --- T-IMP-04: ZIP ----------------------------------------------------------------------------


def test_t_imp_04_garmin_download_with_one_fit(root: Path) -> None:
    fit = imp.fit_run(1)
    write(root, {"12345678901.zip": zw.garmin_download(fit)})

    summary, archive, index = import_all(folder(root))

    assert results(summary) == {
        "12345678901.zip!12345678901_ACTIVITY.fit": (ImportStatus.IMPORTED, None)
    }
    (record,) = index.list_by_start()
    assert archive.read(record.archived, 10**9) == fit  # il file estratto, non lo ZIP
    assert record.archived.relative_path.suffix == ".fit"


def test_t_imp_04_many_fit_and_gpx_activities_and_other_entries_ignored(root: Path) -> None:
    entries = [(f"2026/run{n}.fit", imp.fit_run(n)) for n in range(1, 6)]
    entries += [(f"2026/walk{n}.gpx", imp.gpx_run(n + 10)) for n in range(1, 4)]
    entries += [("2026/", b""), ("readme.txt", b"x"), ("summary.json", b"{}"), ("lap.tcx", b"<a/>")]
    write(root, {"export.zip": zw.build_zip(entries)})

    scan = folder(root).scan()
    summary, archive, index = import_all(folder(root))

    assert len(scan.entries) == 8
    assert not scan.skipped  # le voci di altri tipi si ignorano senza segnalarle
    assert summary.count(ImportStatus.IMPORTED) == 8
    assert len(index.list_by_start()) == 8
    assert {path.suffix for path in archive.files} == {".fit", ".gpx"}
    assert not any(content.startswith(zw.LOCAL_SIGNATURE) for content in archive.files.values())


def test_t_imp_04_entries_read_in_any_order_from_the_same_zip(root: Path) -> None:
    contents = {f"{n}.fit": imp.fit_run(n) for n in range(1, 5)}
    write(root, {"pack.zip": zw.build_zip(list(contents.items()))})
    source = folder(root)
    entries = source.scan().entries

    for entry in reversed(entries):
        assert source.read(entry, MIB) == contents[entry.name.split("!")[1]]
    source.close()


def test_t_imp_04_import_from_the_list_of_chosen_files_with_a_zip(root: Path) -> None:
    write(
        root,
        {
            "a.fit": imp.fit_run(1),
            "b.gpx": imp.gpx_run(2),
            "c.zip": zw.garmin_download(imp.fit_run(3)),
        },
    )
    chosen = [root / "c.zip", root / "a.fit", root / "b.gpx"]

    summary, _, index = import_all(FilesImportSource(chosen))

    assert summary.count(ImportStatus.IMPORTED) == 3
    assert len(index.list_by_start()) == 3


def test_empty_zip_has_nothing_to_import_and_nothing_to_report(root: Path) -> None:
    write(root, {"empty.zip": zw.build_zip([])})

    scan = folder(root).scan()

    assert not scan.entries
    assert not scan.skipped


# --- T-SEC-02: ZIP ostili ---------------------------------------------------------------------


def hostile(root: Path, zip_bytes: bytes, **limits: int) -> tuple[ImportScan, ImportSummary]:
    """Scrive lo ZIP, lo esamina e prova a importarlo; nulla deve uscire dalla cartella."""
    write(root, {"hostile.zip": zip_bytes})
    outside = snapshot(root.parent)
    scan = folder(root, **limits).scan()
    summary, archive, _ = import_all(folder(root, **limits))
    assert all(ARCHIVE_PATH.fullmatch(str(path)) for path in archive.files)
    assert snapshot(root.parent) == outside
    return scan, summary


def test_t_sec_02_zip_bomb_is_refused_before_extracting(root: Path) -> None:
    scan, summary = hostile(root, zw.zero_bomb(45 * MIB))  # sotto 50 MB ma oltre 100:1

    assert not scan.entries
    assert issues(scan) == {"hostile.zip!bomb.fit": ImportIssue.ARCHIVE_LIMIT}
    assert results(summary) == {
        "hostile.zip!bomb.fit": (ImportStatus.FAILED, ImportIssue.ARCHIVE_LIMIT)
    }


def test_t_sec_02_entry_declared_over_50_mib_is_refused(root: Path) -> None:
    scan, _ = hostile(root, zw.zero_bomb(MAX_ACTIVITY_FILE_BYTES + 1))

    assert issues(scan) == {"hostile.zip!bomb.fit": ImportIssue.TOO_LARGE}


def test_t_sec_02_compression_ratio_at_100_to_1_and_just_over(root: Path) -> None:
    stored = zw.build_zip([("a.fit", b"x" * 1000)], method=0)
    at_limit = zw.patch_entry(stored, 0, compress_size=10)  # 1000 / 10 = 100:1
    over = zw.patch_entry(stored, 0, compress_size=9)  # 111:1

    write(root, {"at.zip": at_limit, "over.zip": over})
    scan = folder(root).scan()

    assert entry_names(scan) == ["at.zip!a.fit"]
    assert issues(scan) == {"over.zip!a.fit": ImportIssue.ARCHIVE_LIMIT}


def test_t_sec_02_too_many_entries_refuses_the_whole_archive(root: Path) -> None:
    write(
        root,
        {
            "five.zip": zw.many_entries(5, suffix=".fit"),
            "six.zip": zw.many_entries(6, suffix=".fit"),
        },
    )

    scan = folder(root, max_zip_entries=5).scan()

    assert len(scan.entries) == 5
    assert issues(scan) == {"six.zip": ImportIssue.ARCHIVE_LIMIT}


def test_t_sec_02_default_50001_entries_refuses_the_archive(root: Path) -> None:
    write(root, {"ok.zip": zw.many_entries(50_000), "many.zip": zw.many_entries(50_001)})

    scan = folder(root).scan()

    assert issues(scan) == {"many.zip": ImportIssue.ARCHIVE_LIMIT}


def test_t_sec_02_zip64_record_is_read_and_its_count_enforced(root: Path) -> None:
    write(root, {"huge.zip": zw.many_entries(65_536)})  # oltre 65.535 voci: record ZIP64

    assert issues(folder(root).scan()) == {"huge.zip": ImportIssue.ARCHIVE_LIMIT}


def test_t_sec_02_total_declared_size_cap(root: Path) -> None:
    pack = zw.build_zip([("a.fit", b"x" * 600), ("b.fit", b"x" * 400)])
    write(root, {"pack.zip": pack})
    assert len(folder(root, max_zip_total_bytes=1000).scan().entries) == 2

    assert issues(folder(root, max_zip_total_bytes=999).scan()) == {
        "pack.zip": ImportIssue.ARCHIVE_LIMIT
    }


def test_t_sec_02_directory_larger_than_the_cap_is_refused_without_reading_it(root: Path) -> None:
    pack = zw.build_zip([("a.fit", b"x")])
    write(root, {"lie.zip": patch_end_record(pack, size=0x7FFFFFFF)})

    assert issues(folder(root).scan()) == {"lie.zip": ImportIssue.ARCHIVE_LIMIT}


def test_t_sec_02_declared_entry_count_must_match_the_directory(root: Path) -> None:
    pack = zw.build_zip([("a.fit", b"x"), ("b.fit", b"y")])
    write(root, {"lie.zip": patch_end_record(pack, entries=1)})

    assert issues(folder(root).scan()) == {"lie.zip": ImportIssue.ARCHIVE_INVALID}


def test_t_sec_02_declared_size_larger_than_the_real_one_fails_at_extraction(root: Path) -> None:
    fit = imp.fit_run(1)
    pack = zw.patch_entry(zw.build_zip([("a.fit", fit)]), 0, file_size=len(fit) + 10)

    scan, summary = hostile(root, pack)

    assert entry_names(scan) == ["hostile.zip!a.fit"]  # l'esame vede solo ciò che è dichiarato
    assert results(summary) == {
        "hostile.zip!a.fit": (ImportStatus.FAILED, ImportIssue.ARCHIVE_INVALID)
    }


def test_t_sec_02_declared_size_smaller_than_the_real_one_fails_at_extraction(root: Path) -> None:
    fit = imp.fit_run(1)
    pack = zw.patch_entry(zw.build_zip([("a.fit", fit)]), 0, file_size=10)

    _, summary = hostile(root, pack)

    assert results(summary) == {
        "hostile.zip!a.fit": (ImportStatus.FAILED, ImportIssue.ARCHIVE_INVALID)
    }


def test_t_sec_02_declared_size_over_the_cap_is_refused_at_the_scan(root: Path) -> None:
    pack = zw.patch_entry(zw.build_zip([("a.fit", b"x" * 100)]), 0, file_size=4_000_000_000)

    scan, _ = hostile(root, pack)

    assert issues(scan) == {"hostile.zip!a.fit": ImportIssue.TOO_LARGE}


def test_t_sec_02_wrong_crc_is_detected_at_extraction(root: Path) -> None:
    fit = imp.fit_run(1)
    pack = zw.patch_entry(zw.build_zip([("a.fit", fit)]), 0, crc=0x12345678)

    _, summary = hostile(root, pack)

    assert results(summary) == {
        "hostile.zip!a.fit": (ImportStatus.FAILED, ImportIssue.ARCHIVE_INVALID)
    }


def test_t_sec_02_corrupted_deflate_stream_is_detected(root: Path) -> None:
    pack = bytearray(zw.build_zip([("a.fit", imp.fit_run(1))]))
    pack[60:70] = b"\xff" * 10  # dentro i dati compressi, dopo l'intestazione locale

    _, summary = hostile(root, bytes(pack))

    assert results(summary) == {
        "hostile.zip!a.fit": (ImportStatus.FAILED, ImportIssue.ARCHIVE_INVALID)
    }


def test_t_sec_02_encrypted_entries_are_refused(root: Path) -> None:
    pack = zw.build_zip([("secret.fit", imp.fit_run(1)), ("plain.fit", imp.fit_run(2))])
    encrypted = zw.patch_entry(pack, 0, flags=0x1)

    scan, summary = hostile(root, encrypted)

    assert entry_names(scan) == ["hostile.zip!plain.fit"]
    assert issues(scan) == {"hostile.zip!secret.fit": ImportIssue.ENCRYPTED}
    assert summary.count(ImportStatus.IMPORTED) == 1


def test_t_sec_02_nested_zip_is_refused_and_not_opened(root: Path) -> None:
    inner = zw.build_zip([("inner.fit", imp.fit_run(1))])
    pack = zw.build_zip([("inner.zip", inner), ("ok.fit", imp.fit_run(2))])

    scan, summary = hostile(root, pack)

    assert issues(scan) == {"hostile.zip!inner.zip": ImportIssue.NESTED_ARCHIVE}
    assert list(results(summary)) == ["hostile.zip!inner.zip", "hostile.zip!ok.fit"]
    assert summary.count(ImportStatus.IMPORTED) == 1  # solo ok.fit: l'interno non è importato


@pytest.mark.parametrize(
    "name",
    [
        "../evil.fit",
        "..\\evil.fit",
        "a/../../evil.fit",
        "/abs/evil.fit",
        "\\abs\\evil.fit",
        "C:\\evil.fit",
        "C:evil.fit",
        "dir/CON.fit",
        "NUL.gpx",
        "x" + chr(7) + "y.fit",
        "tab" + chr(9) + ".fit",
        "a:b.fit",
        "long/" + "d" * 2000 + ".fit",
    ],
)
def test_t_sec_02_hostile_entry_names_are_refused_and_never_used_as_paths(
    root: Path, name: str
) -> None:
    pack = zw.build_zip([(name, imp.fit_run(1)), ("good.fit", imp.fit_run(2))])

    scan, summary = hostile(root, pack)

    assert entry_names(scan) == ["hostile.zip!good.fit"]
    (issue,) = issues(scan).values()
    assert issue is ImportIssue.UNSAFE_NAME
    assert summary.count(ImportStatus.IMPORTED) == 1
    assert not list(root.parent.rglob("evil*"))


def test_t_sec_02_displayed_names_have_no_control_characters(root: Path) -> None:
    pack = zw.build_zip([("a" + chr(0x202E) + "tif.fit", b"x"), ("b" + chr(7) + ".fit", b"x")])
    write(root, {"pack.zip": pack})

    scan = folder(root).scan()
    shown = [*entry_names(scan), *issues(scan)]

    assert all(c.isprintable() for name in shown for c in name)


def test_t_sec_02_names_differing_only_in_case_refuse_the_archive(root: Path) -> None:
    pack = zw.build_zip([("run.fit", b"x"), ("RUN.FIT", b"y")])
    write(root, {"pack.zip": pack})

    assert issues(folder(root).scan()) == {"pack.zip": ImportIssue.ARCHIVE_INVALID}


@pytest.mark.parametrize(
    "content", [b"", b"not a zip at all", b"PK\x05\x06" + b"\x01" * 18, b"PK\x03\x04" * 50]
)
def test_t_sec_02_garbage_named_zip_is_refused(root: Path, content: bytes) -> None:
    write(root, {"bad.zip": content})

    assert issues(folder(root).scan()) == {"bad.zip": ImportIssue.ARCHIVE_INVALID}


def test_t_sec_02_truncated_zip_is_refused(root: Path) -> None:
    pack = zw.build_zip([("a.fit", imp.fit_run(1))])
    write(root, {"cut.zip": pack[: len(pack) // 2]})

    assert issues(folder(root).scan()) == {"cut.zip": ImportIssue.ARCHIVE_INVALID}


def test_t_sec_02_zip_changed_after_the_scan_is_refused_at_the_read(root: Path) -> None:
    write(root, {"pack.zip": zw.build_zip([("a.fit", imp.fit_run(1))])})
    source = folder(root)
    (entry,) = source.scan().entries
    write(root, {"pack.zip": zw.build_zip([("other.fit", imp.fit_run(2))])})

    with pytest.raises(ImportEntryError) as caught:
        source.read(entry, MIB)

    assert caught.value.issue is ImportIssue.ARCHIVE_INVALID
    source.close()


def test_t_sec_02_extraction_respects_the_byte_cap_of_the_reader(root: Path) -> None:
    fit = imp.fit_run(1)
    write(root, {"pack.zip": zw.build_zip([("a.fit", fit)])})
    source = folder(root)
    (entry,) = source.scan().entries

    with pytest.raises(ImportEntryError) as caught:
        source.read(entry, len(fit) - 1)

    assert caught.value.issue is ImportIssue.TOO_LARGE
    assert source.read(entry, len(fit)) == fit
    source.close()


# --- T-SEC-04: percorsi -----------------------------------------------------------------------


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
    make_file_link(root / "link.zip", outside / "secret.fit")

    scan = folder(root).scan()

    assert not scan.entries
    assert issues(scan) == {
        "link.fit": ImportIssue.NOT_REGULAR_FILE,
        "link.zip": ImportIssue.NOT_REGULAR_FILE,
    }


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


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="i FIFO esistono solo su POSIX")
def test_t_sec_04_fifo_named_fit_is_reported_and_never_opened(root: Path) -> None:
    os.mkfifo(root / "pipe.fit")  # type: ignore[attr-defined,unused-ignore]

    scan = folder(root).scan()

    assert not scan.entries
    assert issues(scan) == {"pipe.fit": ImportIssue.NOT_REGULAR_FILE}


@pytest.mark.parametrize(
    "name", ["CON.fit", "nul.gpx", "Aux.fit", "COM1.zip", "lpt9.GPX", "COM¹.fit", "con.tar.fit"]
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
    parts = [f"{n}_" + "d" * 60 for n in range(6)]  # oltre 360 caratteri: più di MAX_PATH
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


@pytest.mark.skipif(IS_WINDOWS, reason="su Windows i caratteri di controllo non sono ammessi")
def test_t_sec_04_control_characters_in_file_names_are_not_shown(root: Path) -> None:
    write(root, {"run" + chr(27) + "[31m.fit": imp.fit_run(1)})

    (entry,) = folder(root).scan().entries

    assert entry.name == "run?[31m.fit"


def test_t_sec_04_archive_names_are_generated_by_the_program(root: Path) -> None:
    write(root, {"strano nome (1) é.fit": imp.fit_run(1)})

    _, archive, _ = import_all(folder(root))

    assert all(ARCHIVE_PATH.fullmatch(str(path)) for path in archive.files)
    assert len(archive.files) == 1


# --- Elenco di file scelti --------------------------------------------------------------------


def test_chosen_files_are_listed_by_name_and_wrong_choices_are_reported(root: Path) -> None:
    write(
        root,
        {
            "run.fit": imp.fit_run(1),
            "walk.gpx": imp.gpx_run(2),
            "notes.txt": b"x",
            "sub/inside.fit": imp.fit_run(3),
        },
    )
    chosen = [
        root / "walk.gpx",
        root / "run.fit",
        root / "notes.txt",
        root / "sub",
        root / "missing.fit",
    ]

    scan = FilesImportSource(chosen).scan()

    assert entry_names(scan) == ["run.fit", "walk.gpx"]
    assert issues(scan) == {
        "notes.txt": ImportIssue.UNSUPPORTED_FORMAT,
        "sub": ImportIssue.NOT_REGULAR_FILE,
        "missing.fit": ImportIssue.UNREADABLE,
    }
    assert scan.examined == 5


def test_chosen_files_obey_the_same_caps_and_name_rules(root: Path) -> None:
    write(root, {"a.fit": b"x", "b.fit": b"x", "CON.fit": b"x", "big.gpx": b"x" * 11})
    chosen = [root / name for name in ("a.fit", "b.fit", "CON.fit", "big.gpx")]

    scan = FilesImportSource(chosen, ImportLimits(max_file_bytes=10)).scan()

    assert entry_names(scan) == ["a.fit", "b.fit"]
    assert issues(scan) == {"CON.fit": ImportIssue.RESERVED_NAME, "big.gpx": ImportIssue.TOO_LARGE}
    with pytest.raises(ImportLimitExceededError):
        FilesImportSource(chosen[:2], ImportLimits(max_activity_files=1)).scan()


def test_chosen_symlink_is_refused(tmp_path: Path, root: Path) -> None:
    write(tmp_path / "fuori", {"x.fit": imp.fit_run(1)})
    make_file_link(root / "l.fit", tmp_path / "fuori" / "x.fit")

    scan = FilesImportSource([root / "l.fit"]).scan()

    assert issues(scan) == {"l.fit": ImportIssue.NOT_REGULAR_FILE}


# --- Lettura e risorse ------------------------------------------------------------------------


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


def test_source_is_a_context_manager_and_close_is_repeatable(root: Path) -> None:
    write(root, {"pack.zip": zw.garmin_download(imp.fit_run(1))})

    with folder(root) as source:
        (entry,) = source.scan().entries
        assert source.read(entry, MIB)
    source.close()
    source.close()


def test_zip_stays_readable_after_the_source_is_closed_and_reopened(root: Path) -> None:
    fit = imp.fit_run(1)
    write(root, {"pack.zip": zw.garmin_download(fit)})
    source = folder(root)
    (entry,) = source.scan().entries
    assert source.read(entry, MIB) == fit

    source.close()

    assert source.read(entry, MIB) == fit
    source.close()


def iter_sizes(scan: ImportScan) -> Iterator[int]:
    return (entry.size_bytes for entry in scan.entries)


def test_sizes_are_the_declared_ones(root: Path) -> None:
    fit = imp.fit_run(1)
    write(root, {"run.fit": fit, "pack.zip": zw.build_zip([("a.fit", fit * 2)])})

    assert sorted(iter_sizes(folder(root).scan())) == [len(fit), 2 * len(fit)]


# --- Conteggio mentre si legge e annullamento (R6) --------------------------------------------


class _CountingListing:
    """Elenco di una cartella che conta le voci consegnate al chiamante."""

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
    assert consumed[0] == 6  # il tetto più una: non le 40 voci


def test_a_scan_can_be_cancelled_between_entries(root: Path) -> None:
    write(root, {f"f{n:02d}.fit": imp.fit_run(n) for n in range(1, 11)})
    calls = [0]

    def cancel_after_three() -> bool:
        calls[0] += 1
        return calls[0] > 3

    with pytest.raises(ImportScanCancelledError):
        folder(root).scan(cancel_after_three)

    assert calls[0] == 4  # si è fermata alla prima richiesta di annullare


def test_a_scan_without_cancellation_is_unchanged(root: Path) -> None:
    write(root, {"a.fit": imp.fit_run(1)})

    assert len(folder(root).scan(lambda: False).entries) == 1
