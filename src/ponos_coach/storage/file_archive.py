# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Archivio dei file originali sul file system (porta `ActivityArchive`).

Layout: `<radice>/<anno>/<sha256>.<fit|gpx>`, tutto generato dal programma. Scrittura: file
temporaneo `.tmp-<32 cifre esadecimali>` creato in modo esclusivo (0600) nella cartella
dell'anno, `fsync`, rinomina sul nome finale, `fsync` della cartella (dettagli e differenze
tra Windows e POSIX in `archive_files`). Un crash lascia al più un temporaneo orfano, mai un
file finale a metà. Nessun collegamento si segue: radice, cartella dell'anno e file si
controllano con `lstat` e, in lettura, con il confronto dell'identità del file aperto.
"""

from __future__ import annotations

import hashlib
import os
import re
import secrets
import stat
from datetime import UTC, datetime, timedelta
from io import BufferedReader
from pathlib import Path, PurePosixPath
from typing import Final

from ponos_coach.activities.domain import MAX_ACTIVITY_FILE_BYTES, ArchivedFile, FileFormat
from ponos_coach.activities.ports import (
    ArchiveError,
    ArchiveFileMissingError,
    ArchiveIntegrityError,
    ArchiveTooLargeError,
    ArchiveWriteError,
)
from ponos_coach.profiles.domain import PROFILE_ARCHIVE_DIR
from ponos_coach.storage.archive_files import OsWriteOps, WriteOps

_TEMP_PREFIX: Final = ".tmp-"
_TEMP_NAME: Final = re.compile(r"\.tmp-[0-9a-f]{32}")
_TEMP_CREATE_ATTEMPTS: Final = 4
_CHUNK_BYTES: Final = 1024 * 1024
_REPARSE_POINT: Final = 0x400  # FILE_ATTRIBUTE_REPARSE_POINT
_READ_FLAGS: Final = (
    os.O_RDONLY
    | getattr(os, "O_BINARY", 0)
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_CLOEXEC", 0)
    | getattr(os, "O_NONBLOCK", 0)
)


class _LinkFoundError(OSError):
    """Un componente del percorso è un collegamento o non è del tipo atteso."""


def _is_link(status: os.stat_result) -> bool:
    attributes = getattr(status, "st_file_attributes", 0)
    return stat.S_ISLNK(status.st_mode) or bool(attributes & _REPARSE_POINT)


def _reject_link(path: Path) -> None:
    """Il percorso deve essere una cartella vera (non un collegamento)."""
    status = path.lstat()
    if _is_link(status) or not stat.S_ISDIR(status.st_mode):
        raise _LinkFoundError("componente dell'archivio non regolare")


def _reject_link_at_destination(path: Path) -> None:
    """Se il nome di destinazione esiste, non deve essere un collegamento né un reparse point."""
    try:
        status = path.lstat()
    except FileNotFoundError:
        return
    if _is_link(status):
        raise _LinkFoundError("la destinazione è un collegamento")


def _same_file(first: os.stat_result, second: os.stat_result) -> bool:
    return (first.st_ino, first.st_dev) == (second.st_ino, second.st_dev)


def _require_same_regular_file(before: os.stat_result, after: os.stat_result) -> None:
    if not stat.S_ISREG(after.st_mode) or not _same_file(before, after):
        raise _LinkFoundError("il file è cambiato durante l'apertura")


class FileArchive:
    """Archivio sul file system, con le scritture dietro `WriteOps` (iniettabile nei test)."""

    def __init__(self, root: Path, ops: WriteOps | None = None) -> None:
        self._root = root
        self._ops: WriteOps = OsWriteOps() if ops is None else ops

    @classmethod
    def in_profile_folder(cls, folder: Path) -> FileArchive:
        """Archivio di un profilo: la cartella `archivio` dentro la sua cartella."""
        return cls(folder / PROFILE_ARCHIVE_DIR)

    # --- scrittura -------------------------------------------------------------------------

    def store(self, content: bytes, file_format: FileFormat, start: datetime) -> ArchivedFile:
        """Salva i byte in modo durevole e idempotente; vedi `ActivityArchive.store`.

        Un file già presente con lo stesso nome e il contenuto giusto non si riscrive; uno con
        lo stesso nome ma byte alterati si sostituisce in modo atomico con quelli giusti.

        Raises:
            ArchiveTooLargeError: più di `MAX_ACTIVITY_FILE_BYTES` byte.
            ArchiveWriteError: scrittura non riuscita (anche un collegamento nel percorso, o un
                errore d'I/O nel leggere il file già presente).
            ValueError: `start` non è un istante UTC con fuso.
        """
        if len(content) > MAX_ACTIVITY_FILE_BYTES:
            raise ArchiveTooLargeError("contenuto oltre il massimo ammesso")
        if start.utcoffset() != timedelta(0):
            raise ValueError("l'inizio deve essere un istante UTC con fuso")
        digest = hashlib.sha256(content).hexdigest()
        year = start.astimezone(UTC).year
        relative = PurePosixPath(f"{year:04d}") / f"{digest}.{file_format.value}"
        archived = ArchivedFile(relative, digest, len(content))
        try:
            if not self._already_stored(archived):
                self._write(self._root / relative.parent, self._root / relative, content)
        except (OSError, ArchiveError) as exc:  # anche l'I/O sul file già presente (verifica)
            raise ArchiveWriteError("scrittura nell'archivio non riuscita") from exc
        return archived

    def _already_stored(self, archived: ArchivedFile) -> bool:
        try:
            self._verify_stream(archived)
        except (ArchiveFileMissingError, ArchiveIntegrityError):
            return False
        return True

    def _write(self, directory: Path, final: Path, content: bytes) -> None:
        created = [d for d in (self._root, directory) if self._ops.make_dir(d)]
        _reject_link(self._root)
        _reject_link(directory)
        _reject_link_at_destination(final)
        temporary, descriptor = self._create_temporary(directory)
        try:
            self._fill(descriptor, content)
            self._ops.replace(temporary, final)
        except OSError:
            self._discard(temporary)
            raise
        self._ops.sync_dir(directory)
        for new_directory in reversed(created):
            self._ops.sync_dir(new_directory.parent)

    def _create_temporary(self, directory: Path) -> tuple[Path, int]:
        for _ in range(_TEMP_CREATE_ATTEMPTS - 1):
            path = directory / (_TEMP_PREFIX + secrets.token_hex(16))
            try:
                return path, self._ops.create_exclusive(path)
            except FileExistsError:
                continue
        path = directory / (_TEMP_PREFIX + secrets.token_hex(16))
        return path, self._ops.create_exclusive(path)

    def _fill(self, descriptor: int, content: bytes) -> None:
        """Scrive tutto (anche scritture parziali), `fsync`, chiude."""
        try:
            view = memoryview(content)
            while view:
                view = view[self._ops.write(descriptor, view) :]
            self._ops.fsync(descriptor)
        finally:
            self._ops.close(descriptor)

    def _discard(self, temporary: Path) -> None:
        try:
            self._ops.unlink(temporary)
        except OSError:
            return  # resta un orfano: lo toglie `remove_orphan_temporaries` al prossimo avvio

    # --- lettura ---------------------------------------------------------------------------

    def read(self, file: ArchivedFile, max_bytes: int) -> bytes:
        """Contenuto del file dopo aver verificato dimensione e SHA-256.

        Raises:
            ArchiveFileMissingError: il file non c'è.
            ArchiveTooLargeError: il file supera `max_bytes`; non si legge oltre.
            ArchiveIntegrityError: dimensione, SHA-256 o tipo del file non corrispondono.
            ArchiveError: lettura non riuscita (I/O).
        """
        with self._open(file) as stream:
            if os.fstat(stream.fileno()).st_size > max_bytes:
                raise ArchiveTooLargeError("il file supera il massimo richiesto")
            data = stream.read(max_bytes + 1)
        self._check(file, len(data), hashlib.sha256(data).hexdigest())
        return data

    def verify(self, file: ArchivedFile) -> None:
        """Controlla dimensione e SHA-256 a blocchi, senza tenere il file in memoria."""
        self._verify_stream(file)

    def _verify_stream(self, file: ArchivedFile) -> None:
        digest = hashlib.sha256()
        total = 0
        with self._open(file) as stream:
            while block := stream.read(_CHUNK_BYTES):
                total += len(block)
                if total > file.size_bytes:
                    raise ArchiveIntegrityError("il file è più grande del registrato")
                digest.update(block)
        self._check(file, total, digest.hexdigest())

    @staticmethod
    def _check(file: ArchivedFile, size: int, sha256_hex: str) -> None:
        if size != file.size_bytes or sha256_hex != file.sha256_hex:
            raise ArchiveIntegrityError("dimensione o SHA-256 diversi da quelli registrati")

    def _open(self, file: ArchivedFile) -> BufferedReader:
        """Apre in lettura un file regolare, senza seguire collegamenti."""
        try:
            return os.fdopen(self._open_descriptor(file.relative_path), "rb")
        except (FileNotFoundError, NotADirectoryError) as exc:
            raise ArchiveFileMissingError("file assente dall'archivio") from exc
        except _LinkFoundError as exc:
            raise ArchiveIntegrityError("il percorso contiene un collegamento") from exc
        except OSError as exc:
            raise ArchiveError("lettura dall'archivio non riuscita") from exc

    def _open_descriptor(self, relative: PurePosixPath) -> int:
        directory = self._root
        _reject_link(directory)
        for part in relative.parts[:-1]:
            directory = directory / part
            _reject_link(directory)
        path = directory / relative.parts[-1]
        before = path.lstat()
        if _is_link(before) or not stat.S_ISREG(before.st_mode):
            raise _LinkFoundError("il file non è regolare")
        descriptor = os.open(path, _READ_FLAGS)
        try:
            _require_same_regular_file(before, os.fstat(descriptor))
        except BaseException:
            os.close(descriptor)
            raise
        return descriptor

    # --- temporanei orfani -----------------------------------------------------------------

    def list_orphan_temporaries(self) -> list[PurePosixPath]:
        """Temporanei di scritture interrotte, relativi alla radice, ordinati."""
        found: list[PurePosixPath] = []
        try:
            with os.scandir(self._root) as years:
                for year in years:
                    if year.is_dir(follow_symlinks=False):
                        found.extend(self._temporaries_in(year))
        except FileNotFoundError:
            return []
        except OSError as exc:
            raise ArchiveError("elenco dell'archivio non riuscito") from exc
        return sorted(found)

    @staticmethod
    def _temporaries_in(year: os.DirEntry[str]) -> list[PurePosixPath]:
        with os.scandir(year.path) as entries:
            return [
                PurePosixPath(year.name) / entry.name
                for entry in entries
                if _TEMP_NAME.fullmatch(entry.name) and entry.is_file(follow_symlinks=False)
            ]

    def remove_orphan_temporaries(self) -> int:
        """Cancella i temporanei orfani e dice quanti; i file archiviati non si toccano."""
        removed = 0
        for relative in self.list_orphan_temporaries():
            try:
                self._ops.unlink(self._root / relative)
            except OSError as exc:
                raise ArchiveWriteError("cancellazione di un temporaneo non riuscita") from exc
            removed += 1
        return removed
