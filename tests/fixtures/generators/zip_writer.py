# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Writer of ZIP archives for the import tests, valid and hostile, standard library only.

Hostile ZIPs (bomb, false sizes, wrong CRC, encrypted entries, names with `../`) are
obtained by writing a valid ZIP and tweaking its fields with `patch_entry`. Large files are
generated in the tests and are not versioned.
"""

from __future__ import annotations

import io
import struct
import zipfile
from collections.abc import Sequence

LOCAL_SIGNATURE = b"PK\x03\x04"
CENTRAL_SIGNATURE = b"PK\x01\x02"
# Positions of the fields in the two header types.
_CENTRAL_FLAGS, _CENTRAL_CRC, _CENTRAL_COMPRESSED, _CENTRAL_SIZE = 8, 16, 20, 24
_LOCAL_FLAGS, _LOCAL_CRC, _LOCAL_COMPRESSED, _LOCAL_SIZE = 6, 14, 18, 22


def build_zip(entries: Sequence[tuple[str, bytes]], *, method: int = zipfile.ZIP_DEFLATED) -> bytes:
    """ZIP with the given entries (name, content). Names are not cleaned: `../x` stays `../x`."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=method) as archive:
        for name, content in entries:
            info = zipfile.ZipInfo(name, date_time=(2026, 3, 2, 7, 0, 0))
            info.compress_type = method
            archive.writestr(info, content)
    return buffer.getvalue()


def garmin_download(fit: bytes, activity_id: int = 12345678901) -> bytes:
    """Like the original downloaded from the Garmin site: a ZIP with a single
    `<id>_ACTIVITY.fit`."""
    return build_zip([(f"{activity_id}_ACTIVITY.fit", fit)])


def zero_bomb(size_bytes: int, name: str = "bomb.fit") -> bytes:
    """A ZIP with one entry of `size_bytes` zeros: it compresses hundreds of times."""
    return build_zip([(name, bytes(size_bytes))])


def many_entries(count: int, *, suffix: str = ".txt", prefix: str = "e") -> bytes:
    """A ZIP with `count` empty entries without compression (for the caps on the number of
    entries)."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_STORED) as archive:
        for i in range(count):
            info = zipfile.ZipInfo(f"{prefix}{i}{suffix}", date_time=(2026, 3, 2, 7, 0, 0))
            archive.writestr(info, b"")
    return buffer.getvalue()


def _central_offsets(zip_bytes: bytes) -> list[int]:
    offsets = []
    position = zip_bytes.find(CENTRAL_SIGNATURE)
    while position >= 0:
        offsets.append(position)
        position = zip_bytes.find(CENTRAL_SIGNATURE, position + 4)
    return offsets


def _local_offset(zip_bytes: bytes, central: int) -> int:
    return int(struct.unpack_from("<I", zip_bytes, central + 42)[0])


def patch_entry(
    zip_bytes: bytes,
    index: int,
    *,
    file_size: int | None = None,
    compress_size: int | None = None,
    crc: int | None = None,
    flags: int | None = None,
    local_too: bool = True,
) -> bytes:
    """Tweaks the declared fields of entry `index` (central directory and, if `local_too`,
    also the local header). Recalculates nothing: false values stay false."""
    data = bytearray(zip_bytes)
    central = _central_offsets(zip_bytes)[index]
    local = _local_offset(zip_bytes, central)
    fields = [
        (flags, "<H", _CENTRAL_FLAGS, _LOCAL_FLAGS),
        (crc, "<I", _CENTRAL_CRC, _LOCAL_CRC),
        (compress_size, "<I", _CENTRAL_COMPRESSED, _LOCAL_COMPRESSED),
        (file_size, "<I", _CENTRAL_SIZE, _LOCAL_SIZE),
    ]
    for value, fmt, central_at, local_at in fields:
        if value is None:
            continue
        struct.pack_into(fmt, data, central + central_at, value)
        if local_too:
            struct.pack_into(fmt, data, local + local_at, value)
    return bytes(data)
