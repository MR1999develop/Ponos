# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Scrittore minimo di file FIT di attività (protocollo 2.0), solo libreria standard.

Produce file validi (intestazione di 14 byte con CRC, messaggi di definizione e
di dati in little endian, CRC finale) e, con le funzioni a basso livello,
file ostili per il fuzzing e i test di import.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import IntEnum

FIT_EPOCH = datetime(1989, 12, 31, tzinfo=UTC)
PROTOCOL_VERSION = 0x20  # protocollo 2.0
PROFILE_VERSION = 2100
HEADER_SIZE = 14
INVALID_UINT8 = 0xFF
INVALID_UINT32 = 0xFFFFFFFF
MANUFACTURER_DEVELOPMENT = 255

# Tipi base FIT (numero del tipo, formato struct, dimensione).
_ENUM = 0x00
_UINT8 = 0x02
_UINT16 = 0x84
_UINT32 = 0x86
_UINT32Z = 0x8C

# Numeri globali dei messaggi.
_MSG_FILE_ID = 0
_MSG_SESSION = 18
_MSG_LAP = 19
_MSG_RECORD = 20
_MSG_ACTIVITY = 34

_CRC_TABLE = (
    0x0000, 0xCC01, 0xD801, 0x1400, 0xF001, 0x3C00, 0x2800, 0xE401,
    0xA001, 0x6C00, 0x7800, 0xB401, 0x5000, 0x9C01, 0x8801, 0x4400,
)  # fmt: skip


class Sport(IntEnum):
    """Sport del profilo FIT usati dalle fixture."""

    RUNNING = 1
    CYCLING = 2
    TRAINING = 10
    WALKING = 11
    HIKING = 17


class SubSport(IntEnum):
    """Sotto-sport del profilo FIT usati dalle fixture."""

    GENERIC = 0
    TREADMILL = 1
    TRAIL = 3
    ROAD = 7
    STRENGTH_TRAINING = 20


@dataclass(frozen=True)
class Sample:
    """Un messaggio `record`: secondi FIT e FC (None = valore non valido 0xFF)."""

    timestamp: int
    heart_rate: int | None = None


@dataclass(frozen=True)
class ActivitySpec:
    """Descrizione di un'attività da scrivere."""

    start: datetime
    sport: Sport
    sub_sport: SubSport
    samples: list[Sample] = field(default_factory=list)
    serial_number: int = 1000001
    product: int = 1
    time_created: datetime | None = None  # default: l'inizio dell'attività
    total_elapsed_ms: int | None = None  # default: dal primo all'ultimo campione + 1 s
    total_timer_ms: int | None = None  # default: uguale al tempo trascorso


def datetime_to_fit(moment: datetime) -> int:
    """Converte un istante (con fuso) in secondi dall'epoca FIT."""
    if moment.tzinfo is None:
        raise ValueError("serve un datetime con fuso orario")
    return int((moment - FIT_EPOCH).total_seconds())


def fit_crc(data: bytes, crc: int = 0) -> int:
    """CRC-16 del formato FIT (algoritmo a tabella da 16 voci)."""
    for byte in data:
        tmp = _CRC_TABLE[crc & 0xF]
        crc = (crc >> 4) & 0x0FFF
        crc = crc ^ tmp ^ _CRC_TABLE[byte & 0xF]
        tmp = _CRC_TABLE[crc & 0xF]
        crc = (crc >> 4) & 0x0FFF
        crc = crc ^ tmp ^ _CRC_TABLE[(byte >> 4) & 0xF]
    return crc


def build_header(
    data_size: int,
    *,
    signature: bytes = b".FIT",
    header_size: int = HEADER_SIZE,
    header_crc: int | None = None,
) -> bytes:
    """Intestazione di 14 byte; `data_size` può essere falso a scelta del chiamante."""
    head = struct.pack(
        "<BBHI4s", header_size, PROTOCOL_VERSION, PROFILE_VERSION, data_size, signature
    )
    crc = fit_crc(head) if header_crc is None else header_crc
    return head + struct.pack("<H", crc)


def wrap_body(
    body: bytes, *, declared_size: int | None = None, file_crc: int | None = None
) -> bytes:
    """Intestazione + corpo + CRC finale; dimensione dichiarata e CRC falsificabili."""
    size = len(body) if declared_size is None else declared_size
    crc = fit_crc(body) if file_crc is None else file_crc
    return build_header(size) + body + struct.pack("<H", crc)


