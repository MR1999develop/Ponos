# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Visita e cancellazione di alberi di cartelle senza mai seguire link né giunzioni.

Un link simbolico o una giunzione (junction) dentro la cartella di un profilo potrebbe portare la
cancellazione fuori da essa: si rifiuta prima di toccare qualcosa. Gli altri reparse point di
Windows (segnaposto di OneDrive, deduplicazione) sono file veri e si cancellano normalmente.
"""

from __future__ import annotations

import os
import stat
from collections.abc import Collection, Iterator
from pathlib import Path

_IO_REPARSE_TAG_MOUNT_POINT = 0xA0000003
_IO_REPARSE_TAG_SYMLINK = 0xA000000C
_LINK_REPARSE_TAGS = frozenset({_IO_REPARSE_TAG_MOUNT_POINT, _IO_REPARSE_TAG_SYMLINK})


class LinkInTreeError(OSError):
    """Nell'albero c'è un link simbolico o una giunzione."""


def is_link(path: Path) -> bool:
    """Vero se `path` è un link simbolico o una giunzione (senza seguirlo)."""
    status = os.lstat(path)
    if stat.S_ISLNK(status.st_mode) or path.is_junction():
        return True
    return getattr(status, "st_reparse_tag", 0) in _LINK_REPARSE_TAGS


def _is_directory(path: Path) -> bool:
    return stat.S_ISDIR(os.lstat(path).st_mode)


def _children(folder: Path) -> Iterator[Path]:
    with os.scandir(folder) as entries:
        names = sorted(entry.name for entry in entries)
    for name in names:
        yield folder / name


def check_no_links(root: Path) -> None:
    """Verifica che né `root` né nulla al suo interno sia un link.

    Raises:
        LinkInTreeError: trovato un link (il nome è nel messaggio, non il percorso intero).
        OSError: albero non leggibile.
    """
    pending = [root]
    while pending:
        current = pending.pop()
        if is_link(current):
            raise LinkInTreeError(f"link o giunzione: {current.name}")
        if _is_directory(current):
            pending.extend(_children(current))


def _unlink_file(path: Path) -> None:
    try:
        path.unlink()
    except PermissionError:
        path.chmod(stat.S_IWRITE)  # file di sola lettura su Windows
        path.unlink()


def _remove_path(path: Path) -> None:
    """Cancella file o cartella (con contenuto); rifiuta i link, anche comparsi dopo i controlli."""
    if is_link(path):
        raise LinkInTreeError(f"link o giunzione: {path.name}")
    if _is_directory(path):
        _remove_directory(path)
    else:
        _unlink_file(path)


def _remove_directory(path: Path) -> None:
    directories = [path]
    pending = [path]
    while pending:
        for child in _children(pending.pop()):
            if is_link(child):
                raise LinkInTreeError(f"link o giunzione: {child.name}")
            if _is_directory(child):
                directories.append(child)
                pending.append(child)
            else:
                _unlink_file(child)
    for directory in reversed(directories):  # i figli prima dei genitori
        directory.rmdir()


def remove_children(folder: Path, *, keep: Collection[str], last: Collection[str]) -> None:
    """Cancella il contenuto di `folder` tranne i nomi in `keep`.

    I nomi che iniziano con uno di `last` si cancellano per ultimi: se l'operazione si
    interrompe, resta ciò che identifica la cartella (il database del profilo).

    Raises:
        LinkInTreeError: un elemento è un link; nulla di ciò che segue si tocca.
        OSError: cancellazione non riuscita.
    """
    names = [p.name for p in _children(folder) if p.name not in keep]
    names.sort(key=lambda name: name.startswith(tuple(last)))
    for name in names:
        _remove_path(folder / name)
