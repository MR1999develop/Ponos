# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Lettura sicura di archivi ZIP non fidati, in memoria (PLAN.md 4.9).

Voci e dimensioni dichiarate si controllano prima di estrarre (directory centrale letta con un
tetto, numero di voci dichiarato e reale, somma delle dimensioni, rapporto di compressione per
voce, dimensione per voce); la dimensione reale si ricontrolla durante l'estrazione a blocchi e
il CRC si verifica fino all'ultimo byte. Voci cifrate, ZIP annidati e nomi non sicuri si
rifiutano; i nomi delle voci non sono mai percorsi e non si usano per scrivere. Ogni rifiuto è
un `ZipReadError` con il motivo, mai uno ZIP riparato.

Modulo di basso livello: non importa nulla del progetto.
"""

from __future__ import annotations

import io
import os
import struct
import zipfile
import zlib
from dataclasses import dataclass, fields
from enum import Enum
from typing import BinaryIO, Final

from ponos_coach.zipread.names import file_suffix, is_safe_entry_name

ACTIVITY_SUFFIXES: Final = (".fit", ".gpx")
ZIP_SUFFIX: Final = ".zip"

_MIB = 1024 * 1024
_GIB = 1024 * _MIB
_BLOCK_BYTES = 64 * 1024
_ZIP_STORED, _ZIP_DEFLATED, _ZIP_AES = 0, 8, 99
_ZIP_ENCRYPTED_FLAGS = 0x1 | 0x40 | 0x2000  # cifrata, cifratura forte, directory mascherata

# Directory centrale e record finale dello ZIP.
_EOCD = struct.Struct("<4sHHHHIIH")
_EOCD_SIGNATURE = b"PK\x05\x06"
_EOCD_MAX_COMMENT = 0xFFFF
_ZIP64_LOCATOR = struct.Struct("<4sIQI")
_ZIP64_LOCATOR_SIGNATURE = b"PK\x06\x07"
_ZIP64_RECORD = struct.Struct("<4sQHHIIQQQQ")
_ZIP64_RECORD_SIGNATURE = b"PK\x06\x06"
_CENTRAL_SIGNATURE = b"PK\x01\x02"
_UINT16_MAX = 0xFFFF
_UINT32_MAX = 0xFFFFFFFF

_ZIP_ERRORS = (
    zipfile.BadZipFile,
    zipfile.LargeZipFile,
    OSError,
    ValueError,
    EOFError,
    OverflowError,
    NotImplementedError,
    RuntimeError,
    zlib.error,
    struct.error,
)


class ZipReason(Enum):
    """Perché uno ZIP o una sua voce non si legge. Il valore è stabile."""

    ARCHIVE_INVALID = "archive_invalid"  # corrotto, incoerente, dimensione o CRC falsi
    ARCHIVE_LIMIT = "archive_limit"  # troppe voci, troppi byte, rapporto di compressione
    ENCRYPTED = "encrypted"  # voce cifrata
    UNSAFE_NAME = "unsafe_name"  # `..`, percorso assoluto, unità, controlli, nome riservato
    TOO_LARGE = "too_large"  # voce oltre `max_entry_bytes`
    NESTED_ARCHIVE = "nested_archive"  # ZIP dentro uno ZIP
    NO_ACTIVITY = "no_activity"  # nessuna voce `.fit` o `.gpx`
    MULTIPLE_ACTIVITIES = "multiple_activities"  # più di una voce `.fit` o `.gpx`


class ZipReadError(Exception):
    """Lo ZIP o la voce non si leggono; `reason` dice perché. Il messaggio è solo il motivo."""

    def __init__(self, reason: ZipReason) -> None:
        super().__init__(reason.value)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class ZipLimits:
    """Tetti della lettura; tutti positivi. I default sono quelli del progetto (PLAN.md 4.9)."""

    max_entries: int = 50_000  # voci di un solo ZIP
    max_total_bytes: int = 5 * _GIB  # somma delle dimensioni dichiarate
    max_ratio: int = 100  # dimensione dichiarata / dimensione compressa, per voce
    max_directory_bytes: int = 32 * _MIB  # directory centrale, letta in memoria
    max_entry_bytes: int = 50 * _MIB  # dimensione dichiarata di una voce

    def __post_init__(self) -> None:
        for item in fields(self):
            if getattr(self, item.name) <= 0:
                raise ValueError(f"il limite {item.name} deve essere positivo")


@dataclass(frozen=True, slots=True)
class ZipActivity:
    """La voce di attività estratta: `suffix` è `.fit` o `.gpx`; il nome della voce non esce."""

    suffix: str
    content: bytes


# --- Directory centrale ---------------------------------------------------------------------


def _locate_zip64(handle: BinaryIO, end_position: int) -> tuple[int, int, int]:
    """Voci, byte e inizio della directory centrale da un record ZIP64."""
    record_position = end_position - _ZIP64_LOCATOR.size - _ZIP64_RECORD.size
    if record_position < 0:
        raise ZipReadError(ZipReason.ARCHIVE_INVALID)
    handle.seek(record_position)
    raw = handle.read(_ZIP64_RECORD.size + _ZIP64_LOCATOR.size)
    if len(raw) != _ZIP64_RECORD.size + _ZIP64_LOCATOR.size:
        raise ZipReadError(ZipReason.ARCHIVE_INVALID)
    record = _ZIP64_RECORD.unpack_from(raw)
    locator = _ZIP64_LOCATOR.unpack_from(raw, _ZIP64_RECORD.size)
    if record[0] != _ZIP64_RECORD_SIGNATURE or locator[0] != _ZIP64_LOCATOR_SIGNATURE:
        raise ZipReadError(ZipReason.ARCHIVE_INVALID)
    total, directory_bytes = record[7], record[8]
    return total, directory_bytes, record_position - directory_bytes


def _locate_directory(handle: BinaryIO, file_size: int) -> tuple[int, int, int]:
    """(voci dichiarate, byte, posizione) della directory centrale, dal record finale."""
    tail_length = min(file_size, _EOCD.size + _EOCD_MAX_COMMENT)
    handle.seek(file_size - tail_length)
    tail = handle.read(tail_length)
    found = tail.rfind(_EOCD_SIGNATURE)
    if found < 0 or len(tail) - found < _EOCD.size:
        raise ZipReadError(ZipReason.ARCHIVE_INVALID)
    values = _EOCD.unpack_from(tail, found)
    _, disk, directory_disk, on_disk, total, directory_bytes, directory_offset, _ = values
    end_position = file_size - tail_length + found
    if disk or directory_disk or on_disk != total:
        raise ZipReadError(ZipReason.ARCHIVE_INVALID)  # archivio su più dischi
    if _UINT16_MAX in (total, on_disk) or _UINT32_MAX in (directory_bytes, directory_offset):
        return _locate_zip64(handle, end_position)
    return total, directory_bytes, end_position - directory_bytes


def _count_directory_entries(raw: bytes, limit: int) -> int:
    """Voci della directory centrale contate sui byte stessi; oltre `limit` si ferma."""
    position = count = 0
    while position < len(raw):
        if raw[position : position + 4] != _CENTRAL_SIGNATURE or position + 46 > len(raw):
            raise ZipReadError(ZipReason.ARCHIVE_INVALID)
        name_bytes, extra_bytes, comment_bytes = struct.unpack_from("<HHH", raw, position + 28)
        position += 46 + name_bytes + extra_bytes + comment_bytes
        count += 1
        if count > limit:
            raise ZipReadError(ZipReason.ARCHIVE_LIMIT)
    if position != len(raw):
        raise ZipReadError(ZipReason.ARCHIVE_INVALID)
    return count


def _check_directory(handle: BinaryIO, limits: ZipLimits) -> int:
    """Controlla la directory centrale prima di lasciarla leggere a `zipfile`: dimensione,
    numero di voci dichiarato e reale. Restituisce il numero di voci."""
    file_size = handle.seek(0, os.SEEK_END)
    declared, directory_bytes, start = _locate_directory(handle, file_size)
    if declared > limits.max_entries or directory_bytes > limits.max_directory_bytes:
        raise ZipReadError(ZipReason.ARCHIVE_LIMIT)
    if start < 0:
        raise ZipReadError(ZipReason.ARCHIVE_INVALID)
    handle.seek(start)
    raw = handle.read(directory_bytes)
    if len(raw) != directory_bytes:
        raise ZipReadError(ZipReason.ARCHIVE_INVALID)
    if _count_directory_entries(raw, limits.max_entries) != declared:
        raise ZipReadError(ZipReason.ARCHIVE_INVALID)
    return declared


# --- Interfaccia ------------------------------------------------------------------------------


def open_checked_zip(handle: BinaryIO, limits: ZipLimits) -> zipfile.ZipFile:
    """Apre lo ZIP dopo aver controllato directory, numero di voci, somma delle dimensioni
    dichiarate e nomi duplicati (anche solo per maiuscole). Chi chiama chiude l'archivio e
    l'handle.

    Raises:
        ZipReadError: `ARCHIVE_INVALID` o `ARCHIVE_LIMIT`.
    """
    try:
        declared = _check_directory(handle, limits)
        archive = zipfile.ZipFile(handle)
    except _ZIP_ERRORS as exc:
        raise ZipReadError(ZipReason.ARCHIVE_INVALID) from exc
    infos = archive.infolist()
    names = {info.filename.lower() for info in infos}
    if len(infos) != declared or len(names) != len(infos):
        archive.close()
        raise ZipReadError(ZipReason.ARCHIVE_INVALID)
    if sum(info.file_size for info in infos) > limits.max_total_bytes:
        archive.close()
        raise ZipReadError(ZipReason.ARCHIVE_LIMIT)
    return archive


def entry_issue(info: zipfile.ZipInfo, limits: ZipLimits) -> ZipReason | None:
    """Perché una voce non si estrae (None se va bene), da dati dichiarati."""
    checks = (
        (not is_safe_entry_name(info.filename), ZipReason.UNSAFE_NAME),
        (
            bool(info.flag_bits & _ZIP_ENCRYPTED_FLAGS) or info.compress_type == _ZIP_AES,
            ZipReason.ENCRYPTED,
        ),
        (info.compress_type not in (_ZIP_STORED, _ZIP_DEFLATED), ZipReason.ARCHIVE_INVALID),
        (info.file_size > limits.max_entry_bytes, ZipReason.TOO_LARGE),
        (info.file_size > limits.max_ratio * info.compress_size, ZipReason.ARCHIVE_LIMIT),
    )
    return next((issue for failed, issue in checks if failed), None)


def extract_entry(archive: zipfile.ZipFile, info: zipfile.ZipInfo) -> bytes:
    """Estrae la voce in memoria a blocchi; la dimensione reale deve essere quella dichiarata.

    Raises:
        ZipReadError: `ARCHIVE_INVALID` (CRC errato, deflate corrotto, voce troncata o con
            dimensione diversa da quella dichiarata).
    """
    chunks: list[bytes] = []
    total = 0
    try:
        with archive.open(info) as stream:
            while chunk := stream.read(_BLOCK_BYTES):
                total += len(chunk)
                if total > info.file_size:
                    raise ZipReadError(ZipReason.ARCHIVE_INVALID)
                chunks.append(chunk)
    except _ZIP_ERRORS as exc:
        raise ZipReadError(ZipReason.ARCHIVE_INVALID) from exc
    if total != info.file_size:
        raise ZipReadError(ZipReason.ARCHIVE_INVALID)
    return b"".join(chunks)


def _single_activity(archive: zipfile.ZipFile) -> zipfile.ZipInfo:
    """L'unica voce di attività dell'archivio; uno ZIP annidato si rifiuta."""
    candidates: list[zipfile.ZipInfo] = []
    for info in archive.infolist():
        suffix = file_suffix(info.filename)
        if info.is_dir() or suffix not in (*ACTIVITY_SUFFIXES, ZIP_SUFFIX):
            continue
        if suffix == ZIP_SUFFIX:
            raise ZipReadError(ZipReason.NESTED_ARCHIVE)
        candidates.append(info)
    if not candidates:
        raise ZipReadError(ZipReason.NO_ACTIVITY)
    if len(candidates) > 1:
        raise ZipReadError(ZipReason.MULTIPLE_ACTIVITIES)
    return candidates[0]


def read_activity_entry(content: bytes, limits: ZipLimits | None = None) -> ZipActivity:
    """L'unica voce `.fit` o `.gpx` di uno ZIP dato come byte, estratta in memoria.

    Le altre voci (che non sono ZIP) si ignorano senza leggerle. Nessun nome esce da qui.

    Raises:
        ZipReadError: lo ZIP non si apre o supera un tetto, ha uno ZIP annidato, nessuna voce
            di attività o più d'una, oppure la voce è cifrata, ha un nome non sicuro, è troppo
            grande, ha rapporto di compressione eccessivo o dati incoerenti.
    """
    effective = limits or ZipLimits()
    with io.BytesIO(content) as handle, open_checked_zip(handle, effective) as archive:
        info = _single_activity(archive)
        issue = entry_issue(info, effective)
        if issue is not None:
            raise ZipReadError(issue)
        return ZipActivity(file_suffix(info.filename), extract_entry(archive, info))
