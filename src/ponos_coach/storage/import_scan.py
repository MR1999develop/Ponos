# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Sorgenti dell'import: un elenco di file scelti e una cartella, con gli ZIP (PLAN.md 4.4, 4.9).

Adattatori della porta `ImportSource`. L'esame (`scan`) guarda solo nomi, dimensioni e
directory degli ZIP, applica i tetti di `ImportLimits` e non legge i contenuti; `read` apre il
file in sola lettura, controllando il tipo sull'handle, o estrae la voce dello ZIP in memoria a
blocchi.

Cartelle: solo file regolari che stanno davvero dentro la cartella scelta; collegamenti
simbolici, giunzioni e reparse point, FIFO e dispositivi si saltano e si segnalano; i nomi
riservati di Windows si saltano; sui percorsi lunghi si usa il prefisso `\\\\?\\` di Windows.

ZIP: si leggono solo le voci `.fit` e `.gpx`. Voci e dimensioni dichiarate si controllano prima di
estrarre (directory centrale letta con un tetto, numero di voci, somma delle dimensioni,
rapporto di compressione per voce, dimensione per voce); la dimensione reale si ricontrolla
durante l'estrazione e il CRC si verifica fino all'ultimo byte. I nomi delle voci non sono mai
percorsi (si mostrano ripuliti e non si usano per scrivere): una voce con `..`, percorso
assoluto, unità, controlli o nome riservato si rifiuta. Voci cifrate e ZIP annidati si rifiutano.
"""

from __future__ import annotations

import os
import stat
import unicodedata
import zipfile
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from types import TracebackType
from typing import BinaryIO, Self

from ponos_coach.activities.import_limits import ImportLimits
from ponos_coach.activities.ports import (
    ImportEntry,
    ImportEntryError,
    ImportIssue,
    ImportLimit,
    ImportLimitExceededError,
    ImportScan,
    ImportScanCancelledError,
    ImportSourceUnreadableError,
    SkippedEntry,
)
from ponos_coach.zipread import (
    ACTIVITY_SUFFIXES,
    ZIP_SUFFIX,
    ZipLimits,
    ZipReadError,
    ZipReason,
    entry_issue,
    extract_entry,
    file_suffix,
    has_invalid_windows_chars,
    is_reserved_name,
    open_checked_zip,
)

_REPARSE_POINT = 0x400  # FILE_ATTRIBUTE_REPARSE_POINT: collegamenti e giunzioni su Windows
_CANDIDATE_SUFFIXES = (*ACTIVITY_SUFFIXES, ZIP_SUFFIX)
_DISPLAY_CHARS = 255
# Apertura sicura: sola lettura, niente collegamenti dove il sistema lo sa evitare, non bloccante
# su POSIX, binaria su Windows, non ereditata dai processi figli.
_OPEN_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_NONBLOCK", 0)
    | getattr(os, "O_BINARY", 0)
    | getattr(os, "O_CLOEXEC", 0)
)

# --- Nomi --------------------------------------------------------------------------------------


def _display(text: str) -> str:
    """Nome mostrabile: caratteri di controllo e di formato (anche bidi) diventano `?`."""
    clean = "".join("?" if unicodedata.category(c)[0] in "CZ" and c != " " else c for c in text)
    return clean[:_DISPLAY_CHARS]


def _is_unusable_folder_name(name: str) -> bool:
    """Nome di file o cartella da saltare: riservato ovunque, non valido dove è Windows."""
    return is_reserved_name(name) or (os.name == "nt" and has_invalid_windows_chars(name))


# --- File e cartelle ---------------------------------------------------------------------------


class _Kind(Enum):
    DIRECTORY = "directory"
    FILE = "file"
    LINK = "link"
    SPECIAL = "special"
    UNREADABLE = "unreadable"


def _not_a_file_issue(kind: _Kind) -> ImportIssue:
    """Perché una voce che non è un file regolare non si importa."""
    return ImportIssue.UNREADABLE if kind is _Kind.UNREADABLE else ImportIssue.NOT_REGULAR_FILE


def _classify_status(status: os.stat_result) -> _Kind:
    attributes = int(getattr(status, "st_file_attributes", 0))
    if stat.S_ISLNK(status.st_mode) or attributes & _REPARSE_POINT:
        return _Kind.LINK
    if stat.S_ISDIR(status.st_mode):
        return _Kind.DIRECTORY
    return _Kind.FILE if stat.S_ISREG(status.st_mode) else _Kind.SPECIAL


def _classify(path: Path) -> tuple[_Kind, int]:
    try:
        status = os.lstat(path)
    except OSError:
        return _Kind.UNREADABLE, 0
    return _classify_status(status), status.st_size


def _classify_entry(entry: os.DirEntry[str]) -> tuple[_Kind, int]:
    try:
        status = entry.stat(follow_symlinks=False)
    except OSError:
        return _Kind.UNREADABLE, 0
    return _classify_status(status), status.st_size


def _absolute(path: Path) -> Path:
    """Percorso assoluto e normalizzato; su Windows con il prefisso dei percorsi lunghi."""
    absolute = os.path.abspath(path)  # noqa: PTH100 - serve la normalizzazione senza risolvere
    if os.name != "nt" or absolute.startswith("\\\\?\\"):
        return Path(absolute)
    if absolute.startswith("\\\\"):
        return Path("\\\\?\\UNC\\" + absolute[2:])
    return Path("\\\\?\\" + absolute)


@dataclass(frozen=True, slots=True)
class _Location:
    """Dove sta il file di una voce: `root/parts`, e per una voce di ZIP il suo posto nell'ZIP."""

    root: Path
    parts: tuple[str, ...]
    zip_index: int | None = None
    zip_entry_name: str | None = None
    size_bytes: int = 0

    @property
    def path(self) -> Path:
        return self.root.joinpath(*self.parts)


