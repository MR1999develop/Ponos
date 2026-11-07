# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Scaffolding for the phase 1 acceptance campaign: real stack (SQLite, on-disk archive, real
sources).

The campaign tests do not use in-memory fakes: they exercise the observable behaviour of the
stack the user receives. Here are the shared builders, the consistency checks between index
and archive, and the log capture.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from ponos_coach.activities.decoding import decode_stored_activity
from ponos_coach.activities.domain import ActivityRecord
from ponos_coach.activities.import_limits import ImportLimits
from ponos_coach.activities.ports import ActivityArchive, ImportIssue, ImportSource
from ponos_coach.activities.use_cases import ImportStatus, ImportSummary, import_activities
from ponos_coach.load.use_cases import ComputeProfileOverview, Decoder, ProfileOverview
from ponos_coach.platform.profile_folders import LocalProfileFolders
from ponos_coach.platform.profile_registry import JsonProfileRegistry
from ponos_coach.profiles.domain import PROFILE_ARCHIVE_DIR, FolderKind, ProfileId, ProfileName
from ponos_coach.profiles.use_cases import (
    CreateProfile,
    NewProfileRequest,
    OpenProfileFromFolder,
    OpenRegisteredProfile,
    ProfilePorts,
    ProfileSession,
)
from ponos_coach.storage.file_archive import FileArchive
from ponos_coach.storage.import_scan import FilesImportSource, FolderImportSource
from ponos_coach.storage.sqlite_profile import SqliteProfileStorage
from tests.activities.import_fakes import FixedClock
from tests.builders import physiology

NOW = datetime(2026, 7, 1, 8, 0, tzinfo=UTC)
TODAY = date(2026, 7, 1)
PROFILE_NAME = "Atleta di prova"
TEMP_PREFIX = ".tmp-"


def real_ports(config_dir: Path) -> ProfilePorts:
    """Real JSON registry, local folders and SQLite (fixed folder kind: local)."""
    return ProfilePorts(
        JsonProfileRegistry(config_dir),
        LocalProfileFolders(lambda _folder: FolderKind.LOCAL),
        SqliteProfileStorage(),
    )


def fixed_now() -> datetime:
    return NOW


def new_id() -> ProfileId:
    return ProfileId(uuid.uuid4())


@dataclass(slots=True)
class RealProfile:
    """A profile opened on the real stack, with the archive of its originals."""

    session: ProfileSession
    ports: ProfilePorts

    @property
    def folder(self) -> Path:
        return self.session.folder

    @property
    def archive(self) -> FileArchive:
        return FileArchive.in_profile_folder(self.session.folder)

    def import_source(self, source: ImportSource) -> ImportSummary:
        """Imports from a source (closed at the end) with the clock stopped."""
        try:
            return import_activities(source, self.archive, self.session.activities, FixedClock(NOW))
        finally:
            source.close()

    def import_folder(self, folder: Path, limits: ImportLimits | None = None) -> ImportSummary:
        return self.import_source(FolderImportSource(folder, limits))

    def import_files(
        self, files: Sequence[Path], limits: ImportLimits | None = None
    ) -> ImportSummary:
        return self.import_source(FilesImportSource(files, limits))

    def records(self) -> Sequence[ActivityRecord]:
        return self.session.activities.list_by_start()

    def overview(self, decode: Decoder = decode_stored_activity) -> ProfileOverview:
        """Overview computed the way the app does it (load cache included)."""
        return ComputeProfileOverview(
            self.session.activities,
            self.archive,
            self.session.physiology,
            self.session.load_cache,
            decode,
            sport_weights=self.session.sport_weights,
            timezone=ZoneInfo("Europe/Rome"),
            today=lambda: TODAY,
        ).execute()

    def close(self) -> None:
        self.session.close()


def create_profile(
    base: Path, name: str = PROFILE_NAME, *, folder_name: str = "profilo"
) -> RealProfile:
    """Creates a real profile in `base / folder_name`; the configuration lives in `base /
    config`."""
    ports = real_ports(base / "config")
    create = CreateProfile(ports, fixed_now, new_id)
    request = NewProfileRequest(
        ProfileName.parse(name), base / folder_name, "Europe/Rome", physiology(), TODAY
    )
    return RealProfile(create(request), ports)


def open_profile_from_folder(ports: ProfilePorts, folder: Path) -> RealProfile:
    return RealProfile(OpenProfileFromFolder(ports, fixed_now)(folder), ports)


def open_registered(ports: ProfilePorts, profile_id: ProfileId) -> RealProfile:
    return RealProfile(OpenRegisteredProfile(ports, fixed_now)(profile_id), ports)


def archive_dir(profile_folder: Path) -> Path:
    return profile_folder / PROFILE_ARCHIVE_DIR


def stored_files(profile_folder: Path) -> list[Path]:
    """Final files of the archive (not the temporary ones), sorted."""
    root = archive_dir(profile_folder)
    if not root.exists():
        return []
    return sorted(p for p in root.rglob("*") if p.is_file() and not p.name.startswith(TEMP_PREFIX))


def temporaries(profile_folder: Path) -> list[Path]:
    root = archive_dir(profile_folder)
    if not root.exists():
        return []
    return sorted(p for p in root.rglob("*") if p.is_file() and p.name.startswith(TEMP_PREFIX))


def outcomes(summary: ImportSummary) -> dict[str, tuple[ImportStatus, ImportIssue | None]]:
    """Outcome by file name (names are unique in the tests)."""
    return {r.name: (r.status, r.issue) for r in summary.results}


def assert_index_matches_archive(
    archive: ActivityArchive, records: Iterable[ActivityRecord]
) -> None:
    """Every record points to an existing file, with SHA-256 and size recorded."""
    for record in records:
        archive.verify(record.archived)


def format_logs(records: Iterable[logging.LogRecord]) -> str:
    """Everything the logs would show: message, exception and traceback included."""
    formatter = logging.Formatter("%(levelname)s %(name)s %(message)s")
    return "\n".join(formatter.format(record) for record in records)
