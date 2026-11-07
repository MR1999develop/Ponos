# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Caps for importing files, folders and ZIP archives (PLAN.md 2.11, 4.4, 4.9): one place.

The default values are the project's; tests and adapters may pass smaller
ones. The interface reads them from here for its messages.
"""

from __future__ import annotations

from dataclasses import dataclass, fields

from ponos_coach.activities.domain import MAX_ACTIVITY_FILE_BYTES

_MIB = 1024 * 1024
_GIB = 1024 * _MIB


@dataclass(frozen=True, slots=True)
class ImportLimits:
    """Caps for examination and extraction; all positive.

    The first three (`max_activity_files`, `max_examined_entries`, `max_depth`) stop the examination
    of a source before importing anything. `max_file_bytes` skips the single file
    (or the single entry of a ZIP). The `max_zip_*` caps reject the archive or the entry
    before extracting.
    """

    max_activity_files: int = 50_000  # .fit and .gpx files, including ZIP entries
    max_examined_entries: int = 500_000  # files and folders of every kind, ZIP entries included
    max_depth: int = 32  # levels of subfolders
    max_file_bytes: int = MAX_ACTIVITY_FILE_BYTES
    max_path_chars: int = 2048  # relative path shown: beyond this, the file is reported and skipped
    max_zip_entries: int = 50_000  # entries of a single ZIP
    max_zip_total_bytes: int = 5 * _GIB  # sum of the declared sizes of a ZIP
    max_zip_ratio: int = 100  # declared size / compressed size, per entry
    max_zip_directory_bytes: int = 32 * _MIB  # central directory of the ZIP, read into memory

    def __post_init__(self) -> None:
        for item in fields(self):
            if getattr(self, item.name) <= 0:
                raise ValueError(f"the limit {item.name} must be positive")
