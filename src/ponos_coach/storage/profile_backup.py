# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""`ProfileBackupStorage` on the file system: a profile in a `.ponosbackup` ZIP file.

Writing: the database is copied with the SQLite backup API (a consistent snapshot even while the
app has the profile open), checked with `quick_check`, and added to the ZIP with the activity
files; the manifest (sizes and SHA-256 of every file) is the last entry; the ZIP is built in a
private temporary folder next to the destination and renamed only when complete.

Reading (untrusted input): the central directory, entry count, sizes and compression ratios are
checked before anything is extracted (`zipread`); the entries must be exactly those of the
manifest, each from the closed list of names of `profiles.domain.backup`, so no name from the
file is ever used as a path; every file is streamed to an exclusive temporary, checked against
its SHA-256 and renamed; the database goes last and is then opened as the app will open it.
Anything that fails removes what the call wrote.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import secrets
import shutil
import sqlite3
import stat
import tempfile
import zipfile
import zlib
from collections.abc import Iterator
from contextlib import closing
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Final

from ponos_coach.fsio import make_subdirectory, replace_file, sync_directory
from ponos_coach.profiles.domain import (
    PROFILE_ARCHIVE_DIR,
    PROFILE_BACKUP_DIR,
    PROFILE_DATABASE_NAME,
    PROFILE_LOCK_NAME,
)
from ponos_coach.profiles.domain.backup import (
    BACKUP_FORMAT_VERSION,
    MANIFEST_NAME,
    BackupFile,
    BackupLimits,
    BackupManifest,
    BackupSummary,
    ManifestError,
    ManifestTooNewError,
    decode_manifest,
    encode_manifest,
    is_backup_entry_path,
    summarize,
)
from ponos_coach.profiles.ports import (
    BackupFileExistsError,
    BackupInvalidError,
    BackupProblem,
    BackupTooNewError,
    BackupWriteError,
    FolderNotEmptyError,
    ProfileCorruptError,
    ProfileFormatTooNewError,
    ProfileNotFoundError,
    ProfileStorageError,
)
from ponos_coach.storage.sqlite_database import BUSY_TIMEOUT_MS, open_database
from ponos_coach.storage.sqlite_profile import SqliteProfileStorage
from ponos_coach.zipread import (
    ZipLimits,
    ZipReadError,
    ZipReason,
    entry_issue,
    open_checked_zip,
)

_BLOCK_BYTES: Final = 256 * 1024
_ZIP_EPOCH: Final = (1980, 1, 1, 0, 0, 0)  # no local time in the file
_ZIP_ZIP64_FROM: Final = 1 << 30  # the database may exceed what plain ZIP stores
_FILE_MODE: Final = 0o600
_REPARSE_POINT: Final = 0x400  # FILE_ATTRIBUTE_REPARSE_POINT
_YEAR_DIGITS: Final = 4
_OPEN_FLAGS: Final = (
    os.O_RDONLY
    | getattr(os, "O_BINARY", 0)
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_CLOEXEC", 0)
)
_CREATE_FLAGS: Final = (
    os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
)
_ENTRY_READ_ERRORS: Final = (
    zipfile.BadZipFile,
    zipfile.LargeZipFile,
    EOFError,
    zlib.error,
    ValueError,
    NotImplementedError,
    RuntimeError,
    OverflowError,
)
_REASON_TO_PROBLEM: Final = {
    ZipReason.ARCHIVE_INVALID: BackupProblem.NOT_A_BACKUP,
    ZipReason.ARCHIVE_LIMIT: BackupProblem.LIMIT,
    ZipReason.ENCRYPTED: BackupProblem.UNSAFE_ENTRY,
    ZipReason.UNSAFE_NAME: BackupProblem.UNSAFE_ENTRY,
    ZipReason.TOO_LARGE: BackupProblem.LIMIT,
    ZipReason.NESTED_ARCHIVE: BackupProblem.UNSAFE_ENTRY,
    ZipReason.NO_ACTIVITY: BackupProblem.NOT_A_BACKUP,
    ZipReason.MULTIPLE_ACTIVITIES: BackupProblem.NOT_A_BACKUP,
}


def _is_plain(status: os.stat_result, *, directory: bool = False) -> bool:
    """A regular file (or a real folder): not a link, junction or other reparse point."""
    kind_ok = stat.S_ISDIR(status.st_mode) if directory else stat.S_ISREG(status.st_mode)
    return kind_ok and not getattr(status, "st_file_attributes", 0) & _REPARSE_POINT