def _check_plain_chain(location: _Location) -> None:
    """Le cartelle da `root` al file non sono collegamenti né reparse point (ricontrollo)."""
    for end in range(1, len(location.parts)):
        kind, _ = _classify(location.root.joinpath(*location.parts[:end]))
        if kind is not _Kind.DIRECTORY:
            raise ImportEntryError(ImportIssue.NOT_REGULAR_FILE)


def _open_regular(path: Path) -> BinaryIO:
    """Apre un file regolare, non collegamento, in sola lettura; il tipo si ricontrolla
    sull'handle aperto (un FIFO o un dispositivo non si leggono mai)."""
    kind, _ = _classify(path)
    if kind is not _Kind.FILE:
        raise ImportEntryError(_not_a_file_issue(kind))
    try:
        descriptor = os.open(path, _OPEN_FLAGS)
    except OSError as exc:
        raise ImportEntryError(ImportIssue.UNREADABLE) from exc
    try:
        regular = stat.S_ISREG(os.fstat(descriptor).st_mode)
    except OSError as exc:
        os.close(descriptor)
        raise ImportEntryError(ImportIssue.UNREADABLE) from exc
    if not regular:
        os.close(descriptor)
        raise ImportEntryError(ImportIssue.NOT_REGULAR_FILE)
    return os.fdopen(descriptor, "rb")


def _read_file(location: _Location, max_bytes: int) -> bytes:
    _check_plain_chain(location)
    try:
        with _open_regular(location.path) as handle:
            if os.fstat(handle.fileno()).st_size > max_bytes:
                raise ImportEntryError(ImportIssue.TOO_LARGE)
            data = handle.read(max_bytes + 1)
    except OSError as exc:
        raise ImportEntryError(ImportIssue.UNREADABLE) from exc
    if len(data) > max_bytes:
        raise ImportEntryError(ImportIssue.TOO_LARGE)
    return data


# --- ZIP ---------------------------------------------------------------------------------------


def _zip_limits(limits: ImportLimits) -> ZipLimits:
    """I tetti dell'import espressi per `zipread`."""
    return ZipLimits(
        max_entries=limits.max_zip_entries,
        max_total_bytes=limits.max_zip_total_bytes,
        max_ratio=limits.max_zip_ratio,
        max_directory_bytes=limits.max_zip_directory_bytes,
        max_entry_bytes=limits.max_file_bytes,
    )


