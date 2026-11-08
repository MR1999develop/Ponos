# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Phase 8 acceptance campaign, backup and restore of a real profile: T-DATI round trip, what
never goes in a backup, what a backup refuses to do, and the measured time.

The profile has FIT runs imported through the real import, goals, a plan, an injury and a kept
workout; it is backed up while open, restored into a new folder and compared table by table and
byte by byte. The Garmin token of the tests is a placeholder.
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from time import perf_counter

import pytest

from ponos_coach.activities.domain import FileFormat
from ponos_coach.profiles.domain import PROFILE_ARCHIVE_DIR
from ponos_coach.profiles.domain.backup import BACKUP_SUFFIX, MANIFEST_NAME, is_backup_entry_path
from ponos_coach.profiles.ports import BackupFileExistsError, FolderNotEmptyError
from ponos_coach.storage.file_archive import FileArchive
from ponos_coach.storage.sqlite_profile import SqliteProfileStorage
from tests.builders import T0
from tests.collaudo.fase5_support import World
from tests.collaudo.fase8_support import (
    NOW,
    TOKEN,
    all_tables,
    backup_use_case,
    database_name,
    make_rich_world,
    read_entries,
    restore_use_case,
    tree_bytes,
)
from tests.fakes.tokens import InMemoryTokenStore  # noqa: F401  (documents where tokens live)


@pytest.fixture(scope="module")
def rich(tmp_path_factory: pytest.TempPathFactory) -> Iterator[World]:
    base = tmp_path_factory.mktemp("f8-rich")
    world = make_rich_world(base / "p")
    yield world
    world.close()


@pytest.fixture(scope="module")
def backup_file(rich: World, tmp_path_factory: pytest.TempPathFactory) -> Path:
    destination = tmp_path_factory.mktemp("f8-out") / f"rich{BACKUP_SUFFIX}"
    backup_use_case()(rich.profile.folder, destination)  # the profile is open: a live snapshot
    return destination


def test_t_dati_05_a_real_profile_makes_the_round_trip_table_by_table_and_byte_by_byte(
    rich: World, backup_file: Path, tmp_path: Path
) -> None:
    target = tmp_path / "restored"

    summary = restore_use_case()(backup_file, target)

    assert summary.archive_files >= 6
    assert all_tables(target / database_name()) == all_tables(rich.profile.folder / database_name())
    assert tree_bytes(target / PROFILE_ARCHIVE_DIR) == tree_bytes(
        rich.profile.folder / PROFILE_ARCHIVE_DIR
    )


def test_t_dati_05_the_restored_profile_opens_and_gives_the_same_overview_and_plan(
    rich: World, backup_file: Path, tmp_path: Path
) -> None:
    base = tmp_path / "again"
    base.mkdir()
    restore_use_case()(backup_file, base / "profilo")
    # the registry of the restored profile is not part of the backup: open it from its folder
    from tests.collaudo.support import open_profile_from_folder, real_ports  # noqa: PLC0415

    opened = open_profile_from_folder(real_ports(base / "config"), base / "profilo")
    restored = World(opened, base, rich.today, seed=False)
    try:
        before, after = rich.overview(), restored.overview()
        assert [a.load.load for a in after.activities] == [a.load.load for a in before.activities]
        assert restored.saved_plan().to_json() == rich.saved_plan().to_json()
        assert len(restored.profile.session.injuries.list_problems()) == 1
        assert len(restored.profile.session.workout_backups.pending()) == 1
        # and it keeps working: another run can be imported into it
        restored.import_run(rich.today - timedelta(days=100))
        assert len(restored.profile.records()) == len(rich.profile.records()) + 1
    finally:
        restored.close()


def test_t_dati_05_restoring_the_same_backup_twice_gives_the_same_profile(
    backup_file: Path, tmp_path: Path
) -> None:
    restore_use_case()(backup_file, tmp_path / "one")
    restore_use_case()(backup_file, tmp_path / "two")

    assert all_tables(tmp_path / "one" / database_name()) == all_tables(
        tmp_path / "two" / database_name()
    )
    assert tree_bytes(tmp_path / "one", skip=(".", "backup", database_name())) == tree_bytes(
        tmp_path / "two", skip=(".", "backup", database_name())
    )


