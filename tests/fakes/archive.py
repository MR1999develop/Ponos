# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""In-memory archive of originals (`ActivityArchive` port)."""

from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import PurePosixPath

from ponos_coach.activities.domain import MAX_ACTIVITY_FILE_BYTES, ArchivedFile, FileFormat
from ponos_coach.activities.ports import (
    ArchiveFileMissingError,
    ArchiveIntegrityError,
    ArchiveTooLargeError,
)


class InMemoryActivityArchive:
    """Files in a dictionary; orphan temporaries are simulated with `plant_orphan_temporary`."""

    def __init__(self) -> None:
        self.files: dict[PurePosixPath, bytes] = {}
        self.temporaries: set[PurePosixPath] = set()

    def store(self, content: bytes, file_format: FileFormat, start: datetime) -> ArchivedFile:
        """See `ActivityArchive.store`: path `<year>/<sha256>.<extension>`."""
        if len(content) > MAX_ACTIVITY_FILE_BYTES:
            raise ArchiveTooLargeError(len(content))
        digest = hashlib.sha256(content).hexdigest()
        path = PurePosixPath(str(start.year)) / f"{digest}.{file_format.value}"
        self.files[path] = content
        return ArchivedFile(path, digest, len(content))

    def read(self, file: ArchivedFile, max_bytes: int) -> bytes:
        """See `ActivityArchive.read`."""
        data = self._get(file)
        if len(data) > max_bytes:
            raise ArchiveTooLargeError(len(data))
        self._check(file, data)
        return data

    def verify(self, file: ArchivedFile) -> None:
        """See `ActivityArchive.verify`."""
        self._check(file, self._get(file))

    def list_orphan_temporaries(self) -> list[PurePosixPath]:
        """See `ActivityArchive.list_orphan_temporaries`."""
        return sorted(self.temporaries)

    def remove_orphan_temporaries(self) -> int:
        """See `ActivityArchive.remove_orphan_temporaries`."""
        removed = len(self.temporaries)
        self.temporaries.clear()
        return removed

    def plant_orphan_temporary(self) -> None:
        """Simulate an interrupted write (tests only)."""
        self.temporaries.add(PurePosixPath(f"2026/.tmp-{len(self.temporaries)}"))

    def _get(self, file: ArchivedFile) -> bytes:
        try:
            return self.files[file.relative_path]
        except KeyError:
            raise ArchiveFileMissingError(str(file.relative_path)) from None

    @staticmethod
    def _check(file: ArchivedFile, data: bytes) -> None:
        if len(data) != file.size_bytes or hashlib.sha256(data).hexdigest() != file.sha256_hex:
            raise ArchiveIntegrityError(str(file.relative_path))
