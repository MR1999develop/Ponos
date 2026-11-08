# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Format of a profile backup file: manifest, entry names and limits (pure, no I/O).

A backup is a ZIP archive (suffix `.ponosbackup`) with these entries and no others:

- `manifest.json`: UTF-8 JSON with `format` (the constant `BACKUP_FORMAT`), `format_version`,
  `created_utc`, `app_version`, `profile_id`, `schema_version` and `files`, the list of the
  other entries, each with `path`, `size_bytes` and `sha256`;
- `ponos.sqlite3`: a consistent snapshot of the profile database;
- `archivio/<year>/<sha256>.fit` or `.gpx`: the original activity files.

Never in a backup: Garmin tokens (they live in the system keyring), the lock file and the
`backup/` folder of the pre-migration copies. The ZIP entry names are the only file names the
format knows: they come from the closed list of `is_backup_entry_path`, so nothing read from a
backup is ever used as a path.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, fields
from datetime import UTC, datetime
from typing import Final

from ponos_coach.profiles.domain.layout import PROFILE_ARCHIVE_DIR, PROFILE_DATABASE_NAME
from ponos_coach.profiles.domain.profile import ProfileId

BACKUP_FORMAT: Final = "ponos-coach-profile-backup"
BACKUP_FORMAT_VERSION: Final = 1
BACKUP_SUFFIX: Final = ".ponosbackup"
MANIFEST_NAME: Final = "manifest.json"

_MIB = 1024 * 1024
_GIB = 1024 * _MIB
_ARCHIVE_YEAR = re.compile(r"[0-9]{4}")  # ASCII digits only: `\d` would accept any Unicode digit
_ARCHIVE_ENTRY = re.compile(rf"{PROFILE_ARCHIVE_DIR}/[0-9]{{4}}/[0-9a-f]{{64}}\.(?:fit|gpx)")
_SHA256_HEX = re.compile(r"[0-9a-f]{64}")
_APP_VERSION_MAX_CHARS = 32
_REQUIRED_KEYS = (
    "format",
    "format_version",
    "created_utc",
    "app_version",
    "profile_id",
    "schema_version",
    "files",
)


class ManifestError(ValueError):
    """The manifest is not valid. The message says what, never repeats the content."""


class ManifestTooNewError(ManifestError):
    """The manifest is of a newer format version than the one this app knows."""


@dataclass(frozen=True, slots=True)
class BackupLimits:
    """Caps for reading and writing a backup; all positive."""

    max_entries: int = 100_000  # files of the profile (the manifest is not counted)
    max_total_bytes: int = 20 * _GIB  # sum of the sizes of the files
    max_database_bytes: int = 8 * _GIB
    max_file_bytes: int = 50 * _MIB  # one activity file (the same cap as the import)
    max_manifest_bytes: int = 32 * _MIB
    max_ratio: int = 1000  # declared size / compressed size, per entry

    def __post_init__(self) -> None:
        for item in fields(self):
            if getattr(self, item.name) <= 0:
                raise ValueError(f"the limit {item.name} must be positive")


def is_archive_year(name: str) -> bool:
    """True for the name of a year folder of the archive: exactly four ASCII digits."""
    return _ARCHIVE_YEAR.fullmatch(name) is not None


def is_backup_entry_path(path: str) -> bool:
    """True for the database and for `archivio/<year>/<sha256>.fit|gpx`: the closed list of the
    entries of a backup (the manifest apart)."""
    return path == PROFILE_DATABASE_NAME or _ARCHIVE_ENTRY.fullmatch(path) is not None


@dataclass(frozen=True, slots=True)
class BackupFile:
    """An entry of the backup: its name in the ZIP (and in the profile folder), size, SHA-256."""

    path: str
    size_bytes: int
    sha256_hex: str

    def __post_init__(self) -> None:
        if not is_backup_entry_path(self.path):
            raise ValueError("entry name outside the closed list of a backup")
        if self.size_bytes < 0:
            raise ValueError("the size cannot be negative")
        if not _SHA256_HEX.fullmatch(self.sha256_hex):
            raise ValueError("the SHA-256 must be 64 lowercase hexadecimal digits")

    @property
    def is_database(self) -> bool:
        """True for the profile database."""
        return self.path == PROFILE_DATABASE_NAME


@dataclass(frozen=True, slots=True)
class BackupManifest:
    """What a backup declares about itself. Exactly one entry is the database, paths are unique."""

    format_version: int
    created_utc: datetime
    app_version: str
    profile_id: ProfileId
    schema_version: int
    files: tuple[BackupFile, ...]

    def __post_init__(self) -> None:
        if self.format_version < 1 or self.schema_version < 1:
            raise ValueError("versions start at 1")
        if self.created_utc.utcoffset() != UTC.utcoffset(None):
            raise ValueError("the creation instant must be UTC")
        if not 0 < len(self.app_version) <= _APP_VERSION_MAX_CHARS:
            raise ValueError("app version empty or too long")
        paths = [file.path for file in self.files]
        if len(set(paths)) != len(paths):
            raise ValueError("duplicate entry")
        if sum(file.is_database for file in self.files) != 1:
            raise ValueError("a backup has exactly one database")

    @property
    def database(self) -> BackupFile:
        """The database entry."""
        return next(file for file in self.files if file.is_database)

    @property
    def archive_files(self) -> tuple[BackupFile, ...]:
        """The activity files."""
        return tuple(file for file in self.files if not file.is_database)

    @property
    def total_bytes(self) -> int:
        """Sum of the sizes of the files."""
        return sum(file.size_bytes for file in self.files)