def test_a_backup_holds_exactly_the_closed_list_of_entries(backup_file: Path) -> None:
    entries = read_entries(backup_file)

    assert MANIFEST_NAME in entries and database_name() in entries
    assert all(is_backup_entry_path(n) for n in entries if n != MANIFEST_NAME)
    assert len(entries) == len(set(entries))


# --- Never in a backup ------------------------------------------------------------------------
def test_t_sec_07_the_garmin_token_is_in_no_entry_of_the_backup(
    rich: World, tmp_path: Path
) -> None:
    """Tokens live in the system store, never in the profile folder; even a stray copy of one in
    the folder (a file the app never writes) does not travel: only the closed list does."""
    folder = rich.profile.folder
    stray = [
        folder / "garmin-tokens.json",
        folder / PROFILE_ARCHIVE_DIR / "tokens.txt",
        folder / PROFILE_ARCHIVE_DIR / "2026" / "token.fit",
        folder / ".tokens.tmp",
    ]
    for path in stray:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(TOKEN, encoding="utf-8")
    (folder / "backup").mkdir(exist_ok=True)
    (folder / "backup" / "ponos-v9-v10.sqlite3").write_bytes(b"old copy " + TOKEN.encode())
    try:
        destination = tmp_path / f"t{BACKUP_SUFFIX}"
        backup_use_case()(folder, destination)
        entries = read_entries(destination)
    finally:
        for path in stray:
            path.unlink(missing_ok=True)
        (folder / "backup" / "ponos-v9-v10.sqlite3").unlink()

    assert all(TOKEN.encode() not in data for data in entries.values())
    assert not any("token" in name or name.startswith("backup") for name in entries)
    assert destination.read_bytes().count(TOKEN.encode()) == 0


def test_a_lock_file_a_temporary_and_a_strange_archive_entry_are_not_backed_up(
    rich: World, tmp_path: Path
) -> None:
    archive_root = rich.profile.folder / PROFILE_ARCHIVE_DIR
    noise = [
        archive_root / "2026" / ".tmp-123",
        archive_root / "2026" / "notes.txt",
        archive_root / "20x6" / ("a" * 64 + ".fit"),
        archive_root / "2026" / ("A" * 64 + ".fit"),
        archive_root / "2026" / ("b" * 63 + ".fit"),
        archive_root / "2026" / ("c" * 64 + ".exe"),
    ]
    for path in noise:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"not part of the profile")
    try:
        destination = tmp_path / f"n{BACKUP_SUFFIX}"
        backup_use_case()(rich.profile.folder, destination)
        names = set(read_entries(destination))
    finally:
        for path in noise:
            path.unlink(missing_ok=True)
        if (archive_root / "20x6").exists():
            (archive_root / "20x6").rmdir()

    assert all(is_backup_entry_path(n) for n in names - {MANIFEST_NAME})
    assert not [n for n in names if n.endswith(("notes.txt", ".exe")) or ".tmp" in n]


@pytest.mark.skipif(sys.platform != "win32", reason="a junction is a Windows reparse point")
def test_a_junction_inside_the_archive_is_not_followed_into_the_backup(
    rich: World, tmp_path: Path
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / ("d" * 64 + ".fit")).write_bytes(b"A FILE OUTSIDE THE PROFILE")
    link = rich.profile.folder / PROFILE_ARCHIVE_DIR / "1999"
    made = subprocess.run(  # noqa: S603
        ["cmd", "/c", "mklink", "/J", str(link), str(outside)],  # noqa: S607
        capture_output=True,
        check=False,
    )
    if made.returncode != 0:
        pytest.skip("a junction cannot be created here")
    try:
        destination = tmp_path / f"j{BACKUP_SUFFIX}"
        backup_use_case()(rich.profile.folder, destination)
        entries = read_entries(destination)
    finally:
        subprocess.run(["cmd", "/c", "rmdir", str(link)], check=False)  # noqa: S603, S607

    assert not any("1999" in name for name in entries)
    assert all(b"A FILE OUTSIDE THE PROFILE" not in data for data in entries.values())


