# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Scrittore di archivi ZIP per i test dell'import, validi e ostili, solo libreria standard.

Gli ZIP ostili (bomba, dimensioni false, CRC sbagliato, voci cifrate, nomi con `../`) si
ottengono scrivendo uno ZIP valido e ritoccandone i campi con `patch_entry`. I file grandi si
generano nei test e non si versionano.
"""

from __future__ import annotations

import io
import struct
import zipfile
from collections.abc import Sequence

LOCAL_SIGNATURE = b"PK\x03\x04"
CENTRAL_SIGNATURE = b"PK\x01\x02"
# Posizioni dei campi nei due tipi di intestazione.
_CENTRAL_FLAGS, _CENTRAL_CRC, _CENTRAL_COMPRESSED, _CENTRAL_SIZE = 8, 16, 20, 24
_LOCAL_FLAGS, _LOCAL_CRC, _LOCAL_COMPRESSED, _LOCAL_SIZE = 6, 14, 18, 22


def build_zip(entries: Sequence[tuple[str, bytes]], *, method: int = zipfile.ZIP_DEFLATED) -> bytes:
    """ZIP con le voci date (nome, contenuto). I nomi non si ripuliscono: `../x` resta `../x`."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=method) as archive:
        for name, content in entries:
            info = zipfile.ZipInfo(name, date_time=(2026, 3, 2, 7, 0, 0))
            info.compress_type = method
            archive.writestr(info, content)
    return buffer.getvalue()


def garmin_download(fit: bytes, activity_id: int = 12345678901) -> bytes:
    """Come l'originale scaricato dal sito Garmin: uno ZIP con un solo `<id>_ACTIVITY.fit`."""
    return build_zip([(f"{activity_id}_ACTIVITY.fit", fit)])


def zero_bomb(size_bytes: int, name: str = "bomb.fit") -> bytes:
    """Uno ZIP con una voce di `size_bytes` zeri: si comprime di centinaia di volte."""
    return build_zip([(name, bytes(size_bytes))])


def many_entries(count: int, *, suffix: str = ".txt", prefix: str = "e") -> bytes:
    """Uno ZIP con `count` voci vuote senza compressione (per i tetti sul numero di voci)."""
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
    """Ritocca i campi dichiarati della voce `index` (directory centrale e, se `local_too`,
    anche l'intestazione locale). Non ricalcola nulla: i valori falsi restano falsi."""
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
