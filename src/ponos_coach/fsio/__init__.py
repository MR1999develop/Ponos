# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Low-level primitives for durable file writes, with no dependencies on the project.

Used by both `storage` (archive, backups) and `platform` (profile list), which
stay independent of each other: the durable-write rule lives in this one place.
"""

from ponos_coach.fsio.durable import (
    make_subdirectory,
    replace_file,
    sync_directory,
    write_file_atomically,
)

__all__ = ["make_subdirectory", "replace_file", "sync_directory", "write_file_atomically"]
