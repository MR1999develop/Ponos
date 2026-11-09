# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Caps for importing files and folders (PLAN.md 2.11, 4.4): one place.

The default values are the project's; tests and adapters may pass smaller
ones. The interface reads them from here for its messages.
"""

from __future__ import annotations

from dataclasses import dataclass, fields

from ponos_coach.activities.domain import MAX_ACTIVITY_FILE_BYTES


@dataclass(frozen=True, slots=True)
class ImportLimits:
    """Caps for examination and extraction; all positive.

    The first three (`max_activity_files`, `max_examined_entries`, `max_depth`) stop the examination
    of a source before importing anything. `max_file_bytes` skips the single file.
    """

    max_activity_files: int = 50_000  # .fit files
    max_examined_entries: int = 500_000  # files and folders of every kind
    max_depth: int = 32  # levels of subfolders
    max_file_bytes: int = MAX_ACTIVITY_FILE_BYTES
    max_path_chars: int = 2048  # relative path shown: beyond this, the file is reported and skipped

    def __post_init__(self) -> None:
        for item in fields(self):
            if getattr(self, item.name) <= 0:
                raise ValueError(f"the limit {item.name} must be positive")
