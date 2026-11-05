# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Riconosce se una cartella sta su una rete o in una cartella sincronizzata col cloud.

SQLite su rete o su una cartella sincronizzata rischia di corrompersi: l'app avvisa, non
blocca. Nel dubbio il tipo è `UNKNOWN`, mai `LOCAL` a caso.

La logica è in funzioni pure su testo e percorsi (provate su qualunque sistema); le sonde che
toccano il sistema (tipo di unità, attributi, file `info.json` di Dropbox) si passano da fuori.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path, PurePosixPath, PureWindowsPath

from ponos_coach.platform import windows_api
from ponos_coach.platform.atomic_file import FileTooLargeError, read_limited
from ponos_coach.profiles.domain import FolderKind

_IS_WINDOWS: bool = sys.platform == "win32"  # bool, non Literal: mypy non scarta un ramo
_MOUNTINFO_PATH = Path("/proc/self/mountinfo")
_MOUNTINFO_MAX_BYTES = 4 * 1024 * 1024
_DROPBOX_INFO_MAX_BYTES = 64 * 1024

_NETWORK_FILESYSTEMS = frozenset({"nfs", "nfs4", "cifs", "smb", "smb3", "smbfs", "sshfs"})
_NETWORK_FILESYSTEM_PREFIX = "fuse."

_ONEDRIVE_ENV_NAMES = ("onedrive", "onedriveconsumer", "onedrivecommercial")
_CLOUD_FOLDER_NAMES = frozenset(
    {"dropbox", "google drive", "googledrive", "my drive", "icloud drive", "iclouddrive"}
)
_ONEDRIVE_NAME_PREFIX = "onedrive"

_FILE_ATTRIBUTE_RECALL_ON_OPEN = 0x00040000
_FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS = 0x00400000
_CLOUD_PLACEHOLDER_ATTRIBUTES = (
    _FILE_ATTRIBUTE_RECALL_ON_OPEN | _FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS
)


# --- Linux ---------------------------------------------------------------------------------


def _unescape_mount_field(field: str) -> str:
    """`\\040` e simili (spazio, tab, a capo, backslash) nei campi di mountinfo."""
    out: list[str] = []
    index = 0
    while index < len(field):
        if field[index] == "\\" and field[index + 1 : index + 4].isdigit():
            out.append(chr(int(field[index + 1 : index + 4], 8)))
            index += 4
        else:
            out.append(field[index])
            index += 1
    return "".join(out)


def parse_mountinfo(text: str) -> list[tuple[str, str]]:
    """Coppie (punto di mount, tipo di file system) da `/proc/self/mountinfo`.

    Le righe di forma inattesa si saltano: il tipo resta ignoto e vale il genitore.
    """
    mounts: list[tuple[str, str]] = []
    for line in text.splitlines():
        head, separator, tail = line.partition(" - ")
        fields = head.split(" ")
        tail_fields = tail.split(" ")
        if not separator or len(fields) < 5 or not tail_fields[0]:
            continue
        mounts.append((_unescape_mount_field(fields[4]), tail_fields[0]))
    return mounts


def kind_from_mounts(path: PurePosixPath, mounts: Sequence[tuple[str, str]]) -> FolderKind:
    """Tipo della cartella dal mount più specifico che la contiene."""
    best_length = -1
    best_type: str | None = None
    for mount_point, filesystem in mounts:
        mount = PurePosixPath(mount_point)
        if (path == mount or mount in path.parents) and len(mount.parts) > best_length:
            best_length, best_type = len(mount.parts), filesystem
    if best_type is None:
        return FolderKind.UNKNOWN
    if best_type in _NETWORK_FILESYSTEMS or best_type.startswith(_NETWORK_FILESYSTEM_PREFIX):
        return FolderKind.NETWORK
    return FolderKind.LOCAL


# --- Windows -------------------------------------------------------------------------------


def _is_network_path(path: PureWindowsPath) -> bool:
    text = str(path)
    if text.upper().startswith("\\\\?\\UNC\\"):
        return True
    return text.startswith("\\\\") and not text.startswith(("\\\\?\\", "\\\\.\\"))


def cloud_roots(env: Mapping[str, str], dropbox_info: str | None) -> list[PureWindowsPath]:
    """Radici delle cartelle sincronizzate note: variabili d'ambiente di OneDrive e
    `info.json` di Dropbox (già letto, o None)."""
    lowered = {key.casefold(): value for key, value in env.items()}
    roots = [PureWindowsPath(lowered[n]) for n in _ONEDRIVE_ENV_NAMES if lowered.get(n)]
    roots.extend(_dropbox_roots(dropbox_info))
    return roots


