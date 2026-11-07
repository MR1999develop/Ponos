# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Minimal writer of FIT activity files (protocol 2.0), standard library only.

Produces valid files (14-byte header with CRC, definition and data messages in
little endian, final CRC) and, with the low-level functions, hostile files for
fuzzing and import tests.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import IntEnum

FIT_EPOCH = datetime(1989, 12, 31, tzinfo=UTC)
PROTOCOL_VERSION = 0x20  # protocol 2.0
PROFILE_VERSION = 2100
HEADER_SIZE = 14
INVALID_UINT8 = 0xFF
INVALID_UINT32 = 0xFFFFFFFF
INVALID_SINT32 = 0x7FFFFFFF
INVALID_UINT16 = 0xFFFF
SEMICIRCLES_PER_DEGREE = 2**31 / 180.0
MANUFACTURER_DEVELOPMENT = 255

# FIT base types (type number, struct format, size).
_ENUM = 0x00
_UINT8 = 0x02
_UINT16 = 0x84
_SINT32 = 0x85
_UINT32 = 0x86
_UINT32Z = 0x8C

# Global message numbers.
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
    """FIT profile sports used by the fixtures."""

    RUNNING = 1
    CYCLING = 2
    TRAINING = 10
    WALKING = 11
    HIKING = 17


class SubSport(IntEnum):
    """FIT profile sub-sports used by the fixtures."""

    GENERIC = 0
    TREADMILL = 1
    INDOOR_CYCLING = 6
    TRAIL = 3
    ROAD = 7
    STRENGTH_TRAINING = 20


@dataclass(frozen=True)
class Sample:
    """A `record` message: FIT seconds, HR (None = invalid 0xFF) and, if present,
    position in degrees, altitude in metres and cumulative distance in metres.

    The position, altitude and distance fields enter the record definition only if
    at least one sample of the file has them; a sample without a value writes the invalid value.
    """

    timestamp: int
    heart_rate: int | None = None
    lat_deg: float | None = None
    lon_deg: float | None = None
    altitude_m: float | None = None
    distance_m: float | None = None


@dataclass(frozen=True)
class ActivitySpec:
    """Description of an activity to write."""

    start: datetime
    sport: Sport
    sub_sport: SubSport
    samples: list[Sample] = field(default_factory=list)
    serial_number: int = 1000001
    product: int = 1
    time_created: datetime | None = None  # default: the start of the activity
    total_elapsed_ms: int | None = None  # default: from the first to the last sample + 1 s
    total_timer_ms: int | None = None  # default: equal to the elapsed time
    total_distance_m: float | None = None  # session field, if present
    total_ascent_m: int | None = None  # session field, if present
    start_position: tuple[float, float] | None = None  # (lat, lon) in degrees, in the session


def datetime_to_fit(moment: datetime) -> int:
    """Converts an instant (with time zone) to seconds since the FIT epoch."""
    if moment.tzinfo is None:
        raise ValueError("a datetime with a time zone is required")
    return int((moment - FIT_EPOCH).total_seconds())


def fit_crc(data: bytes, crc: int = 0) -> int:
    """CRC-16 of the FIT format (table algorithm with 16 entries)."""
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
    """14-byte header; `data_size` may be false at the caller's choice."""
    head = struct.pack(
        "<BBHI4s", header_size, PROTOCOL_VERSION, PROFILE_VERSION, data_size, signature
    )
    crc = fit_crc(head) if header_crc is None else header_crc
    return head + struct.pack("<H", crc)


def wrap_body(
    body: bytes, *, declared_size: int | None = None, file_crc: int | None = None
) -> bytes:
    """Header + body + final CRC; declared size and CRC can be falsified."""
    size = len(body) if declared_size is None else declared_size
    crc = fit_crc(body) if file_crc is None else file_crc
    return build_header(size) + body + struct.pack("<H", crc)


def _definition(local: int, global_num: int, fields: list[tuple[int, int, int]]) -> bytes:
    """Definition message: fields as (number, size, base type)."""
    out = struct.pack("<BBBHB", 0x40 | local, 0, 0, global_num, len(fields))
    for number, size, base_type in fields:
        out += struct.pack("<BBB", number, size, base_type)
    return out


def _data(local: int, fmt: str, *values: int) -> bytes:
    return struct.pack("<B" + fmt, local, *values)


def degrees_to_semicircles(degrees: float) -> int:
    """Degrees → FIT semicircles (32-bit signed integers)."""
    return round(degrees * SEMICIRCLES_PER_DEGREE)


def _opt(value: float | None, scale: float, invalid: int, offset: float = 0.0) -> int:
    return invalid if value is None else round((value + offset) * scale)