@dataclass(frozen=True, slots=True)
class BackupSummary:
    """The outcome of a backup or a restore."""

    profile_id: ProfileId
    created_utc: datetime
    schema_version: int
    archive_files: int
    total_bytes: int


def summarize(manifest: BackupManifest) -> BackupSummary:
    """The summary of a manifest."""
    return BackupSummary(
        manifest.profile_id,
        manifest.created_utc,
        manifest.schema_version,
        len(manifest.archive_files),
        manifest.total_bytes,
    )


def encode_manifest(manifest: BackupManifest) -> bytes:
    """The manifest as UTF-8 JSON (stable key order)."""
    document = {
        "format": BACKUP_FORMAT,
        "format_version": manifest.format_version,
        "created_utc": manifest.created_utc.isoformat().replace("+00:00", "Z"),
        "app_version": manifest.app_version,
        "profile_id": str(manifest.profile_id.value),
        "schema_version": manifest.schema_version,
        "files": [
            {"path": file.path, "size_bytes": file.size_bytes, "sha256": file.sha256_hex}
            for file in sorted(manifest.files, key=lambda item: item.path)
        ],
    }
    return json.dumps(document, indent=1, sort_keys=True).encode("utf-8")


def _require_int(value: object, what: str) -> int:
    if type(value) is not int:
        raise ManifestError(f"{what} is not an integer")
    return value


def _require_str(value: object, what: str) -> str:
    if not isinstance(value, str):
        raise ManifestError(f"{what} is not a text")
    return value


def _parse_file(item: object, limits: BackupLimits) -> BackupFile:
    if not isinstance(item, dict):
        raise ManifestError("a file entry is not an object")
    try:
        entry = BackupFile(
            _require_str(item.get("path"), "path"),
            _require_int(item.get("size_bytes"), "size_bytes"),
            _require_str(item.get("sha256"), "sha256"),
        )
    except ValueError as exc:
        if isinstance(exc, ManifestError):
            raise
        raise ManifestError("a file entry is not valid") from exc
    cap = limits.max_database_bytes if entry.is_database else limits.max_file_bytes
    if entry.size_bytes > cap:
        raise ManifestError("a file is beyond the size limit")
    return entry


def _parse_creation(text: str) -> datetime:
    try:
        created = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ManifestError("created_utc is not an instant") from exc
    if created.utcoffset() != UTC.utcoffset(None):
        raise ManifestError("created_utc is not in UTC")
    return created


def _load_document(data: bytes, limits: BackupLimits) -> dict[str, object]:
    """The JSON object of the manifest, size-checked first, with the required keys."""
    if len(data) > limits.max_manifest_bytes:
        raise ManifestError("the manifest is too large")
    try:
        document = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError) as exc:
        raise ManifestError("the manifest is not valid JSON") from exc
    if not isinstance(document, dict) or any(key not in document for key in _REQUIRED_KEYS):
        raise ManifestError("the manifest lacks required fields")
    if document["format"] != BACKUP_FORMAT:
        raise ManifestError("not a profile backup")
    return document


def _parse_files(raw_files: object, limits: BackupLimits) -> tuple[BackupFile, ...]:
    if not isinstance(raw_files, list) or len(raw_files) > limits.max_entries:
        raise ManifestError("the file list is missing or too long")
    files = tuple(_parse_file(item, limits) for item in raw_files)
    if sum(file.size_bytes for file in files) > limits.max_total_bytes:
        raise ManifestError("the files are beyond the total size limit")
    return files


def decode_manifest(data: bytes, limits: BackupLimits | None = None) -> BackupManifest:
    """Reads and validates a manifest; the size is checked before the JSON decoder runs.

    Unknown keys are ignored (tolerant reader); a newer `format_version` is refused.

    Raises:
        ManifestTooNewError: `format_version` is newer than `BACKUP_FORMAT_VERSION`.
        ManifestError: anything else wrong (size, syntax, missing or invalid field, limit).
    """
    effective = limits or BackupLimits()
    document = _load_document(data, effective)
    version = _require_int(document["format_version"], "format_version")
    if version > BACKUP_FORMAT_VERSION:
        raise ManifestTooNewError("backup of a newer format")
    files = _parse_files(document["files"], effective)
    try:
        return BackupManifest(
            version,
            _parse_creation(_require_str(document["created_utc"], "created_utc")),
            _require_str(document["app_version"], "app_version"),
            ProfileId.parse(_require_str(document["profile_id"], "profile_id")),
            _require_int(document["schema_version"], "schema_version"),
            files,
        )
    except ManifestError:
        raise
    except ValueError as exc:
        raise ManifestError("the manifest is not valid") from exc
