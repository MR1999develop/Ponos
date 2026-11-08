# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
# ruff: noqa: E501
"""Phase 8 acceptance campaign, hostile and damaged backup files: T-SEC (untrusted ZIP) and
T-DATI for the restore.

Every backup here is forged from a real small one with a manifest that stays consistent, so a test
breaks exactly one rule. Every restore must refuse with the right error, leave no folder or
residue, and never write outside the target.
"""

from __future__ import annotations

import hashlib
import io
import json
import random
import sqlite3
import zipfile
from collections.abc import Callable, Iterator
from contextlib import closing
from pathlib import Path

import pytest

from ponos_coach.profiles.backup import RestoreProfile
from ponos_coach.profiles.domain.backup import (
    BACKUP_SUFFIX,
    MANIFEST_NAME,
    BackupLimits,
)
from ponos_coach.profiles.ports import (
    BackupError,
    BackupInvalidError,
    BackupProblem,
    BackupTooNewError,
)
from ponos_coach.storage.profile_backup import FileProfileBackup
from tests.collaudo.fase5_support import World
from tests.collaudo.fase8_support import (
    Entries,
    backup_use_case,
    database_name,
    folders,
    forge,
    make_rich_world,
    manifest_of,
    read_entries,
    restore_use_case,
    without_residue,
)

PROBLEM = BackupProblem


@pytest.fixture(scope="module")
def good(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Entries]:
    base = tmp_path_factory.mktemp("f8-hostile")
    world: World = make_rich_world(base / "p", runs=3)
    destination = base / f"good{BACKUP_SUFFIX}"
    backup_use_case()(world.profile.folder, destination)
    world.close()
    entries = read_entries(destination)
    assert len([n for n in entries if n.startswith("archivio/")]) >= 3
    yield entries


def archive_names(entries: Entries) -> list[str]:
    return [n for n in entries if n.startswith("archivio/")]


def refuse(
    tmp_path: Path,
    backup: Path,
    expected: type[BaseException] = BackupInvalidError,
    problem: BackupProblem | None = None,
    *,
    restore: RestoreProfile | None = None,
) -> None:
    """The restore refuses with `expected` (and `problem`), leaves nothing and escapes nowhere."""
    target = tmp_path / "target"
    seen = sorted(p.name for p in tmp_path.iterdir())
    with pytest.raises(expected) as caught:
        (restore or restore_use_case())(backup, target)
    if problem is not None:
        assert isinstance(caught.value, BackupInvalidError) and caught.value.problem is problem
    assert without_residue(target)
    assert sorted(p.name for p in tmp_path.iterdir() if p.name != "target") == seen
    assert not list(tmp_path.parent.glob("evil*"))
    assert "target" not in str(caught.value) and str(tmp_path) not in str(caught.value)


def forged(tmp_path: Path, entries: Entries, **kwargs: object) -> Path:
    return forge(tmp_path / f"hostile{BACKUP_SUFFIX}", entries, **kwargs)  # type: ignore[arg-type]


def test_the_good_backup_restores(good: Entries, tmp_path: Path) -> None:
    """The forge itself is faithful: an unmodified rebuild is accepted."""
    restore_use_case()(forged(tmp_path, good), tmp_path / "ok")

    assert (tmp_path / "ok" / database_name()).is_file()


# --- Names: traversal and the closed list ------------------------------------------------------
HOSTILE_NAMES = [
    "../evil.txt",
    "..\\evil.txt",
    "/evil.txt",
    "\\evil.txt",
    "C:\\evil.txt",
    "C:evil.txt",
    "archivio/2026/../../evil.fit",
    "archivio/2026/" + "a" * 64 + ".fit/../../../evil",
    "archivio/2026/" + "a" * 64 + ".fit ",
    "archivio/2026/" + "A" * 64 + ".fit",
    "archivio/2026/" + "a" * 64 + ".FIT",
    "archivio/2026/" + "a" * 64 + ".exe",
    "archivio/2026/" + "a" * 64 + ".fit\x00.exe",
    "ponos.sqlite3-wal",
    "ponos.sqlite3 ",
    "PONOS.SQLITE3",
    "ponos.lock",
    "backup/ponos-v9-v10.sqlite3",
    ".ponos-token",
    "con",
    "nul.fit",
    "archivio/",
    "archivio",
    "",
]


