# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Import sources: a list of chosen `.fit` files and a folder (PLAN.md 4.4).

Adapters of the `ImportSource` port. The examination (`scan`) looks only at names and sizes,
applies the `ImportLimits` caps and does not read the contents; `read` opens the file read-only,
checking the type on the handle.

Folders: only regular files that really lie inside the chosen folder; symbolic
links, junctions and reparse points, FIFOs and devices are skipped and reported; reserved
Windows names are skipped; for long paths the Windows prefix `\\\\?\\` is used. Only `.fit` files
are read: any other file is ignored (in a folder) or reported (when chosen by hand).
"""

from __future__ import annotations

import os
import stat
import unicodedata
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import BinaryIO

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
from ponos_coach.zipread import file_suffix, has_invalid_windows_chars, is_reserved_name

_REPARSE_POINT = 0x400  # FILE_ATTRIBUTE_REPARSE_POINT: links and junctions on Windows
_FIT_SUFFIX = ".fit"
_DISPLAY_CHARS = 255
# Safe opening: read-only, no links where the system can avoid them, non-blocking
# on POSIX, binary on Windows, not inherited by child processes.
_OPEN_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_NONBLOCK", 0)
    | getattr(os, "O_BINARY", 0)
    | getattr(os, "O_CLOEXEC", 0)
)

# --- Names --------------------------------------------------------------------------------------


def _display(text: str) -> str:
    """Displayable name: control and format characters (also bidi) become `?`."""
    clean = "".join("?" if unicodedata.category(c)[0] in "CZ" and c != " " else c for c in text)
    return clean[:_DISPLAY_CHARS]


def _is_unusable_folder_name(name: str) -> bool:
    """File or folder name to skip: reserved everywhere, not valid where it is Windows."""
    return is_reserved_name(name) or (os.name == "nt" and has_invalid_windows_chars(name))


# --- Files and folders ---------------------------------------------------------------------------


class _Kind(Enum):
    DIRECTORY = "directory"
    FILE = "file"
    LINK = "link"
    SPECIAL = "special"
    UNREADABLE = "unreadable"


def _not_a_file_issue(kind: _Kind) -> ImportIssue:
    """Why an entry that is not a regular file is not imported."""
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
    """Absolute and normalized path; on Windows with the long-path prefix."""
    absolute = os.path.abspath(path)  # noqa: PTH100 - normalization without resolving is needed
    if os.name != "nt" or absolute.startswith("\\\\?\\"):
        return Path(absolute)
    if absolute.startswith("\\\\"):
        return Path("\\\\?\\UNC\\" + absolute[2:])
    return Path("\\\\?\\" + absolute)


@dataclass(frozen=True, slots=True)
class _Location:
    """Where the file of an entry is: `root/parts`."""

    root: Path
    parts: tuple[str, ...]

    @property
    def path(self) -> Path:
        return self.root.joinpath(*self.parts)


def _check_plain_chain(location: _Location) -> None:
    """The folders from `root` to the file are not links or reparse points (recheck)."""
    for end in range(1, len(location.parts)):
        kind, _ = _classify(location.root.joinpath(*location.parts[:end]))
        if kind is not _Kind.DIRECTORY:
            raise ImportEntryError(ImportIssue.NOT_REGULAR_FILE)


def _open_regular(path: Path) -> BinaryIO:
    """Opens a regular, non-link file read-only; the type is rechecked
    on the open handle (a FIFO or a device is never read)."""
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


# --- Sources -----------------------------------------------------------------------------


def _never_cancelled() -> bool:
    return False


class _PathSource(ABC):
    """Common part: counters and caps, list and skipped, reading of files."""

    def __init__(self, limits: ImportLimits | None) -> None:
        self._limits = limits or ImportLimits()
        self._is_cancelled: Callable[[], bool] = _never_cancelled
        self._reset()

    def _reset(self) -> None:
        self._examined = 0
        self._activity_files = 0
        self._found: list[tuple[str, int, _Location]] = []
        self._skipped: list[SkippedEntry] = []
        self._locations: dict[int, _Location] = {}

    def close(self) -> None:  # noqa: B027 - the `ImportSource` port requires it
        """No resource to release (files are opened and closed at every read)."""

    # -- examination --

    def scan(self, is_cancelled: Callable[[], bool] | None = None) -> ImportScan:
        """See `ImportSource.scan`. It can be called again: it restarts from zero.

        Cancellation is checked at every entry examined, so even inside a huge
        folder; entries are counted as they are read, and the cap applies immediately.
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
        """Fills `_found` and `_skipped` respecting the caps."""

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
        """A regular `.fit` file with a usable name."""
        self._note_activity_file()
        if size > self._limits.max_file_bytes:
            self._skip(display, ImportIssue.TOO_LARGE)
        else:
            self._found.append((display, size, location))

    # -- reading --

    def read(self, entry: ImportEntry, max_bytes: int) -> bytes:
        """See `ImportSource.read`."""
        location = self._locations.get(entry.entry_id)
        if location is None:
            raise ImportEntryError(ImportIssue.UNREADABLE)
        return _read_file(location, max_bytes)


class FolderImportSource(_PathSource):
    """Folder with its subfolders: regular `.fit` files inside it.

    Adapter of `ImportSource`. Beyond the caps the examination stops with
    `ImportLimitExceededError`; files and entries that cannot be imported (too large,
    links, reserved names, paths too long) go in `skipped` with
    the reason. Usable as a context manager.
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
        """Entries of a folder in name order, counted as they are read: the cap on examined
        entries applies even to a single huge folder, which is not loaded in full."""
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
        elif kind is not _Kind.SPECIAL or file_suffix(entry.name) == _FIT_SUFFIX:
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
        if file_suffix(name) != _FIT_SUFFIX:
            return
        issue = self._name_issue(name, display)
        if issue is not None:
            self._skip(display, issue)
            return
        self._handle_candidate(_Location(root, parts), display, size)


class FilesImportSource(_PathSource):
    """List of files chosen by the user (`.fit`): no folders.

    A file with another extension, a folder, a link or a file that cannot be read
    goes in `skipped` with the reason.
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
        # The name is judged before touching the disk: on Windows the absolute path of a
        # reserved name (`CON.fit`) becomes the device, not the file.
        issue = self._name_issue(name, display)
        if issue is not None:
            self._skip(display, issue)
            return
        path = _absolute(chosen)
        kind, size = _classify(path)
        if kind is not _Kind.FILE:
            self._skip(display, _not_a_file_issue(kind))
        elif file_suffix(name) != _FIT_SUFFIX:
            self._skip(display, ImportIssue.UNSUPPORTED_FORMAT)
        else:
            self._handle_candidate(_Location(path.parent, (name,)), display, size)
