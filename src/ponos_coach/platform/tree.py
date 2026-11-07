# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Visiting and deleting folder trees without ever following links or junctions.

A symbolic link or a junction inside a profile folder could take the
deletion outside it: it is refused before touching anything. Other Windows reparse
points (OneDrive placeholders, deduplication) are real files and are deleted normally.
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
    """There is a symbolic link or a junction in the tree."""


def is_link(path: Path) -> bool:
    """True if `path` is a symbolic link or a junction (without following it)."""
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
    """Verifies that neither `root` nor anything inside it is a link.

    Raises:
        LinkInTreeError: a link was found (the name is in the message, not the full path).
        OSError: tree not readable.
    """
    pending = [root]
    while pending:
        current = pending.pop()
        if is_link(current):
            raise LinkInTreeError(f"link or junction: {current.name}")
        if _is_directory(current):
            pending.extend(_children(current))


def _unlink_file(path: Path) -> None:
    try:
        path.unlink()
    except PermissionError:
        path.chmod(stat.S_IWRITE)  # read-only file on Windows
        path.unlink()


def _remove_path(path: Path) -> None:
    """Deletes a file or folder (with content); refuses links, even those that appeared after the
    checks."""
    if is_link(path):
        raise LinkInTreeError(f"link or junction: {path.name}")
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
                raise LinkInTreeError(f"link or junction: {child.name}")
            if _is_directory(child):
                directories.append(child)
                pending.append(child)
            else:
                _unlink_file(child)
    for directory in reversed(directories):  # children before parents
        directory.rmdir()


def remove_children(folder: Path, *, keep: Collection[str], last: Collection[str]) -> None:
    """Deletes the content of `folder` except the names in `keep`.

    Names that start with one of `last` are deleted last: if the operation is
    interrupted, what identifies the folder (the profile database) remains.

    Raises:
        LinkInTreeError: an element is a link; nothing that follows is touched.
        OSError: deletion failed.
    """
    names = [p.name for p in _children(folder) if p.name not in keep]
    names.sort(key=lambda name: name.startswith(tuple(last)))
    for name in names:
        _remove_path(folder / name)