@pytest.mark.parametrize("name", HOSTILE_NAMES)
def test_t_sec_an_entry_outside_the_closed_list_is_refused_whether_or_not_the_manifest_lists_it(
    good: Entries, tmp_path: Path, name: str
) -> None:
    payload = b"hostile payload"
    unlisted = forged(tmp_path, good, extra=((name, payload),))
    refuse(tmp_path, unlisted, BackupInvalidError)  # the ZIP holds more than the manifest lists
    (tmp_path / f"hostile{BACKUP_SUFFIX}").unlink()

    def list_it(document: dict[str, object]) -> None:
        files = document["files"]
        assert isinstance(files, list)
        files.append(
            {
                "path": name,
                "size_bytes": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
        )

    listed = forged(tmp_path, good, extra=((name, payload),), mutate_manifest=list_it)
    refuse(tmp_path, listed, BackupInvalidError, PROBLEM.NOT_A_BACKUP)


@pytest.mark.xfail(strict=True, reason="D-F8-04: the entry-name pattern accepts Unicode digits")
def test_t_sec_the_year_folder_of_an_entry_is_made_of_ascii_digits_only(
    good: Entries, tmp_path: Path
) -> None:
    """D-F8-04 (low): the entry-name pattern of `profiles/domain/backup.py` uses the digit class
    of `re`, which matches any Unicode decimal digit, so a backup can carry an entry whose year
    folder is made of Arabic-Indic digits and the restore creates a folder with that name; the
    format says "4 digits". The writer (`str.isdecimal`) has the same leniency."""
    payload = b"hostile payload"
    name = "archivio/٢٠٢٦/" + "a" * 64 + ".fit"

    def list_it(document: dict[str, object]) -> None:
        files = document["files"]
        assert isinstance(files, list)
        files.append(
            {
                "path": name,
                "size_bytes": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
        )

    refuse(tmp_path, forged(tmp_path, good, extra=((name, payload),), mutate_manifest=list_it))


def test_t_sec_a_duplicate_entry_name_is_refused(good: Entries, tmp_path: Path) -> None:
    name = archive_names(good)[0]

    refuse(tmp_path, forged(tmp_path, good, extra=((name, b"a second copy, different"),)))


def test_t_sec_a_duplicate_database_name_is_refused(good: Entries, tmp_path: Path) -> None:
    refuse(tmp_path, forged(tmp_path, good, extra=((database_name(), b"another database"),)))


def test_t_sec_a_duplicate_manifest_is_refused(good: Entries, tmp_path: Path) -> None:
    manifest = json.dumps(manifest_of(good)).encode()

    refuse(tmp_path, forged(tmp_path, good, extra=((MANIFEST_NAME, manifest),)))


# --- Manifest that lies ---------------------------------------------------------------------------
def lie(change: Callable[[dict[str, object]], None]) -> Callable[[dict[str, object]], None]:
    return change


def first_archive(document: dict[str, object]) -> dict[str, object]:
    files = document["files"]
    assert isinstance(files, list)
    return next(f for f in files if f["path"].startswith("archivio/"))


def test_t_sec_a_wrong_checksum_is_refused_as_such(good: Entries, tmp_path: Path) -> None:
    def change(document: dict[str, object]) -> None:
        first_archive(document)["sha256"] = "0" * 64

    refuse(tmp_path, forged(tmp_path, good, mutate_manifest=change), problem=PROBLEM.CHECKSUM)


@pytest.mark.parametrize("delta", [-1, 1, 10**6])
def test_t_sec_a_declared_size_that_differs_from_the_entry_is_refused(
    good: Entries, tmp_path: Path, delta: int
) -> None:
    def change(document: dict[str, object]) -> None:
        entry = first_archive(document)
        assert isinstance(entry["size_bytes"], int)
        entry["size_bytes"] += delta

    refuse(tmp_path, forged(tmp_path, good, mutate_manifest=change), problem=PROBLEM.CHECKSUM)


def test_t_sec_a_listed_file_that_is_not_in_the_zip_is_refused(
    good: Entries, tmp_path: Path
) -> None:
    def change(document: dict[str, object]) -> None:
        files = document["files"]
        assert isinstance(files, list)
        files.append(
            {"path": "archivio/2020/" + "e" * 64 + ".fit", "size_bytes": 3, "sha256": "e" * 64}
        )

    refuse(tmp_path, forged(tmp_path, good, mutate_manifest=change), problem=PROBLEM.UNSAFE_ENTRY)


def test_t_sec_a_zip_file_the_manifest_does_not_list_is_refused(
    good: Entries, tmp_path: Path
) -> None:
    def change(document: dict[str, object]) -> None:
        files = document["files"]
        assert isinstance(files, list)
        files.remove(first_archive(document))

    refuse(tmp_path, forged(tmp_path, good, mutate_manifest=change), problem=PROBLEM.UNSAFE_ENTRY)


def test_t_sec_a_manifest_with_two_databases_or_none_is_refused(
    good: Entries, tmp_path: Path
) -> None:
    def none(document: dict[str, object]) -> None:
        files = document["files"]
        assert isinstance(files, list)
        files[:] = [f for f in files if f["path"] != database_name()]

    refuse(tmp_path, forged(tmp_path, good, mutate_manifest=none), problem=PROBLEM.NOT_A_BACKUP)

    def two(document: dict[str, object]) -> None:
        files = document["files"]
        assert isinstance(files, list)
        files.append(next(dict(f) for f in files if f["path"] == database_name()))

    refuse(tmp_path, forged(tmp_path, good, mutate_manifest=two), problem=PROBLEM.NOT_A_BACKUP)


@pytest.mark.parametrize(
    "change",
    [
        lambda d: d.update(format="something-else"),
        lambda d: d.pop("format"),
        lambda d: d.update(profile_id="not-a-uuid"),
        lambda d: d.update(profile_id=12),
        lambda d: d.update(created_utc="2026-11-08T10:00:00+02:00"),
        lambda d: d.update(created_utc="yesterday"),
        lambda d: d.update(app_version=""),
        lambda d: d.update(app_version="9" * 40),
        lambda d: d.update(schema_version=0),
        lambda d: d.update(schema_version="7"),
        lambda d: d.update(schema_version=True),
        lambda d: d.update(format_version=0),
        lambda d: d.update(format_version="1"),
        lambda d: d.update(format_version=1.0),
        lambda d: d.update(files="all of them"),
        lambda d: d.update(files=[1, 2, 3]),
        lambda d: d.update(files=[{"path": 5, "size_bytes": 1, "sha256": "a" * 64}]),
        lambda d: first_archive(d).update(size_bytes=-1),
        lambda d: first_archive(d).update(size_bytes=2**70),
        lambda d: first_archive(d).update(sha256="ABCDEF" * 10 + "ABCD"),
        lambda d: first_archive(d).update(sha256="g" * 64),
    ],
    ids=lambda f: f"case{id(f) % 1000}",
)
def test_t_sec_a_manifest_with_a_wrong_field_is_refused_as_not_a_backup(
    good: Entries, tmp_path: Path, change: Callable[[dict[str, object]], object]
) -> None:
    def mutate(document: dict[str, object]) -> None:
        change(document)

    refuse(tmp_path, forged(tmp_path, good, mutate_manifest=mutate))


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"\xff\xfe\x00bad utf8",
        b"{",
        b"[]",
        b"null",
        b"[" * 100_000,
        b'{"format": "ponos-coach-profile-backup"}',
        b'{"a":' * 50_000,
    ],
)
def test_t_sec_a_manifest_that_is_not_json_or_is_hostile_json_is_refused(
    good: Entries, tmp_path: Path, raw: bytes
) -> None:
    entries = {k: v for k, v in good.items() if k != MANIFEST_NAME}
    target = tmp_path / f"raw{BACKUP_SUFFIX}"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
        archive.writestr(MANIFEST_NAME, raw)

    refuse(tmp_path, target)


