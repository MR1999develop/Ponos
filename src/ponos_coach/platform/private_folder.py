# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Cartelle private: leggibili e scrivibili solo dall'utente (e da SYSTEM su Windows).

Windows: descrittore esplicito e protetto applicato alla creazione (`windows_api`).
POSIX: modo 0700, verificato dopo la creazione (un file system che lo ignora, come certe
condivisioni di rete, è un errore e non un permesso più largo accettato in silenzio).
"""

from __future__ import annotations

import stat
import sys
from pathlib import Path

from ponos_coach.platform import windows_api
from ponos_coach.profiles.ports import FolderUnavailableError

_PRIVATE_MODE = 0o700
_IS_WINDOWS: bool = sys.platform == "win32"  # bool, non Literal: mypy non scarta un ramo


def _verify_mode(path: Path) -> None:
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode != _PRIVATE_MODE:
        raise FolderUnavailableError(
            f"{path.name}: il file system non applica i permessi privati ({mode:o})"
        )


def make_private_directory(path: Path) -> None:
    """Crea `path` (un solo livello, il genitore esiste) privato.

    Raises:
        FileExistsError: esiste già.
        FolderUnavailableError: permessi non applicabili.
        OSError: creazione non riuscita.
    """
    if _IS_WINDOWS:
        windows_api.create_private_directory(path)
        return
    path.mkdir(mode=_PRIVATE_MODE)
    path.chmod(_PRIVATE_MODE)  # la umask può aver tolto bit; mai aggiungerne
    _verify_mode(path)


def restrict_directory(path: Path) -> None:
    """Rende privata una cartella esistente (e vuota: sul contenuto non si interviene).

    Raises:
        FolderUnavailableError: permessi non applicabili.
        OSError: modifica non riuscita.
    """
    if _IS_WINDOWS:
        windows_api.restrict_to_current_user(path)
        return
    path.chmod(_PRIVATE_MODE)
    _verify_mode(path)
