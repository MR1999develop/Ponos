# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Confine di lettura dei file FIT: `bytes` → `Activity` oppure `FitDecodeError`.

Nessun I/O: il chiamante legge i byte (con un massimo) e li passa qui. Dimensione, numero di
record e durata si controllano con i limiti di `FitLimits`; i CRC si verificano; un file non
valido non si ripara mai, si rifiuta con un motivo enumerato. Qualunque guasto interno della
libreria `fitdecode` su dati ostili diventa `FitDecodeReason.CORRUPT`.
"""

from __future__ import annotations

import io
import math
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum

import fitdecode
import numpy as np

from ponos_coach.activities.domain import Activity, FileId
from ponos_coach.load.domain import HeartRateSeries, Sport

# Numeri del profilo FIT (campo `sport` e `sub_sport` del messaggio `session`).
_FIT_SPORT_RUNNING = 1
_FIT_SPORT_CYCLING = 2
_FIT_SPORT_TRAINING = 10
_FIT_SPORT_WALKING = 11
_FIT_SPORT_HIKING = 17
_FIT_SUB_SPORT_TRAIL = 3
_FIT_SUB_SPORT_STRENGTH = 20
_FIT_TYPE_ACTIVITY = 4
_MESSAGES_PER_RECORD = 5  # tetto ai messaggi di dati totali: 5 volte i record ammessi


class FitDecodeReason(Enum):
    """Motivo per cui un file non diventa un'attività."""

    EMPTY = "empty"
    TOO_LARGE = "too_large"
    CORRUPT = "corrupt"
    NOT_AN_ACTIVITY = "not_an_activity"
    TOO_MANY_RECORDS = "too_many_records"
    TOO_LONG = "too_long"


class FitDecodeError(Exception):
    """Il file non è un'attività valida entro i limiti; `reason` dice perché."""

    def __init__(self, reason: FitDecodeReason) -> None:
        super().__init__(reason.value)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class FitLimits:
    """Tetti di lettura (default del progetto): 50 MB, un milione di record, 7 giorni."""

    max_bytes: int = 50 * 1024 * 1024
    max_records: int = 1_000_000
    max_duration_s: float = 7 * 86_400.0

    def __post_init__(self) -> None:
        if self.max_bytes <= 0 or self.max_records <= 0:
            raise ValueError("i limiti di byte e record devono essere positivi")
        if not math.isfinite(self.max_duration_s) or self.max_duration_s <= 0:
            raise ValueError("la durata massima deve essere finita e positiva")


def map_sport(sport: int | None, sub_sport: int | None) -> Sport:
    """Sport FIT (numeri del profilo) → `Sport`; ciò che non si riconosce è `OTHER`."""
    if sport == _FIT_SPORT_RUNNING:
        return Sport.TRAIL if sub_sport == _FIT_SUB_SPORT_TRAIL else Sport.RUNNING
    if sport == _FIT_SPORT_TRAINING:
        return Sport.STRENGTH if sub_sport == _FIT_SUB_SPORT_STRENGTH else Sport.OTHER
    simple = {
        _FIT_SPORT_CYCLING: Sport.CYCLING,
        _FIT_SPORT_WALKING: Sport.WALKING,
        _FIT_SPORT_HIKING: Sport.HIKING,
    }
    return simple.get(sport, Sport.OTHER) if sport is not None else Sport.OTHER


@dataclass(slots=True)
class _Session:
    start: datetime | None
    elapsed_s: float | None
    sport: int | None
    sub_sport: int | None


@dataclass(slots=True)
class _Parsed:
    file_id: dict[str, object] | None = None
    sessions: list[_Session] = field(default_factory=list)
    record_times: list[datetime] = field(default_factory=list)
    hr_times: list[datetime] = field(default_factory=list)
    hr_values: list[int] = field(default_factory=list)
    messages: int = 0
    records: int = 0


def _value(frame: fitdecode.FitDataMessage, name: str, *, raw: bool = False) -> object:
    if not frame.has_field(name):
        return None
    return frame.get_value(name, raw_value=raw, fallback=None)


def _as_datetime(value: object) -> datetime | None:
    return value.astimezone(UTC) if isinstance(value, datetime) and value.tzinfo else None