def test_t_sec_a_backup_without_a_manifest_is_refused(good: Entries, tmp_path: Path) -> None:
    target = tmp_path / f"nomanifest{BACKUP_SUFFIX}"
    with zipfile.ZipFile(target, "w") as archive:
        for name, data in good.items():
            if name != MANIFEST_NAME:
                archive.writestr(name, data)

    refuse(tmp_path, target, problem=PROBLEM.NOT_A_BACKUP)


# --- Versions -----------------------------------------------------------------------------------
@pytest.mark.parametrize("version", [2, 3, 99, 2**31])
def test_a_newer_format_version_is_refused_with_the_update_message(
    good: Entries, tmp_path: Path, version: int
) -> None:
    refuse(
        tmp_path,
        forged(tmp_path, good, mutate_manifest=lambda d: d.update(format_version=version)),
        BackupTooNewError,
    )


def database_with_version(good: Entries, version: int) -> bytes:
    scratch = Path(f"{id(good)}-scratch.sqlite3")
    return _rewritten_database(
        good[database_name()], lambda c: c.execute(f"PRAGMA user_version = {version}"), scratch
    )


def _rewritten_database(
    data: bytes, change: Callable[[sqlite3.Connection], object], scratch: Path
) -> bytes:
    import tempfile  # noqa: PLC0415

    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / scratch.name
        path.write_bytes(data)
        with closing(sqlite3.connect(path)) as connection:
            change(connection)
            connection.commit()
            connection.execute("PRAGMA journal_mode = DELETE").fetchall()
        return path.read_bytes()