# --- What the use cases refuse ----------------------------------------------------------------
def test_a_backup_never_overwrites_a_file_and_never_lands_inside_the_profile(
    rich: World, tmp_path: Path
) -> None:
    existing = tmp_path / f"e{BACKUP_SUFFIX}"
    existing.write_bytes(b"the user's own file")

    with pytest.raises(BackupFileExistsError):
        backup_use_case()(rich.profile.folder, existing)
    assert existing.read_bytes() == b"the user's own file"
    with pytest.raises(ValueError, match="inside"):
        backup_use_case()(rich.profile.folder, rich.profile.folder / f"x{BACKUP_SUFFIX}")
    with pytest.raises(ValueError, match="absolute"):
        backup_use_case()(rich.profile.folder, Path(f"relative{BACKUP_SUFFIX}"))
    with pytest.raises(ValueError, match="ends with"):
        backup_use_case()(rich.profile.folder, tmp_path / "x.zip")
    assert sorted(p.name for p in tmp_path.iterdir()) == [existing.name]  # no stray temporary


def test_a_restore_into_a_folder_with_anything_in_it_is_refused_and_touches_nothing(
    backup_file: Path, tmp_path: Path
) -> None:
    target = tmp_path / "busy"
    target.mkdir()
    (target / "notes.txt").write_text("the user's file", encoding="utf-8")
    (target / "sub").mkdir()
    (target / "sub" / "deeper.txt").write_text("also theirs", encoding="utf-8")
    before = tree_bytes(target)

    with pytest.raises(FolderNotEmptyError):
        restore_use_case()(backup_file, target)

    assert tree_bytes(target) == before


def test_a_restore_never_goes_over_an_existing_profile(rich: World, backup_file: Path) -> None:
    before = tree_bytes(rich.profile.folder, skip=("backup", "ponos.lock"))

    with pytest.raises(FolderNotEmptyError):
        restore_use_case()(backup_file, rich.profile.folder)

    assert tree_bytes(rich.profile.folder, skip=("backup", "ponos.lock")) == before


def test_a_restore_into_an_empty_existing_folder_works(backup_file: Path, tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()

    restore_use_case()(backup_file, empty)

    assert (empty / database_name()).is_file()


@pytest.mark.xfail(
    strict=True,
    reason="D-F8-03: a failed restore deletes an empty folder the user had created",
)
def test_a_failed_restore_keeps_an_empty_folder_the_user_had_created(tmp_path: Path) -> None:
    """D-F8-03 (low): `docs/backup-format.md` says the folder is removed "if the restore created
    it"; `RestoreProfile` removes any empty folder, also one that existed before the call."""
    garbage = tmp_path / f"garbage{BACKUP_SUFFIX}"
    garbage.write_bytes(b"PK this is not a backup" * 10)
    mine = tmp_path / "mine"
    mine.mkdir()

    with pytest.raises(Exception):  # noqa: B017 - the type is the subject of other tests
        restore_use_case()(garbage, mine)

    assert mine.is_dir() and list(mine.iterdir()) == []


# --- Measures ---------------------------------------------------------------------------------
def test_backup_and_restore_time_with_a_thousand_activity_files(tmp_path: Path) -> None:
    """Measured, not promised by the plan: 1,000 originals of 24 KiB and a small database."""
    from tests.collaudo.support import create_profile  # noqa: PLC0415

    base = tmp_path / "many"
    base.mkdir()
    profile = create_profile(base)
    archive = FileArchive.in_profile_folder(profile.folder)
    for n in range(1000):
        archive.store(
            (f"synthetic activity {n} ".encode() * 1200)[:24_576],
            FileFormat.FIT,
            T0 + timedelta(minutes=n),
        )
    profile.close()
    destination = tmp_path / f"many{BACKUP_SUFFIX}"

    started = perf_counter()
    backup_use_case()(profile.folder, destination)
    backed = perf_counter() - started
    started = perf_counter()
    restore_use_case()(destination, tmp_path / "many-restored")
    restored = perf_counter() - started

    sys.stdout.write(
        f"[T-PERF] backup of 1,000 files: {backed:.2f} s; restore: {restored:.2f} s; "
        f"file {destination.stat().st_size / 1024:.0f} KiB at {NOW.year}\n"
    )
    assert backed < 60.0 and restored < 60.0
    assert len(tree_bytes(tmp_path / "many-restored" / PROFILE_ARCHIVE_DIR)) == 1000
    assert SqliteProfileStorage().peek(tmp_path / "many-restored").info.format_version >= 10
