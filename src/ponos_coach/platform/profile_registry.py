# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""List of profiles in a JSON file in the app's configuration folder.

Format (version 1):

    {"format_version": 1,
     "profiles": [{"id": "<uuid4>", "folder": "<absolute path>", "name": "<name>",
                   "last_used_utc": "<ISO 8601 with UTC time zone>"}]}

Fields this version does not know (in the root or in an entry) are preserved on
resave. Every write (read-modify-write) happens under an operating-system lock
on `profiles.json.lock`, next to the file, with a bounded wait: so two instances
of the app do not lose each other's changes. An unreadable, too large, corrupt or future-version
file is an explicit error and is never overwritten.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ponos_coach.platform.atomic_file import FileTooLargeError, read_limited, write_atomic
from ponos_coach.platform.file_lock import acquire_file_lock
from ponos_coach.profiles.domain import ProfileId, ProfileName, RegistryEntry
from ponos_coach.profiles.ports import (
    FolderUnavailableError,
    ProfileAlreadyRegisteredError,
    ProfileInUseError,
    ProfileNotRegisteredError,
    ProfileRegistryError,
)

REGISTRY_FILE_NAME = "profiles.json"
REGISTRY_FORMAT_VERSION = 1
REGISTRY_MAX_BYTES = 1024 * 1024
REGISTRY_LOCK_NAME = REGISTRY_FILE_NAME + ".lock"
REGISTRY_LOCK_TIMEOUT_S = 2.0  # maximum wait for the lock of another instance
_LOCK_RETRY_INTERVAL_S = 0.05
_ORPHAN_TEMPORARY = re.compile(re.escape(REGISTRY_FILE_NAME) + r"\.[A-Za-z0-9_]+\.tmp", re.ASCII)

type _Raw = dict[str, Any]


class RegistryCorruptError(ProfileRegistryError):
    """The list file is unreadable, too large or not of the expected shape."""


class RegistryFormatTooNewError(ProfileRegistryError):
    """The list file is of a newer version than the one this app knows."""


class RegistryBusyError(ProfileRegistryError):
    """Another instance holds the list lock beyond the maximum wait: nothing was written."""


def _corrupt(reason: str) -> RegistryCorruptError:
    return RegistryCorruptError(f"invalid profile list: {reason}")


def _same_folder_key(folder: Path) -> str:
    return os.path.normcase(os.fspath(folder))


def _entry_from_raw(raw: object) -> RegistryEntry:
    """Domain entry from a JSON object; every defect is `RegistryCorruptError`."""
    if not isinstance(raw, dict):
        raise _corrupt("entry is not an object")
    try:
        identifier, folder, name, used = (
            raw["id"],
            raw["folder"],
            raw["name"],
            raw["last_used_utc"],
        )
        if not all(isinstance(v, str) for v in (identifier, folder, name, used)):
            raise _corrupt("a field of an entry is not text")
        return RegistryEntry(
            ProfileId.parse(identifier),
            Path(folder),
            ProfileName(name),
            datetime.fromisoformat(used),
        )
    except KeyError as exc:
        raise _corrupt("entry without a required field") from exc
    except ValueError as exc:  # include InvalidProfileNameError
        raise _corrupt("entry with an invalid value") from exc


def _raw_from_entry(entry: RegistryEntry) -> _Raw:
    return {
        "id": str(entry.profile_id),
        "folder": os.fspath(entry.folder),
        "name": entry.cached_name.value,
        "last_used_utc": entry.last_used_utc.astimezone(UTC).isoformat(),
    }


class _Document:
    """Content of the file: raw root (unknown fields included) and entries."""

    def __init__(self, root: _Raw) -> None:
        self.root = root
        profiles = root["profiles"]
        self.raw_entries: list[_Raw] = profiles
        self.entries = [_entry_from_raw(raw) for raw in profiles]
        self._check_unique()

    def _check_unique(self) -> None:
        ids = {e.profile_id for e in self.entries}
        folders = {_same_folder_key(e.folder) for e in self.entries}
        if len(ids) != len(self.entries) or len(folders) != len(self.entries):
            raise _corrupt("entries with the same id or the same folder")

    def index_of(self, profile_id: ProfileId) -> int:
        for index, entry in enumerate(self.entries):
            if entry.profile_id == profile_id:
                return index
        raise ProfileNotRegisteredError(str(profile_id))


def _parse_document(data: bytes | None) -> _Document:
    if data is None:
        return _Document({"format_version": REGISTRY_FORMAT_VERSION, "profiles": []})
    try:
        root = json.loads(data)
    except (ValueError, RecursionError) as exc:  # UnicodeDecodeError is a ValueError
        raise _corrupt("not JSON") from exc
    if not isinstance(root, dict):
        raise _corrupt("the root is not an object")
    version = root.get("format_version")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise _corrupt("format version missing or invalid")
    if version > REGISTRY_FORMAT_VERSION:
        raise RegistryFormatTooNewError(
            f"profile list of a newer version ({version}): update the app"
        )
    if not isinstance(root.get("profiles"), list):
        raise _corrupt("profile list missing")
    return _Document(root)


