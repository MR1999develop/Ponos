# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""File and archive-entry names: Windows reserved, invalid characters, safe names.

A name that comes from outside (ZIP entry, file in a folder) is never a path on
disk: these functions only say whether it is acceptable as text to display and compare.
"""

from __future__ import annotations

import re
from typing import Final

_ENTRY_NAME_CHARS: Final = 1024
_DRIVE: Final = re.compile(r"[A-Za-z]:")
_INVALID_NAME_CHARS: Final = frozenset('<>:"|?*')
_SEPARATORS: Final = re.compile(r"[\\/]")
_RESERVED_NAMES: Final = frozenset(
    {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"}
    | {f"{device}{digit}" for device in ("COM", "LPT") for digit in "123456789¹²³"}
)


def file_suffix(name: str) -> str:
    """Lowercase suffix with the dot (empty if missing) of the last component of the name."""
    last = _SEPARATORS.split(name)[-1]
    _, dot, tail = last.rpartition(".")
    return f".{tail.lower()}" if dot else ""


def is_reserved_name(name: str) -> bool:
    """Windows reserved name (`CON`, `NUL`, `COM1`...) with any extension."""
    return name.split(".", 1)[0].rstrip(" ").upper() in _RESERVED_NAMES


def has_invalid_windows_chars(name: str) -> bool:
    """Control characters or `<>:"|?*`, or trailing dot or space."""
    return name != name.rstrip(" .") or any(c in _INVALID_NAME_CHARS or ord(c) < 32 for c in name)


def is_safe_entry_name(name: str) -> bool:
    """Name of an entry without `..`, absolute path, drive, control characters or Windows names."""
    if not name or len(name) > _ENTRY_NAME_CHARS or name[0] in "/\\":
        return False
    if _DRIVE.match(name) or any(ord(c) < 32 or ord(c) == 127 for c in name):
        return False
    parts = [part for part in _SEPARATORS.split(name) if part]
    return ".." not in parts and not any(
        is_reserved_name(part) or has_invalid_windows_chars(part) for part in parts
    )
