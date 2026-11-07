# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Impalcatura del collaudo della fase 1: pila reale (SQLite, archivio su disco, sorgenti vere).

I test di collaudo non usano i fake in memoria: provano il comportamento osservabile della
pila che l'utente riceve. Qui stanno i costruttori comuni, i controlli di coerenza tra indice
e archivio e la cattura dei log.
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
    """Registro JSON, cartelle locali e SQLite veri (tipo di cartella fisso: locale)."""
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
    """Un profilo aperto sulla pila reale, con l'archivio dei suoi originali."""

    session: ProfileSession
    ports: ProfilePorts

    @property
    def folder(self) -> Path:
        return self.session.folder

    @property
    def archive(self) -> FileArchive:
        return FileArchive.in_profile_folder(self.session.folder)

    def import_source(self, source: ImportSource) -> ImportSummary:
        """Importa da una sorgente (chiusa alla fine) con l'orologio fermo."""
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
        """Panoramica calcolata come fa l'app (cache dei carichi compresa)."""
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
    """Crea un profilo vero in `base / folder_name`; la configurazione sta in `base / config`."""
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
    """File finali dell'archivio (non i temporanei), ordinati."""
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
    """Esito per nome di file (i nomi sono unici nei test)."""
    return {r.name: (r.status, r.issue) for r in summary.results}


def assert_index_matches_archive(
    archive: ActivityArchive, records: Iterable[ActivityRecord]
) -> None:
    """Ogni record punta a un file che c'è, con SHA-256 e dimensione registrati."""
    for record in records:
        archive.verify(record.archived)


def format_logs(records: Iterable[logging.LogRecord]) -> str:
    """Tutto ciò che i log mostrerebbero: messaggio, eccezione e traceback compresi."""
    formatter = logging.Formatter("%(levelname)s %(name)s %(message)s")
    return "\n".join(formatter.format(record) for record in records)