class JsonProfileRegistry:
    """`ProfileRegistry` on `<config_dir>/profiles.json`.

    Every operation rereads the file and, if anything changes, rewrites it atomically: no
    in-memory copy that could diverge. The `config_dir` folder is created on the first write.
    Writes first take the operating-system lock on `profiles.json.lock`, waiting
    at most `lock_timeout_s` seconds (then `RegistryBusyError`); reading does not take it, because
    the file is replaced atomically. The lock file stays on disk: the lock belongs to the
    system, and frees itself even if the instance dies. On opening, temporaries left by
    an interrupted write are removed (under lock, so that the one of another
    instance that is writing is not touched); if the lock is busy it is deferred to the next
    opening.
    """

    def __init__(self, config_dir: Path, lock_timeout_s: float = REGISTRY_LOCK_TIMEOUT_S) -> None:
        if lock_timeout_s <= 0:
            raise ValueError("the lock wait must be positive")
        self._path = config_dir / REGISTRY_FILE_NAME
        self._lock_path = config_dir / REGISTRY_LOCK_NAME
        self._lock_timeout_s = lock_timeout_s
        self._remove_orphan_temporaries()

    def list_entries(self) -> Sequence[RegistryEntry]:
        """See `ProfileRegistry.list_entries`."""
        entries = self._load().entries
        return sorted(entries, key=lambda e: (-e.last_used_utc.timestamp(), str(e.profile_id)))

    def add(self, entry: RegistryEntry) -> None:
        """See `ProfileRegistry.add`."""
        with self._exclusive():
            document = self._load()
            folder_key = _same_folder_key(entry.folder)
            for existing in document.entries:
                if existing.profile_id == entry.profile_id or (
                    _same_folder_key(existing.folder) == folder_key
                ):
                    raise ProfileAlreadyRegisteredError(str(entry.profile_id))
            document.raw_entries.append(_raw_from_entry(entry))
            self._save(document)

    def upsert(self, entry: RegistryEntry) -> None:
        """See `ProfileRegistry.upsert`.

        The entry with the same id is updated in place, so its unknown fields remain.
        """
        with self._exclusive():
            document = self._load()
            folder_key = _same_folder_key(entry.folder)
            kept = [
                raw
                for raw, existing in zip(document.raw_entries, document.entries, strict=True)
                if existing.profile_id == entry.profile_id
                or _same_folder_key(existing.folder) != folder_key
            ]
            current = _raw_from_entry(entry)
            for raw in kept:
                if raw["id"] == current["id"]:
                    raw.update(current)
                    break
            else:
                kept.append(current)
            document.raw_entries[:] = kept
            self._save(document)

    def remove(self, profile_id: ProfileId) -> None:
        """See `ProfileRegistry.remove`."""
        with self._exclusive():
            document = self._load()
            del document.raw_entries[document.index_of(profile_id)]
            self._save(document)

    def mark_used(self, profile_id: ProfileId, when_utc: datetime) -> None:
        """See `ProfileRegistry.mark_used`."""
        with self._exclusive():
            document = self._load()
            index = document.index_of(profile_id)
            updated = RegistryEntry(
                profile_id,
                document.entries[index].folder,
                document.entries[index].cached_name,
                when_utc,
            )
            # Only the field changes: the others, even unknown ones, stay as they were.
            document.raw_entries[index]["last_used_utc"] = _raw_from_entry(updated)["last_used_utc"]
            self._save(document)

    def _remove_orphan_temporaries(self) -> None:
        """Removes the leftover `profiles.json.*.tmp`; a failure here does not prevent using the
        list."""
        try:
            with os.scandir(self._path.parent) as listing:
                orphans = [
                    entry.name
                    for entry in listing
                    if _ORPHAN_TEMPORARY.fullmatch(entry.name)
                    and entry.is_file(follow_symlinks=False)
                ]
        except OSError:
            return
        if not orphans:
            return
        try:
            with self._exclusive():
                for name in orphans:
                    with contextlib.suppress(OSError):
                        (self._path.parent / name).unlink()
        except ProfileRegistryError:
            return  # lock busy or folder not writable: it will retry on the next opening

    @contextmanager
    def _exclusive(self) -> Iterator[None]:
        """Holds the system lock on the file next to the list, with a bounded wait."""
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise ProfileRegistryError("profile list not writable") from exc
        deadline = time.monotonic() + self._lock_timeout_s
        while True:
            try:
                lock = acquire_file_lock(self._lock_path)
                break
            except ProfileInUseError as exc:
                if time.monotonic() >= deadline:
                    raise RegistryBusyError(
                        "profile list in use by another instance: try again"
                    ) from exc
                time.sleep(_LOCK_RETRY_INTERVAL_S)
            except FolderUnavailableError as exc:
                raise ProfileRegistryError("profile list lock cannot be obtained") from exc
        try:
            yield
        finally:
            lock.release()

    def _load(self) -> _Document:
        try:
            data = read_limited(self._path, REGISTRY_MAX_BYTES)
        except FileTooLargeError as exc:
            raise _corrupt("file too large") from exc
        except OSError as exc:
            raise ProfileRegistryError("profile list not readable") from exc
        return _parse_document(data)

    def _save(self, document: _Document) -> None:
        text = json.dumps(document.root, ensure_ascii=False, indent=2, sort_keys=False)
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            write_atomic(self._path, (text + "\n").encode("utf-8"))
        except OSError as exc:
            raise ProfileRegistryError("profile list not writable") from exc