def _as_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _as_float(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


def _take_record(frame: fitdecode.FitDataMessage, parsed: _Parsed, limits: FitLimits) -> None:
    parsed.records += 1
    if parsed.records > limits.max_records:
        raise FitDecodeError(FitDecodeReason.TOO_MANY_RECORDS)
    when = _as_datetime(_value(frame, "timestamp"))
    if when is None:
        raise FitDecodeError(FitDecodeReason.CORRUPT)
    parsed.record_times.append(when)
    heart_rate = _as_int(_value(frame, "heart_rate"))
    if heart_rate is not None:
        parsed.hr_times.append(when)
        parsed.hr_values.append(heart_rate)


def _take_message(frame: fitdecode.FitDataMessage, parsed: _Parsed, limits: FitLimits) -> None:
    parsed.messages += 1
    if parsed.messages > limits.max_records * _MESSAGES_PER_RECORD:
        raise FitDecodeError(FitDecodeReason.TOO_MANY_RECORDS)
    if frame.name == "record":
        _take_record(frame, parsed, limits)
    elif frame.name == "file_id" and parsed.file_id is None:
        parsed.file_id = {
            "type": _as_int(_value(frame, "type", raw=True)),
            "manufacturer": _as_int(_value(frame, "manufacturer", raw=True)),
            "product": _as_int(_value(frame, "product", raw=True)),
            "serial_number": _as_int(_value(frame, "serial_number")),
            "time_created": _as_datetime(_value(frame, "time_created")),
        }
    elif frame.name == "session":
        parsed.sessions.append(
            _Session(
                _as_datetime(_value(frame, "start_time")),
                _as_float(_value(frame, "total_elapsed_time")),
                _as_int(_value(frame, "sport", raw=True)),
                _as_int(_value(frame, "sub_sport", raw=True)),
            )
        )


def _parse(data: bytes, limits: FitLimits) -> _Parsed:
    parsed = _Parsed()
    with fitdecode.FitReader(
        io.BytesIO(data),
        check_crc=fitdecode.CrcCheck.RAISE,
        error_handling=fitdecode.ErrorHandling.RAISE,
    ) as reader:
        for frame in reader:
            if isinstance(frame, fitdecode.FitDataMessage):
                _take_message(frame, parsed, limits)
    return parsed


def _file_id(parsed: _Parsed) -> FileId:
    raw = parsed.file_id
    if raw is None or raw["type"] != _FIT_TYPE_ACTIVITY:
        raise FitDecodeError(FitDecodeReason.NOT_AN_ACTIVITY)
    manufacturer, product = raw["manufacturer"], raw["product"]
    serial, created = raw["serial_number"], raw["time_created"]
    if not (
        isinstance(manufacturer, int)
        and isinstance(product, int)
        and isinstance(serial, int)
        and isinstance(created, datetime)
    ):
        raise FitDecodeError(FitDecodeReason.NOT_AN_ACTIVITY)
    return FileId(manufacturer, product, serial, created)


def _timing(parsed: _Parsed) -> tuple[datetime, float]:
    """Inizio UTC e durata (secondi) dalle sessioni; se mancano, dai record."""
    times = parsed.record_times
    if not parsed.sessions and not times:
        raise FitDecodeError(FitDecodeReason.NOT_AN_ACTIVITY)
    first = parsed.sessions[0].start if parsed.sessions else None
    start = first or times[0]
    elapsed = [s.elapsed_s for s in parsed.sessions if s.elapsed_s is not None]
    if elapsed:
        return start, sum(elapsed)
    if len(times) < 2:
        raise FitDecodeError(FitDecodeReason.CORRUPT)
    return start, (times[-1] - times[0]).total_seconds()


def _series(parsed: _Parsed) -> HeartRateSeries | None:
    if not parsed.hr_values:
        return None
    origin = parsed.record_times[0]
    seconds = np.array([(t - origin).total_seconds() for t in parsed.hr_times])
    return HeartRateSeries(seconds, np.array(parsed.hr_values, dtype=np.float64))


def _build(parsed: _Parsed, limits: FitLimits) -> Activity:
    file_id = _file_id(parsed)
    start, duration_s = _timing(parsed)
    if duration_s > limits.max_duration_s:
        raise FitDecodeError(FitDecodeReason.TOO_LONG)
    session = parsed.sessions[0] if parsed.sessions else None
    sport = map_sport(session.sport, session.sub_sport) if session else Sport.OTHER
    return Activity(file_id, sport, start, duration_s, _series(parsed))


def decode_activity(data: bytes, limits: FitLimits | None = None) -> Activity:
    """Decodifica un file FIT di attività.

    Controlla prima la dimensione, poi legge con verifica dei CRC e dei limiti di record e di
    durata. L'identità (`FileId`) richiede `file_id` di tipo attività con produttore, prodotto,
    numero di serie e ora di creazione; senza, il file non è un'attività. Secondi della serie
    di FC dal primo record; campioni con FC non valida (0xFF) omessi.

    Raises:
        FitDecodeError: l'unico errore in uscita, con il motivo in `reason`.
    """
    limits = limits or FitLimits()
    if not data:
        raise FitDecodeError(FitDecodeReason.EMPTY)
    if len(data) > limits.max_bytes:
        raise FitDecodeError(FitDecodeReason.TOO_LARGE)
    try:
        parsed = _parse(data, limits)
        return _build(parsed, limits)
    except FitDecodeError:
        raise
    except Exception as exc:  # confine: ogni guasto su dati ostili è un file corrotto
        raise FitDecodeError(FitDecodeReason.CORRUPT) from exc