def _open_plain_file(path: Path) -> BinaryIO:
    """Opens a regular file for reading without following a link.

    Raises:
        OSError: missing, not a regular file, a link, or not readable.
    """
    descriptor = os.open(path, _OPEN_FLAGS)
    handle = os.fdopen(descriptor, "rb")
    if not _is_plain(os.fstat(handle.fileno())):
        handle.close()
        raise OSError("not a regular file")
    return handle


# --- Writing ---------------------------------------------------------------------------------


def _snapshot_database(folder: Path, work: Path) -> Path:
    """Consistent copy of the profile database into `work`, integrity-checked.

    Raises:
        BackupWriteError: the database is missing, busy or not intact.
    """
    source = folder / PROFILE_DATABASE_NAME
    snapshot = work / PROFILE_DATABASE_NAME
    try:
        if not _is_plain(source.lstat()):
            raise BackupWriteError("the profile database is not a regular file")
        uri = source.resolve().as_uri() + "?mode=rw"
        with (
            closing(sqlite3.connect(uri, uri=True, timeout=BUSY_TIMEOUT_MS / 1000)) as origin,
            closing(sqlite3.connect(str(snapshot))) as target,
        ):
            origin.execute("PRAGMA query_only = ON")
            origin.backup(target)
            if target.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                raise BackupWriteError("the profile database is not intact")
    except (OSError, sqlite3.Error) as exc:
        raise BackupWriteError("the profile database cannot be copied") from exc
    return snapshot


def _plain_entries(folder: Path, *, directory: bool) -> Iterator[os.DirEntry[str]]:
    """The entries of `folder` that are real files (or real folders), sorted by name."""
    with os.scandir(folder) as scan:
        entries = sorted(scan, key=lambda entry: entry.name)
    for entry in entries:
        status = entry.stat(follow_symlinks=False)
        if _is_plain(status, directory=directory):
            yield entry


def _archive_sources(folder: Path) -> list[tuple[str, Path]]:
    """(entry name, path) of the archive files: `archivio/<year>/<sha256>.fit|gpx`.

    Anything else in the archive (temporaries, links, strange names) is not part of a backup.
    """
    root = folder / PROFILE_ARCHIVE_DIR
    if not root.exists():
        return []
    found: list[tuple[str, Path]] = []
    for year in _plain_entries(root, directory=True):
        if len(year.name) != _YEAR_DIGITS or not year.name.isdecimal():
            continue
        for item in _plain_entries(Path(year.path), directory=False):
            name = f"{PROFILE_ARCHIVE_DIR}/{year.name}/{item.name}"
            if is_backup_entry_path(name):
                found.append((name, Path(item.path)))
    return found


def _add_file(
    archive: zipfile.ZipFile, name: str, source: Path, max_bytes: int, *, large: bool = False
) -> BackupFile:
    """Streams a file into the ZIP computing its SHA-256 in the same pass."""
    info = zipfile.ZipInfo(name, date_time=_ZIP_EPOCH)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = _FILE_MODE << 16
    digest = hashlib.sha256()
    size = 0
    with _open_plain_file(source) as stream, archive.open(info, "w", force_zip64=large) as sink:
        while chunk := stream.read(_BLOCK_BYTES):
            size += len(chunk)
            if size > max_bytes:
                raise BackupWriteError("a file is beyond the size limit")
            digest.update(chunk)
            sink.write(chunk)
    return BackupFile(name, size, digest.hexdigest())


def _fsync_file(path: Path) -> None:
    with path.open("r+b") as handle:  # fsync needs write access on Windows
        os.fsync(handle.fileno())