_ISSUE_BY_REASON = {
    ZipReason.ARCHIVE_INVALID: ImportIssue.ARCHIVE_INVALID,
    ZipReason.ARCHIVE_LIMIT: ImportIssue.ARCHIVE_LIMIT,
    ZipReason.ENCRYPTED: ImportIssue.ENCRYPTED,
    ZipReason.UNSAFE_NAME: ImportIssue.UNSAFE_NAME,
    ZipReason.TOO_LARGE: ImportIssue.TOO_LARGE,
    ZipReason.NESTED_ARCHIVE: ImportIssue.NESTED_ARCHIVE,
}


def _issue_of(reason: ZipReason) -> ImportIssue:
    """Il motivo di `zipread` come motivo dell'import (quelli di una voce singola sono mappati;
    gli altri non nascono qui e valgono archivio non valido)."""
    return _ISSUE_BY_REASON.get(reason, ImportIssue.ARCHIVE_INVALID)


def _open_checked_zip(handle: BinaryIO, limits: ImportLimits) -> zipfile.ZipFile:
    """ZIP con directory controllata (vedi `zipread.open_checked_zip`), errori come import."""
    try:
        return open_checked_zip(handle, _zip_limits(limits))
    except ZipReadError as error:
        raise ImportEntryError(_issue_of(error.reason)) from error


def _entry_issue(info: zipfile.ZipInfo, limits: ImportLimits) -> ImportIssue | None:
    """Perché una voce `.fit` o `.gpx` non si estrae (None se va bene), da dati dichiarati."""
    reason = entry_issue(info, _zip_limits(limits))
    return None if reason is None else _issue_of(reason)


def _extract(archive: zipfile.ZipFile, info: zipfile.ZipInfo) -> bytes:
    """Estrae la voce in memoria a blocchi (vedi `zipread.extract_entry`)."""
    try:
        return extract_entry(archive, info)
    except ZipReadError as error:
        raise ImportEntryError(_issue_of(error.reason)) from error


@dataclass(slots=True)
class _OpenZip:
    path: Path
    handle: BinaryIO
    archive: zipfile.ZipFile

    def close(self) -> None:
        self.archive.close()
        self.handle.close()


# --- Sorgenti ----------------------------------------------------------------------------------


def _never_cancelled() -> bool:
    return False