def _record_messages(samples: list[Sample]) -> bytes:
    """Definition and data of the `record`s. Without position, altitude or distance the format is
    the
    original one (same bytes as the already versioned fixtures)."""
    has_position = any(s.lat_deg is not None or s.lon_deg is not None for s in samples)
    has_altitude = any(s.altitude_m is not None for s in samples)
    has_distance = any(s.distance_m is not None for s in samples)
    fields = [(253, 4, _UINT32), (3, 1, _UINT8)]
    fmt = "IB"
    if has_position:
        fields += [(0, 4, _SINT32), (1, 4, _SINT32)]
        fmt += "ii"
    if has_distance:
        fields.append((5, 4, _UINT32))  # scale 100: centimetres
        fmt += "I"
    if has_altitude:
        fields.append((2, 2, _UINT16))  # scale 5, offset 500
        fmt += "H"
    out = bytearray(_definition(1, _MSG_RECORD, fields))
    for s in samples:
        values = [s.timestamp, INVALID_UINT8 if s.heart_rate is None else s.heart_rate]
        if has_position:
            values += [
                INVALID_SINT32 if degrees is None else degrees_to_semicircles(degrees)
                for degrees in (s.lat_deg, s.lon_deg)
            ]
        if has_distance:
            values.append(_opt(s.distance_m, 100, INVALID_UINT32))
        if has_altitude:
            values.append(_opt(s.altitude_m, 5, INVALID_UINT16, 500.0))
        out += _data(1, fmt, *values)
    return bytes(out)


def build_body(spec: ActivitySpec) -> bytes:
    """Body (messages) of an activity: file_id, record, lap, session, activity."""
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

    body += _record_messages(spec.samples)

    timing = [(253, 4, _UINT32), (2, 4, _UINT32), (7, 4, _UINT32), (8, 4, _UINT32)]
    body += _definition(2, _MSG_LAP, timing)
    body += _data(2, "IIII", end, start, elapsed, timer)

    session_fields = [*timing, (5, 1, _ENUM), (6, 1, _ENUM)]
    session_fmt = "IIIIBB"
    session_values = [end, start, elapsed, timer, int(spec.sport), int(spec.sub_sport)]
    if spec.start_position is not None:
        session_fields += [(3, 4, _SINT32), (4, 4, _SINT32)]
        session_fmt += "ii"
        session_values += [degrees_to_semicircles(d) for d in spec.start_position]
    if spec.total_distance_m is not None:
        session_fields.append((9, 4, _UINT32))  # scale 100
        session_fmt += "I"
        session_values.append(round(spec.total_distance_m * 100))
    if spec.total_ascent_m is not None:
        session_fields.append((22, 2, _UINT16))
        session_fmt += "H"
        session_values.append(spec.total_ascent_m)
    body += _definition(3, _MSG_SESSION, session_fields)
    body += _data(3, session_fmt, *session_values)

    body += _definition(4, _MSG_ACTIVITY, [(253, 4, _UINT32), (0, 4, _UINT32), (1, 2, _UINT16)])
    body += _data(4, "IIH", end, timer, 1)
    return bytes(body)


def build_activity(spec: ActivitySpec) -> bytes:
    """Complete and valid FIT file of an activity."""
    return wrap_body(build_body(spec))


# --- Hostile files -----------------------------------------------------------


def with_declared_size(fit: bytes, declared_size: int) -> bytes:
    """Same file with another size declared in the header (valid CRC)."""
    return build_header(declared_size) + fit[HEADER_SIZE:]


def truncate(fit: bytes, length: int) -> bytes:
    """Only the first `length` bytes of the file."""
    return fit[:length]


def with_bad_file_crc(fit: bytes) -> bytes:
    """CRC finale errato (ultimo byte invertito)."""
    return fit[:-1] + bytes([fit[-1] ^ 0xFF])


def with_bad_header_crc(fit: bytes) -> bytes:
    """CRC dell'intestazione errato."""
    return fit[: HEADER_SIZE - 1] + bytes([fit[HEADER_SIZE - 1] ^ 0xFF]) + fit[HEADER_SIZE:]


def with_bad_signature(fit: bytes) -> bytes:
    """`.FIT` signature replaced, with consistent header CRC."""
    return build_header(len(fit) - HEADER_SIZE - 2, signature=b".FIX") + fit[HEADER_SIZE:]


def huge_declared_file() -> bytes:
    """Header that declares ~4 GiB of data, with a tiny body."""
    return wrap_body(b"\x40\x00\x00\x00\x00\x00", declared_size=0xFFFFFFF0)


def undefined_local_message() -> bytes:
    """Data message without a preceding definition (correct CRCs)."""
    return wrap_body(bytes([0x05, 1, 2, 3, 4]))


def huge_field_definition() -> bytes:
    """Definition with 255 fields of 255 bytes, then a truncated data message."""
    fields = [(i, 255, _UINT8) for i in range(255)]
    return wrap_body(_definition(0, _MSG_RECORD, fields) + b"\x00" + b"\x01" * 16)