class FileProfileBackup:
    """Backup and restore of a profile folder as a `.ponosbackup` file."""

    def __init__(self, limits: BackupLimits | None = None) -> None:
        self._limits = limits or BackupLimits()
        self._zip_limits = ZipLimits(
            max_entries=self._limits.max_entries + 1,
            max_total_bytes=self._limits.max_total_bytes + self._limits.max_manifest_bytes,
            max_ratio=self._limits.max_ratio,
            max_entry_bytes=max(self._limits.max_database_bytes, self._limits.max_manifest_bytes),
        )

    # --- write -------------------------------------------------------------------------------

    def write(
        self, folder: Path, destination: Path, created_utc: datetime, app_version: str
    ) -> BackupSummary:
        """See `ProfileBackupStorage.write`."""
        if os.path.lexists(destination):
            raise BackupFileExistsError("the backup file already exists")
        try:
            work = Path(tempfile.mkdtemp(dir=destination.parent, prefix=".ponos-backup-"))
        except OSError as exc:
            raise BackupWriteError("the backup folder cannot be used") from exc
        try:
            manifest = self._write_in(folder, work, created_utc, app_version)
            self._publish(work / "backup.zip", destination)
        except OSError as exc:
            raise BackupWriteError("writing the backup failed") from exc
        finally:
            shutil.rmtree(work, ignore_errors=True)
        return summarize(manifest)

    def _write_in(
        self, folder: Path, work: Path, created_utc: datetime, app_version: str
    ) -> BackupManifest:
        snapshot = _snapshot_database(folder, work)
        try:
            peeked = SqliteProfileStorage().peek(work).info
        except ProfileStorageError as exc:
            raise BackupWriteError("the profile database is not readable") from exc
        sources = _archive_sources(folder)
        if len(sources) > self._limits.max_entries:
            raise BackupWriteError("too many files in the archive")
        zip_path = work / "backup.zip"
        descriptor = os.open(zip_path, _CREATE_FLAGS, _FILE_MODE)
        with os.fdopen(descriptor, "wb") as handle, zipfile.ZipFile(handle, "w") as archive:
            files = [
                _add_file(archive, name, path, self._limits.max_file_bytes)
                for name, path in sources
            ]
            size = snapshot.stat().st_size
            files.append(
                _add_file(
                    archive,
                    PROFILE_DATABASE_NAME,
                    snapshot,
                    self._limits.max_database_bytes,
                    large=size > _ZIP_ZIP64_FROM,
                )
            )
            manifest = BackupManifest(
                BACKUP_FORMAT_VERSION,
                created_utc,
                app_version,
                peeked.profile_id,
                peeked.format_version,
                tuple(files),
            )
            if manifest.total_bytes > self._limits.max_total_bytes:
                raise BackupWriteError("the profile is beyond the total size limit")
            archive.writestr(
                zipfile.ZipInfo(MANIFEST_NAME, date_time=_ZIP_EPOCH),
                encode_manifest(manifest),
                compress_type=zipfile.ZIP_DEFLATED,
            )
        _fsync_file(zip_path)
        return manifest

    @staticmethod
    def _publish(finished: Path, destination: Path) -> None:
        """The complete file takes its name; if someone created it meanwhile it is not replaced."""
        if os.path.lexists(destination):
            raise BackupFileExistsError("the backup file already exists")
        replace_file(finished, destination)
        sync_directory(destination.parent)

    # --- restore -----------------------------------------------------------------------------

    def restore(self, source: Path, folder: Path) -> BackupSummary:
        """See `ProfileBackupStorage.restore`."""
        _require_only_lock(folder)
        created: list[Path] = []
        try:
            return summarize(self._restore_into(source, folder, created))
        except BaseException:
            _roll_back(folder, created)
            raise

    def _restore_into(self, source: Path, folder: Path, created: list[Path]) -> BackupManifest:
        try:
            handle = _open_plain_file(source)
        except OSError as exc:
            raise BackupWriteError("the backup file cannot be read") from exc
        with handle:
            if os.fstat(handle.fileno()).st_size > self._zip_limits.max_total_bytes:
                raise BackupInvalidError(BackupProblem.LIMIT)
            try:
                archive = open_checked_zip(handle, self._zip_limits)
            except ZipReadError as exc:
                raise BackupInvalidError(_REASON_TO_PROBLEM[exc.reason]) from exc
            with archive:
                manifest = self._read_manifest(archive)
                self._check_entries(archive, manifest)
                for file in (*manifest.archive_files, manifest.database):  # database last
                    _extract(archive, file, folder, created)
        _verify_database(folder, manifest)
        return manifest

    def _read_manifest(self, archive: zipfile.ZipFile) -> BackupManifest:
        try:
            info = archive.getinfo(MANIFEST_NAME)
        except KeyError as exc:
            raise BackupInvalidError(BackupProblem.NOT_A_BACKUP) from exc
        _require_allowed(info, self._zip_limits)
        if info.file_size > self._limits.max_manifest_bytes:
            raise BackupInvalidError(BackupProblem.LIMIT)
        data = b"".join(_read_entry(archive, info))
        try:
            return decode_manifest(data, self._limits)
        except ManifestTooNewError as exc:
            raise BackupTooNewError("backup of a newer format") from exc
        except ManifestError as exc:
            raise BackupInvalidError(BackupProblem.NOT_A_BACKUP) from exc

    def _check_entries(self, archive: zipfile.ZipFile, manifest: BackupManifest) -> None:
        """The ZIP holds exactly the manifest and the files it declares, with the declared sizes."""
        declared = {file.path: file for file in manifest.files}
        infos = [info for info in archive.infolist() if info.filename != MANIFEST_NAME]
        if {info.filename for info in infos} != set(declared) or len(infos) != len(declared):
            raise BackupInvalidError(BackupProblem.UNSAFE_ENTRY)
        for info in infos:
            _require_allowed(info, self._zip_limits)
            if info.file_size != declared[info.filename].size_bytes:
                raise BackupInvalidError(BackupProblem.CHECKSUM)


