# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Nomi di file e di voci di archivio: riservati di Windows, caratteri non validi, nomi sicuri.

Un nome che viene da fuori (voce di uno ZIP, file di una cartella) non è mai un percorso su
disco: queste funzioni dicono solo se è accettabile come testo da mostrare e da confrontare.
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
    """Suffisso minuscolo con il punto (vuoto se manca) dell'ultimo componente del nome."""
    last = _SEPARATORS.split(name)[-1]
    _, dot, tail = last.rpartition(".")
    return f".{tail.lower()}" if dot else ""


def is_reserved_name(name: str) -> bool:
    """Nome riservato di Windows (`CON`, `NUL`, `COM1`...) con qualunque estensione."""
    return name.split(".", 1)[0].rstrip(" ").upper() in _RESERVED_NAMES


def has_invalid_windows_chars(name: str) -> bool:
    """Caratteri di controllo o `<>:"|?*`, oppure punto o spazio finale."""
    return name != name.rstrip(" .") or any(c in _INVALID_NAME_CHARS or ord(c) < 32 for c in name)


def is_safe_entry_name(name: str) -> bool:
    """Nome di una voce senza `..`, percorso assoluto, unità, controlli né nomi di Windows."""
    if not name or len(name) > _ENTRY_NAME_CHARS or name[0] in "/\\":
        return False
    if _DRIVE.match(name) or any(ord(c) < 32 or ord(c) == 127 for c in name):
        return False
    parts = [part for part in _SEPARATORS.split(name) if part]
    return ".." not in parts and not any(
        is_reserved_name(part) or has_invalid_windows_chars(part) for part in parts
    )
