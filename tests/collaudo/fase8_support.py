# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
# ruff: noqa: S105
"""Scaffolding for the phase 8 acceptance campaign: a real profile with imported FIT runs, goals,
a plan, an injury and a kept Garmin workout; the backup and restore use cases wired the way the
app wires them; and a small forge of backup files (rewritten entries with a consistent manifest, so
that exactly one thing is wrong at a time).

The athlete is invented ("Atleta di prova"); the Garmin token of the tests is a placeholder.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import warnings
import zipfile
from collections.abc import Callable, Mapping
from contextlib import closing
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from ponos_coach.goals.domain import Priority
from ponos_coach.injury.domain import Injury, InjuryId
from ponos_coach.injury.use_cases import OpenInjury
from ponos_coach.planning.workout_calendar import NewBackup
from ponos_coach.platform.profile_folders import LocalProfileFolders
from ponos_coach.profiles.backup import BackupProfile, RestoreProfile
from ponos_coach.profiles.domain import PROFILE_DATABASE_NAME, FolderKind
from ponos_coach.profiles.domain.backup import MANIFEST_NAME
from ponos_coach.storage.profile_backup import FileProfileBackup
from tests.collaudo.fase5_support import TODAY, World, make_world, race
from tests.injury.builders import questionnaire

NOW = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)
TOKEN = "TOKEN-SEGNAPOSTO-8842-abcdef0123456789"
Entries = dict[str, bytes]


def folders() -> LocalProfileFolders:
    return LocalProfileFolders(lambda _folder: FolderKind.LOCAL)


def backup_use_case() -> BackupProfile:
    return BackupProfile(FileProfileBackup(), lambda: NOW, "0.1.0")


def restore_use_case() -> RestoreProfile:
    return RestoreProfile(FileProfileBackup(), folders())


def make_rich_world(base: Path, *, runs: int = 12) -> World:
    """A profile with `runs` imported runs, a race, a plan, an open injury and a kept workout."""
    base.mkdir(parents=True, exist_ok=True)
    world = make_world(base)
    world.import_history(TODAY - timedelta(days=2 * runs), TODAY - timedelta(days=1), every=2)
    world.add_goal(race("a", TODAY + timedelta(days=84), Priority.A, 10_000))
    world.built()
    session = world.profile.session
    OpenInjury(session.injuries)(Injury(InjuryId("i1"), TODAY, questionnaire(), checkins=()))
    session.workout_backups.add(
        NewBackup("Ponos · Easy 30 min", TODAY + timedelta(days=3), '{"workoutName": "x"}', NOW)
    )
    return world


def all_tables(database: Path) -> dict[str, list[tuple[object, ...]]]:
    """Every table of a profile database, rows sorted (the `meta` table included)."""
    with closing(sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)) as connection:
        names = [
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        ]
        return {
            name: sorted(connection.execute(f'SELECT * FROM "{name}"').fetchall(), key=repr)  # noqa: S608
            for name in sorted(names)
        }


def tree_bytes(folder: Path, *, skip: tuple[str, ...] = ()) -> dict[str, bytes]:
    """Every regular file under `folder` by relative posix path, except the ones in `skip`."""
    return {
        path.relative_to(folder).as_posix(): path.read_bytes()
        for path in sorted(folder.rglob("*"))
        if path.is_file() and not path.relative_to(folder).as_posix().startswith(skip)
    }


def read_entries(backup: Path) -> Entries:
    with zipfile.ZipFile(backup) as archive:
        return {info.filename: archive.read(info) for info in archive.infolist()}


def manifest_of(entries: Mapping[str, bytes]) -> dict[str, object]:
    document = json.loads(entries[MANIFEST_NAME])
    assert isinstance(document, dict)
    return document


def forge(
    destination: Path,
    entries: Entries,
    *,
    mutate_manifest: Callable[[dict[str, object]], None] | None = None,
    keep_manifest_consistent: bool = True,
    extra: tuple[tuple[str, bytes], ...] = (),
    compress: int = zipfile.ZIP_DEFLATED,
) -> Path:
    """Writes a backup file from `entries`.

    With `keep_manifest_consistent` the manifest lists exactly the entries given (with their real
    sizes and SHA-256) before `mutate_manifest` changes it, so a test breaks one rule only.
    `extra` entries are appended as they are (names may be hostile, duplicates are allowed).
    """
    body = {name: data for name, data in entries.items() if name != MANIFEST_NAME}
    document = manifest_of(entries)
    if keep_manifest_consistent:
        document["files"] = [
            {"path": name, "size_bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
            for name, data in sorted(body.items())
        ]
    if mutate_manifest is not None:
        mutate_manifest(document)
    with (
        warnings.catch_warnings(),
        zipfile.ZipFile(destination, "w", compress) as archive,
    ):
        warnings.simplefilter("ignore")  # duplicate names are the point of some tests
        for name, data in body.items():
            archive.writestr(name, data)
        for name, data in extra:
            archive.writestr(name, data)
        archive.writestr(MANIFEST_NAME, json.dumps(document).encode("utf-8"))
    return destination


def without_residue(folder: Path) -> bool:
    """True if `folder` is gone or empty."""
    return not folder.exists() or not any(folder.iterdir())


def database_name() -> str:
    return PROFILE_DATABASE_NAME


def day(offset: int = 0) -> date:
    return TODAY + timedelta(days=offset)