class _PathSource(ABC):
    """Parte comune: contatori e tetti, elenco e saltati, lettura di file e voci di ZIP."""

    def __init__(self, limits: ImportLimits | None) -> None:
        self._limits = limits or ImportLimits()
        self._cached_zip: _OpenZip | None = None
        self._is_cancelled: Callable[[], bool] = _never_cancelled
        self._reset()

    def _reset(self) -> None:
        self.close()
        self._examined = 0
        self._activity_files = 0
        self._found: list[tuple[str, int, _Location]] = []
        self._skipped: list[SkippedEntry] = []
        self._locations: dict[int, _Location] = {}

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        """Chiude lo ZIP aperto per le letture, se c'è."""
        opened, self._cached_zip = self._cached_zip, None
        if opened is not None:
            opened.close()

    # -- esame --

    def scan(self, is_cancelled: Callable[[], bool] | None = None) -> ImportScan:
        """Vedi `ImportSource.scan`. Si può richiamare: riparte da zero.

        L'annullamento si controlla a ogni voce esaminata, quindi anche dentro una cartella
        enorme; le voci si contano mentre si leggono, e il tetto vale subito.
        """
        self._is_cancelled = is_cancelled or _never_cancelled
        self._reset()
        self._discover()
        found = sorted(self._found, key=lambda item: item[0])
        entries = []
        for entry_id, (name, size, location) in enumerate(found):
            self._locations[entry_id] = location
            entries.append(ImportEntry(entry_id, name, size))
        skipped = sorted(self._skipped, key=lambda item: item.name)
        return ImportScan(tuple(entries), tuple(skipped), self._examined)

    @abstractmethod
    def _discover(self) -> None:
        """Riempie `_found` e `_skipped` rispettando i tetti."""

    def _examine(self, count: int = 1) -> None:
        if self._is_cancelled():
            raise ImportScanCancelledError
        self._examined += count
        if self._examined > self._limits.max_examined_entries:
            raise ImportLimitExceededError(
                ImportLimit.EXAMINED_ENTRIES, self._limits.max_examined_entries
            )

    def _note_activity_file(self) -> None:
        self._activity_files += 1
        if self._activity_files > self._limits.max_activity_files:
            raise ImportLimitExceededError(
                ImportLimit.ACTIVITY_FILES, self._limits.max_activity_files
            )

    def _skip(self, name: str, issue: ImportIssue) -> None:
        self._skipped.append(SkippedEntry(name, issue))

    def _name_issue(self, name: str, display: str) -> ImportIssue | None:
        if _is_unusable_folder_name(name):
            return ImportIssue.RESERVED_NAME
        if len(display) > self._limits.max_path_chars:
            return ImportIssue.PATH_TOO_LONG
        return None

    def _handle_candidate(self, location: _Location, display: str, size: int) -> None:
        """Un file regolare con suffisso `.fit`, `.gpx` o `.zip` e nome utilizzabile."""
        if file_suffix(location.parts[-1]) == ZIP_SUFFIX:
            self._handle_zip(location, display)
            return
        self._note_activity_file()
        if size > self._limits.max_file_bytes:
            self._skip(display, ImportIssue.TOO_LARGE)
        else:
            self._found.append((display, size, location))

    def _handle_zip(self, location: _Location, display: str) -> None:
        try:
            _check_plain_chain(location)
            with (
                _open_regular(location.path) as handle,
                _open_checked_zip(handle, self._limits) as zf,
            ):
                infos = zf.infolist()
                self._examine(len(infos))
                for index, info in enumerate(infos):
                    self._handle_zip_entry(location, display, index, info)
        except ImportEntryError as error:
            self._skip(display, error.issue)

    def _handle_zip_entry(
        self, zip_location: _Location, zip_display: str, index: int, info: zipfile.ZipInfo
    ) -> None:
        suffix = file_suffix(info.filename)
        if info.is_dir() or suffix not in (*ACTIVITY_SUFFIXES, ZIP_SUFFIX):
            return
        display = f"{zip_display}!{_display(info.filename)}"
        if suffix == ZIP_SUFFIX:
            self._skip(display, ImportIssue.NESTED_ARCHIVE)
            return
        self._note_activity_file()
        issue = _entry_issue(info, self._limits)
        if issue is not None:
            self._skip(display, issue)
            return
        location = _Location(
            zip_location.root, zip_location.parts, index, info.filename, info.file_size
        )
        self._found.append((display, info.file_size, location))

    # -- lettura --

    def read(self, entry: ImportEntry, max_bytes: int) -> bytes:
        """Vedi `ImportSource.read`."""
        location = self._locations.get(entry.entry_id)
        if location is None:
            raise ImportEntryError(ImportIssue.UNREADABLE)
        if location.zip_index is None:
            return _read_file(location, max_bytes)
        return self._read_zip_entry(location, max_bytes)

    def _zip_for(self, location: _Location) -> zipfile.ZipFile:
        opened = self._cached_zip
        if opened is not None and opened.path == location.path:
            return opened.archive
        self.close()
        _check_plain_chain(location)
        handle = _open_regular(location.path)
        try:
            archive = _open_checked_zip(handle, self._limits)
        except BaseException:
            handle.close()
            raise
        self._cached_zip = _OpenZip(location.path, handle, archive)
        return archive

    def _read_zip_entry(self, location: _Location, max_bytes: int) -> bytes:
        archive = self._zip_for(location)
        infos = archive.infolist()
        index = location.zip_index
        if index is None or index >= len(infos):
            raise ImportEntryError(ImportIssue.ARCHIVE_INVALID)
        info = infos[index]
        if info.filename != location.zip_entry_name or info.file_size != location.size_bytes:
            raise ImportEntryError(ImportIssue.ARCHIVE_INVALID)  # lo ZIP è cambiato dopo l'esame
        issue = _entry_issue(info, self._limits)
        if issue is not None:
            raise ImportEntryError(issue)
        if info.file_size > max_bytes:
            raise ImportEntryError(ImportIssue.TOO_LARGE)
        return _extract(archive, info)