def test_a_database_newer_than_the_app_is_refused_as_too_new_even_with_a_consistent_manifest(
    good: Entries, tmp_path: Path
) -> None:
    entries = dict(good)
    entries[database_name()] = database_with_version(good, 9999)

    backup = forged(tmp_path, entries, mutate_manifest=lambda d: d.update(schema_version=9999))

    refuse(tmp_path, backup, BackupTooNewError)


def test_a_manifest_that_misdescribes_the_database_version_is_refused(
    good: Entries, tmp_path: Path
) -> None:
    refuse(
        tmp_path,
        forged(tmp_path, good, mutate_manifest=lambda d: d.update(schema_version=1)),
        problem=PROBLEM.DATABASE_INVALID,
    )


def test_a_manifest_with_another_profile_id_than_the_database_is_refused(
    good: Entries, tmp_path: Path
) -> None:
    refuse(
        tmp_path,
        forged(
            tmp_path,
            good,
            mutate_manifest=lambda d: d.update(profile_id="11111111-2222-4333-8444-555555555555"),
        ),
        problem=PROBLEM.DATABASE_INVALID,
    )


@pytest.mark.parametrize(
    "damage", ["garbage", "truncated", "flipped", "empty", "wrong-application"]
)
def test_a_database_that_is_not_an_intact_profile_is_refused_even_if_its_checksum_matches(
    good: Entries, tmp_path: Path, damage: str
) -> None:
    data = good[database_name()]
    damaged = {
        "garbage": b"SQLite format 3\x00" + b"\x00" * 100 + b"junk" * 500,
        "truncated": data[: len(data) // 2],
        "flipped": data[:4096] + bytes(b ^ 0xFF for b in data[4096:8192]) + data[8192:],
        "empty": b"",
        "wrong-application": b"%PDF-1.7 this is not a database",
    }[damage]
    entries = dict(good)
    entries[database_name()] = damaged

    refuse(tmp_path, forged(tmp_path, entries))


# --- Limits: bombs ---------------------------------------------------------------------------------
def test_t_sec_a_compression_bomb_is_refused_by_the_ratio_before_extracting(
    good: Entries, tmp_path: Path
) -> None:
    name = archive_names(good)[0]
    entries = dict(good)
    entries[name] = b"\x00" * (4 * 1024 * 1024)  # compresses ~1000:1
    backup = forged(tmp_path, entries)
    small = RestoreProfile(FileProfileBackup(BackupLimits(max_ratio=50)), folders())

    refuse(tmp_path, backup, problem=PROBLEM.LIMIT, restore=small)


def test_t_sec_a_file_larger_than_the_limit_is_refused(good: Entries, tmp_path: Path) -> None:
    entries = dict(good)
    entries[archive_names(good)[0]] = b"x" * 5000
    small = RestoreProfile(FileProfileBackup(BackupLimits(max_file_bytes=4096)), folders())

    refuse(tmp_path, forged(tmp_path, entries), restore=small)


def test_t_sec_too_many_entries_are_refused_before_reading_them(
    good: Entries, tmp_path: Path
) -> None:
    extra = tuple((f"archivio/2026/{i:064x}.fit", b"z") for i in range(300))
    small = RestoreProfile(FileProfileBackup(BackupLimits(max_entries=50)), folders())

    refuse(tmp_path, forged(tmp_path, good, extra=extra), restore=small)


def test_t_sec_a_total_above_the_limit_is_refused(good: Entries, tmp_path: Path) -> None:
    small = RestoreProfile(FileProfileBackup(BackupLimits(max_total_bytes=10_000)), folders())

    refuse(tmp_path, forged(tmp_path, good), restore=small)


def test_t_sec_an_entry_that_inflates_beyond_its_declared_size_is_refused(
    good: Entries, tmp_path: Path
) -> None:
    """The ZIP header says 10 bytes, the compressed data holds far more: never more than declared."""
    name = archive_names(good)[0]
    entries = dict(good)
    entries[name] = b"A" * 200_000
    backup = forged(tmp_path, entries)
    raw = bytearray(backup.read_bytes())
    marker = name.encode()
    patched = 0
    for signature, offset in ((b"PK\x03\x04", 22), (b"PK\x01\x02", 24)):  # uncompressed size fields
        position = 0
        while (position := raw.find(signature, position)) != -1:
            name_at = position + (30 if signature == b"PK\x03\x04" else 46)
            if raw[name_at : name_at + len(marker)] == marker:
                raw[position + offset : position + offset + 4] = (10).to_bytes(4, "little")
                patched += 1
            position += 4
    assert patched == 2
    backup.write_bytes(bytes(raw))

    refuse(tmp_path, backup)


def test_t_sec_an_encrypted_entry_is_refused(good: Entries, tmp_path: Path) -> None:
    name = archive_names(good)[0]
    backup = forged(tmp_path, good)
    raw = bytearray(backup.read_bytes())
    marker = name.encode()
    patched = 0
    for signature, flags_at, name_at in ((b"PK", 6, 30), (b"PK", 8, 46)):
        position = 0
        while (position := raw.find(signature, position)) != -1:
            start = position + name_at
            if raw[start : start + len(marker)] == marker:
                raw[position + flags_at] |= 0x01  # the "encrypted" bit
                patched += 1
            position += 4
    assert patched == 2
    backup.write_bytes(bytes(raw))

    refuse(tmp_path, backup, problem=PROBLEM.UNSAFE_ENTRY)


# --- Not backups at all ------------------------------------------------------------------------
@pytest.mark.parametrize(
    "content",
    [b"", b"PK", b"PK\x05\x06" + b"\x00" * 18, b"not a zip at all" * 1000, bytes(range(256)) * 64],
    ids=["empty", "two-bytes", "empty-zip", "text", "binary"],
)
def test_a_file_that_is_not_a_backup_is_refused(tmp_path: Path, content: bytes) -> None:
    source = tmp_path / f"x{BACKUP_SUFFIX}"
    source.write_bytes(content)

    refuse(tmp_path, source)


def test_a_zip_of_something_else_is_refused(tmp_path: Path) -> None:
    source = tmp_path / f"other{BACKUP_SUFFIX}"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("notes.txt", "a plain archive")
        archive.writestr("a/b/c.fit", b"FIT")

    refuse(tmp_path, source)


def test_a_missing_source_and_a_directory_as_source_are_errors_not_crashes(tmp_path: Path) -> None:
    for source in (tmp_path / "missing.ponosbackup", tmp_path):
        with pytest.raises(BackupError):
            restore_use_case()(source, tmp_path / "t")
    assert without_residue(tmp_path / "t")


def test_a_zip_inside_the_backup_is_just_a_name_outside_the_list(
    good: Entries, tmp_path: Path
) -> None:
    inner = io.BytesIO()
    with zipfile.ZipFile(inner, "w") as archive:
        archive.writestr("x", "y")

    refuse(tmp_path, forged(tmp_path, good, extra=(("inner.zip", inner.getvalue()),)))


# --- Damage by bit flips and truncation (fuzzing with an oracle) ------------------------------------
def test_fuzz_a_damaged_real_backup_is_refused_or_restores_exactly_and_never_crashes(
    good: Entries, tmp_path: Path
) -> None:
    """Flip or cut at 80 random places. The outcome is one of the documented errors, or, if the
    damage hit nothing that matters (the ZIP's own padding), the very same profile."""
    backup = forged(tmp_path, good)
    original = backup.read_bytes()
    rng = random.Random(20261108)
    accepted = 0
    for n in range(80):
        damaged = bytearray(original)
        if n % 4 == 0:
            damaged = damaged[: rng.randrange(1, len(damaged))]
        else:
            for _ in range(rng.choice([1, 1, 2, 8])):
                damaged[rng.randrange(len(damaged))] ^= 1 << rng.randrange(8)
        source = tmp_path / f"fuzz{n}{BACKUP_SUFFIX}"
        source.write_bytes(bytes(damaged))
        target = tmp_path / f"out{n}"
        try:
            restore_use_case()(source, target)
        except BackupError:
            assert without_residue(target), n
        else:
            accepted += 1
            with closing(sqlite3.connect(target / database_name())) as connection:
                assert connection.execute("PRAGMA quick_check").fetchall() == [("ok",)]
    assert accepted < 80