def _definition(local: int, global_num: int, fields: list[tuple[int, int, int]]) -> bytes:
    """Messaggio di definizione: campi come (numero, dimensione, tipo base)."""
    out = struct.pack("<BBBHB", 0x40 | local, 0, 0, global_num, len(fields))
    for number, size, base_type in fields:
        out += struct.pack("<BBB", number, size, base_type)
    return out


def _data(local: int, fmt: str, *values: int) -> bytes:
    return struct.pack("<B" + fmt, local, *values)


def build_body(spec: ActivitySpec) -> bytes:
    """Corpo (messaggi) di un'attività: file_id, record, lap, session, activity."""
    start = datetime_to_fit(spec.start)
    created = datetime_to_fit(spec.time_created or spec.start)
    stamps = [s.timestamp for s in spec.samples]
    last = max(stamps) if stamps else start
    first = min(stamps) if stamps else start
    elapsed = (
        spec.total_elapsed_ms if spec.total_elapsed_ms is not None else (last - first + 1) * 1000
    )
    timer = spec.total_timer_ms if spec.total_timer_ms is not None else elapsed
    end = max(last, start)

    body = bytearray()
    body += _definition(
        0,
        _MSG_FILE_ID,
        [(0, 1, _ENUM), (1, 2, _UINT16), (2, 2, _UINT16), (3, 4, _UINT32Z), (4, 4, _UINT32)],
    )
    body += _data(
        0, "BHHII", 4, MANUFACTURER_DEVELOPMENT, spec.product, spec.serial_number, created
    )

    body += _definition(1, _MSG_RECORD, [(253, 4, _UINT32), (3, 1, _UINT8)])
    for sample in spec.samples:
        hr = INVALID_UINT8 if sample.heart_rate is None else sample.heart_rate
        body += _data(1, "IB", sample.timestamp, hr)

    timing = [(253, 4, _UINT32), (2, 4, _UINT32), (7, 4, _UINT32), (8, 4, _UINT32)]
    body += _definition(2, _MSG_LAP, timing)
    body += _data(2, "IIII", end, start, elapsed, timer)

    body += _definition(3, _MSG_SESSION, [*timing, (5, 1, _ENUM), (6, 1, _ENUM)])
    body += _data(3, "IIIIBB", end, start, elapsed, timer, int(spec.sport), int(spec.sub_sport))

    body += _definition(4, _MSG_ACTIVITY, [(253, 4, _UINT32), (0, 4, _UINT32), (1, 2, _UINT16)])
    body += _data(4, "IIH", end, timer, 1)
    return bytes(body)


def build_activity(spec: ActivitySpec) -> bytes:
    """File FIT completo e valido di un'attività."""
    return wrap_body(build_body(spec))


# --- File ostili -----------------------------------------------------------


def with_declared_size(fit: bytes, declared_size: int) -> bytes:
    """Stesso file con un'altra dimensione dichiarata nell'intestazione (CRC valido)."""
    return build_header(declared_size) + fit[HEADER_SIZE:]


def truncate(fit: bytes, length: int) -> bytes:
    """Solo i primi `length` byte del file."""
    return fit[:length]


def with_bad_file_crc(fit: bytes) -> bytes:
    """CRC finale errato (ultimo byte invertito)."""
    return fit[:-1] + bytes([fit[-1] ^ 0xFF])


def with_bad_header_crc(fit: bytes) -> bytes:
    """CRC dell'intestazione errato."""
    return fit[: HEADER_SIZE - 1] + bytes([fit[HEADER_SIZE - 1] ^ 0xFF]) + fit[HEADER_SIZE:]


def with_bad_signature(fit: bytes) -> bytes:
    """Firma `.FIT` sostituita, con CRC intestazione coerente."""
    return build_header(len(fit) - HEADER_SIZE - 2, signature=b".FIX") + fit[HEADER_SIZE:]


def huge_declared_file() -> bytes:
    """Intestazione che dichiara ~4 GiB di dati, con un corpo minuscolo."""
    return wrap_body(b"\x40\x00\x00\x00\x00\x00", declared_size=0xFFFFFFF0)


def undefined_local_message() -> bytes:
    """Messaggio di dati senza definizione precedente (CRC corretti)."""
    return wrap_body(bytes([0x05, 1, 2, 3, 4]))


def huge_field_definition() -> bytes:
    """Definizione con 255 campi da 255 byte, poi un messaggio di dati troncato."""
    fields = [(i, 255, _UINT8) for i in range(255)]
    return wrap_body(_definition(0, _MSG_RECORD, fields) + b"\x00" + b"\x01" * 16)