def _require_allowed(info: zipfile.ZipInfo, limits: ZipLimits) -> None:
    issue = entry_issue(info, limits)
    if issue is not None:
        raise BackupInvalidError(_REASON_TO_PROBLEM[issue])


def _read_entry(archive: zipfile.ZipFile, info: zipfile.ZipInfo) -> Iterator[bytes]:
    """The blocks of an entry; no more bytes than declared; CRC checked at the end.

    Raises:
        BackupInvalidError: corrupt or truncated entry, or more bytes than declared.
        BackupWriteError: the backup file cannot be read.
    """
    total = 0
    try:
        with archive.open(info) as stream:
            while chunk := stream.read(_BLOCK_BYTES):
                total += len(chunk)
                if total > info.file_size:
                    raise BackupInvalidError(BackupProblem.CHECKSUM)
                yield chunk
    except _ENTRY_READ_ERRORS as exc:
        raise BackupInvalidError(BackupProblem.NOT_A_BACKUP) from exc
    except OSError as exc:
        raise BackupWriteError("the backup file cannot be read") from exc
    if total != info.file_size:
        raise BackupInvalidError(BackupProblem.NOT_A_BACKUP)


def _make_parents(target: Path, folder: Path, created: list[Path]) -> None:
    """Creates the missing folders between `folder` and the parent of `target`."""
    for part in reversed(target.relative_to(folder).parents):
        if part == Path():
            continue
        if make_subdirectory(folder / part):
            created.append(folder / part)


def _extract(archive: zipfile.ZipFile, file: BackupFile, folder: Path, created: list[Path]) -> None:
    """One file of the backup into the profile folder, verified, durable, under its final name."""
    info = archive.getinfo(file.path)
    target = folder / PurePosixPath(file.path)  # the name is from the closed list
    try:
        _make_parents(target, folder, created)
        if os.path.lexists(target):
            raise BackupWriteError("the destination already has a file")
        temporary = target.parent / f".{secrets.token_hex(8)}.tmp"
        created.append(temporary)
        digest = hashlib.sha256()
        with os.fdopen(os.open(temporary, _CREATE_FLAGS, _FILE_MODE), "wb") as sink:
            for chunk in _read_entry(archive, info):
                digest.update(chunk)
                sink.write(chunk)
            sink.flush()
            os.fsync(sink.fileno())
        if digest.hexdigest() != file.sha256_hex:
            raise BackupInvalidError(BackupProblem.CHECKSUM)
        replace_file(temporary, target)
        created.append(target)
        sync_directory(target.parent)
    except OSError as exc:
        raise BackupWriteError("writing the restored files failed") from exc


def _verify_database(folder: Path, manifest: BackupManifest) -> None:
    """The restored database is the one the manifest describes and opens like the app opens it."""
    try:
        info = SqliteProfileStorage().peek(folder).info
        if info.profile_id != manifest.profile_id or info.format_version != manifest.schema_version:
            raise BackupInvalidError(BackupProblem.DATABASE_INVALID)
        open_database(folder / PROFILE_DATABASE_NAME, check_integrity=True).close()
    except ProfileFormatTooNewError as exc:
        raise BackupTooNewError("database newer than this app knows") from exc
    except (ProfileCorruptError, ProfileNotFoundError) as exc:
        raise BackupInvalidError(BackupProblem.DATABASE_INVALID) from exc
    except (ProfileStorageError, sqlite3.Error) as exc:
        raise BackupWriteError("the restored database cannot be opened") from exc


def _require_only_lock(folder: Path) -> None:
    try:
        with os.scandir(folder) as scan:
            names = [entry.name for entry in scan]
    except OSError as exc:
        raise BackupWriteError("the destination folder cannot be read") from exc
    if any(name != PROFILE_LOCK_NAME for name in names):
        raise FolderNotEmptyError(folder.name)


def _roll_back(folder: Path, created: list[Path]) -> None:
    """Removes what the restore wrote (and the pre-migration copies the check may have made)."""
    for side in ("", "-wal", "-shm", "-journal"):
        with contextlib.suppress(OSError):
            (folder / (PROFILE_DATABASE_NAME + side)).unlink(missing_ok=True)
    for path in reversed(created):
        with contextlib.suppress(OSError):
            if path.is_dir():
                path.rmdir()
            else:
                path.unlink(missing_ok=True)
    shutil.rmtree(folder / PROFILE_BACKUP_DIR, ignore_errors=True)