def _dropbox_roots(info_text: str | None) -> list[PureWindowsPath]:
    if info_text is None:
        return []
    try:
        info = json.loads(info_text)
    except (ValueError, RecursionError):
        return []
    roots: list[PureWindowsPath] = []
    for account in info.values() if isinstance(info, dict) else ():
        value = account.get("path") if isinstance(account, dict) else None
        if isinstance(value, str) and value:
            roots.append(PureWindowsPath(value))
    return roots


def _has_cloud_name(names: Sequence[str]) -> bool:
    """Vero se un componente del percorso ha il nome tipico di una cartella sincronizzata."""
    lowered = [name.casefold() for name in names]
    return any(n in _CLOUD_FOLDER_NAMES or n.startswith(_ONEDRIVE_NAME_PREFIX) for n in lowered)


def _is_under(path: PureWindowsPath, root: PureWindowsPath) -> bool:
    path_parts = [part.casefold() for part in path.parts]
    root_parts = [part.casefold() for part in root.parts]
    return bool(root_parts) and path_parts[: len(root_parts)] == root_parts


def windows_kind(
    path: PureWindowsPath,
    roots: Sequence[PureWindowsPath],
    drive_type_of: Callable[[str], int],
    attributes_of: Callable[[PureWindowsPath], int],
) -> FolderKind:
    """Tipo di una cartella esistente (o del suo antenato esistente) su Windows.

    Rete: percorso UNC o unità remota. Sincronizzata: sotto una radice nota, con un nome
    tipico (OneDrive, Dropbox, Google Drive…) o con attributi di segnaposto cloud sulla
    cartella o su un antenato. Unità sconosciuta: `UNKNOWN`.
    """
    if _is_network_path(path):
        return FolderKind.NETWORK
    root_drive = drive_type_of(path.anchor)
    if root_drive == windows_api.DRIVE_REMOTE:
        return FolderKind.NETWORK
    if any(_is_under(path, root) for root in roots) or _has_cloud_name(path.parts[1:]):
        return FolderKind.CLOUD_SYNCED
    if any(attributes_of(p) & _CLOUD_PLACEHOLDER_ATTRIBUTES for p in (path, *path.parents)):
        return FolderKind.CLOUD_SYNCED
    if root_drive in (windows_api.DRIVE_UNKNOWN, windows_api.DRIVE_NO_ROOT_DIR):
        return FolderKind.UNKNOWN
    return FolderKind.LOCAL


def _file_attributes(path: PureWindowsPath) -> int:
    try:
        return int(getattr(Path(path).stat(), "st_file_attributes", 0))
    except OSError:
        return 0


def _read_dropbox_info(env: Mapping[str, str]) -> str | None:
    lowered = {key.casefold(): value for key, value in env.items()}
    for variable in ("localappdata", "appdata"):
        base = lowered.get(variable)
        if not base:
            continue
        try:
            data = read_limited(Path(base) / "Dropbox" / "info.json", _DROPBOX_INFO_MAX_BYTES)
        except (OSError, FileTooLargeError):
            continue
        if data is not None:
            return data.decode("utf-8", errors="replace")
    return None


# --- Punto d'ingresso ------------------------------------------------------------------------


def nearest_existing(path: Path) -> Path:
    """Il percorso stesso se esiste, altrimenti il suo antenato più vicino che esiste."""
    for candidate in (path, *path.parents):
        if candidate.exists():
            return candidate
    return path


class SystemFolderKindDetector:
    """Rivelatore usato in produzione: `detector(folder) -> FolderKind`.

    Per una cartella mancante riporta il tipo del posto in cui verrebbe creata. Non solleva
    eccezioni: un'informazione non ottenibile dà `UNKNOWN`.
    """

    def __init__(self, env: Mapping[str, str]) -> None:
        self._env = env

    def __call__(self, folder: Path) -> FolderKind:
        """Tipo della cartella o del suo antenato esistente più vicino."""
        existing = nearest_existing(folder.absolute())
        try:
            if _IS_WINDOWS:
                return self._windows(existing)
            return self._posix(existing)
        except (OSError, ValueError):
            return FolderKind.UNKNOWN

    def _windows(self, path: Path) -> FolderKind:
        roots = cloud_roots(self._env, _read_dropbox_info(self._env))
        return windows_kind(
            PureWindowsPath(path),
            roots,
            windows_api.drive_type,
            _file_attributes,
        )

    def _posix(self, path: Path) -> FolderKind:
        data = read_limited(_MOUNTINFO_PATH, _MOUNTINFO_MAX_BYTES)
        if data is None:
            return FolderKind.UNKNOWN
        kind = kind_from_mounts(
            PurePosixPath(path.resolve().as_posix()),
            parse_mountinfo(data.decode("utf-8", "replace")),
        )
        if kind is FolderKind.LOCAL and _has_cloud_name(path.parts[1:]):
            return FolderKind.CLOUD_SYNCED
        return kind
