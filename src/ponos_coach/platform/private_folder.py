# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Private folders: readable and writable only by the user (and by SYSTEM on Windows).

Windows: explicit protected descriptor applied at creation (`windows_api`).
POSIX: mode 0700, verified after creation (a file system that ignores it, like some
network shares, is an error and not a wider permission silently accepted).
"""

from __future__ import annotations

import stat
import sys
from pathlib import Path

from ponos_coach.platform import windows_api
from ponos_coach.profiles.ports import FolderUnavailableError

_PRIVATE_MODE = 0o700
_IS_WINDOWS: bool = sys.platform == "win32"  # bool, not Literal: mypy does not drop a branch


def _verify_mode(path: Path) -> None:
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode != _PRIVATE_MODE:
        raise FolderUnavailableError(
            f"{path.name}: the file system does not apply private permissions ({mode:o})"
        )


def make_private_directory(path: Path) -> None:
    """Creates `path` (a single level, the parent exists) as private.

    Raises:
        FileExistsError: it already exists.
        FolderUnavailableError: permissions not applicable.
        OSError: creation failed.
    """
    if _IS_WINDOWS:
        windows_api.create_private_directory(path)
        return
    path.mkdir(mode=_PRIVATE_MODE)
    path.chmod(_PRIVATE_MODE)  # the umask may have removed bits; never add any
    _verify_mode(path)


def restrict_directory(path: Path) -> None:
    """Makes an existing folder private (and empty: its content is not touched).

    Raises:
        FolderUnavailableError: permissions not applicable.
        OSError: change failed.
    """
    if _IS_WINDOWS:
        windows_api.restrict_to_current_user(path)
        return
    path.chmod(_PRIVATE_MODE)
    _verify_mode(path)