class FolderImportSource(_PathSource):
    """Cartella con le sue sottocartelle: file `.fit`, `.gpx` e `.zip` regolari al suo interno.

    Adattatore di `ImportSource`. Oltre i tetti l'esame si ferma con
    `ImportLimitExceededError`; i file e le voci che non si possono importare (troppo grandi,
    collegamenti, nomi riservati, percorsi troppo lunghi, ZIP rifiutati) vanno in `skipped` con
    il motivo. Usabile come gestore di contesto.
    """

    def __init__(self, folder: Path, limits: ImportLimits | None = None) -> None:
        super().__init__(limits)
        self._folder = folder

    def _discover(self) -> None:
        root = _absolute(self._folder)
        if not root.is_dir():
            raise ImportSourceUnreadableError(self._folder.name)
        pending: list[tuple[str, ...]] = [()]
        while pending:
            self._scan_directory(root, pending.pop(), pending)

    def _scan_directory(
        self, root: Path, parts: tuple[str, ...], pending: list[tuple[str, ...]]
    ) -> None:
        try:
            with os.scandir(root.joinpath(*parts)) as listing:
                entries = self._read_listing(listing)
        except OSError as exc:
            if not parts:
                raise ImportSourceUnreadableError(self._folder.name) from exc
            self._skip(_display("/".join(parts)), ImportIssue.UNREADABLE)
            return
        for entry in entries:
            self._visit(root, parts, entry, pending)

    def _read_listing(self, listing: Iterator[os.DirEntry[str]]) -> list[os.DirEntry[str]]:
        """Voci di una cartella in ordine di nome, contate mentre si leggono: il tetto delle voci
        esaminate vale anche per una sola cartella enorme, che non si carica per intero."""
        entries: list[os.DirEntry[str]] = []
        for entry in listing:
            self._examine()
            entries.append(entry)
        entries.sort(key=lambda item: item.name)
        return entries

    def _visit(
        self,
        root: Path,
        parts: tuple[str, ...],
        entry: os.DirEntry[str],
        pending: list[tuple[str, ...]],
    ) -> None:
        display = _display("/".join((*parts, entry.name)))
        kind, size = _classify_entry(entry)
        if kind is _Kind.DIRECTORY:
            self._visit_directory(parts, entry.name, display, pending)
        elif kind is _Kind.FILE:
            self._visit_file(root, (*parts, entry.name), display, size)
        elif kind is not _Kind.SPECIAL or file_suffix(entry.name) in _CANDIDATE_SUFFIXES:
            self._skip(display, _not_a_file_issue(kind))

    def _visit_directory(
        self, parts: tuple[str, ...], name: str, display: str, pending: list[tuple[str, ...]]
    ) -> None:
        if len(parts) + 1 > self._limits.max_depth:
            raise ImportLimitExceededError(ImportLimit.DEPTH, self._limits.max_depth)
        issue = self._name_issue(name, display)
        if issue is not None:
            self._skip(display, issue)
        else:
            pending.append((*parts, name))

    def _visit_file(self, root: Path, parts: tuple[str, ...], display: str, size: int) -> None:
        name = parts[-1]
        if file_suffix(name) not in _CANDIDATE_SUFFIXES:
            return
        issue = self._name_issue(name, display)
        if issue is not None:
            self._skip(display, issue)
            return
        self._handle_candidate(_Location(root, parts), display, size)


class FilesImportSource(_PathSource):
    """Elenco di file scelti dall'utente (`.fit`, `.gpx`, `.zip`): niente cartelle.

    Un file con altra estensione, una cartella, un collegamento o un file che non si legge
    vanno in `skipped` con il motivo.
    """

    def __init__(self, files: Sequence[Path], limits: ImportLimits | None = None) -> None:
        super().__init__(limits)
        self._files = tuple(files)

    def _discover(self) -> None:
        for chosen in self._files:
            self._examine()
            self._visit(chosen)

    def _visit(self, chosen: Path) -> None:
        name, display = chosen.name, _display(chosen.name)
        # Il nome si giudica prima di toccare il disco: su Windows il percorso assoluto di un
        # nome riservato (`CON.fit`) diventa il dispositivo, non il file.
        issue = self._name_issue(name, display)
        if issue is not None:
            self._skip(display, issue)
            return
        path = _absolute(chosen)
        kind, size = _classify(path)
        if kind is not _Kind.FILE:
            self._skip(display, _not_a_file_issue(kind))
        elif file_suffix(name) not in _CANDIDATE_SUFFIXES:
            self._skip(display, ImportIssue.UNSUPPORTED_FORMAT)
        else:
            self._handle_candidate(_Location(path.parent, (name,)), display, size)
