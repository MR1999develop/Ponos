# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Confine di lettura dei file FIT: `bytes` → `Activity` oppure `FitDecodeError`.

Nessun I/O: il chiamante legge i byte (con un massimo) e li passa qui. Dimensione, numero di
record e durata si controllano con i limiti di `FitLimits`; i CRC si verificano; un file non
valido non si ripara mai, si rifiuta con un motivo enumerato. Gli errori di `fitdecode` e le
sole eccezioni del parser osservate su file ostili e byte casuali diventano
`FitDecodeReason.CORRUPT`; un errore di programmazione si propaga.
"""

from __future__ import annotations

import io
import math
import struct
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum

import fitdecode
import numpy as np

from ponos_coach.activities.domain import (
    MAX_ACTIVITY_FILE_BYTES,
    Activity,
    ActivitySummary,
    FileFormat,
    FileId,
    FitIdentity,
    GeoPoint,
    HeartRateSeries,
    Sport,
    encode_polyline,
    is_plausible_instant,
    is_plausible_span,
    simplify_indices,
    truncate_to_millisecond,
)

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
_SEMICIRCLE_INVALID = 0x7FFFFFFF  # valore non valido di un campo sint32
_SEMICIRCLES_HALF_TURN = 2**31  # 180 gradi
_SEMICIRCLES_QUARTER_TURN = 2**30  # 90 gradi
_DEGREES_PER_SEMICIRCLE = 180.0 / _SEMICIRCLES_HALF_TURN
_TIMER_EXCESS_TOLERANCE_MS = 1000  # tempo in movimento oltre il trascorso: fino a 1 s si porta
_MS_PER_S = 1000

# Errori di `fitdecode` e sole eccezioni di Python osservate dentro il suo parser su `fit-ostili/`
# e su byte casuali o mutati (le fa scattare, su dati ostili, un controllo mancante della
# libreria): TypeError (processore di date), struct.error (header troncato), AssertionError
# (lunghezza di chunk) e ValueError (lettura oltre la fine). Valgono solo attorno alla lettura
# dei frame: un errore del nostro codice non passa di qui e si propaga.
_PARSER_ERRORS = (fitdecode.FitError, TypeError, struct.error, AssertionError, ValueError)


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

    max_bytes: int = MAX_ACTIVITY_FILE_BYTES
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
    timer_s: float | None
    sport: int | None
    sub_sport: int | None
    distance_m: float | None
    ascent_m: float | None
    start_lat: int | None  # semicerchi
    start_lon: int | None


@dataclass(slots=True)
class _Parsed:
    file_id: dict[str, object] | None = None
    sessions: list[_Session] = field(default_factory=list)
    record_times: list[datetime] = field(default_factory=list)
    hr_times: list[datetime] = field(default_factory=list)
    hr_values: list[int] = field(default_factory=list)
    pos_lat: list[int] = field(default_factory=list)  # semicerchi, solo coppie valide
    pos_lon: list[int] = field(default_factory=list)
    messages: int = 0
    records: int = 0


def _value(frame: fitdecode.FitDataMessage, name: str, *, raw: bool = False) -> object:
    if not frame.has_field(name):
        return None
    return frame.get_value(name, raw_value=raw, fallback=None)


def _as_datetime(value: object) -> datetime | None:
    """Istante UTC, o None se manca il fuso o la conversione non è rappresentabile."""
    if not isinstance(value, datetime) or value.tzinfo is None:
        return None
    try:
        return value.astimezone(UTC)
    except (OverflowError, ValueError):
        return None


def _as_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _as_float(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


def _semicircles(value: object) -> int | None:
    """Semicerchi validi (non 0x7FFFFFFF e dentro l'intervallo di una coordinata) o None."""
    number = _as_int(value)
    if number is None or number == _SEMICIRCLE_INVALID or abs(number) > _SEMICIRCLES_HALF_TURN:
        return None
    return number


def _position(frame: fitdecode.FitDataMessage, lat: str, lon: str) -> tuple[int, int] | None:
    """Coppia di coordinate in semicerchi se entrambe valide (±90° di latitudine)."""
    latitude = _semicircles(_value(frame, lat, raw=True))
    longitude = _semicircles(_value(frame, lon, raw=True))
    if latitude is None or longitude is None or abs(latitude) > _SEMICIRCLES_QUARTER_TURN:
        return None
    return latitude, longitude


def _non_negative(value: object) -> float | None:
    number = _as_float(value)
    return number if number is not None and number >= 0.0 else None


def _take_record(frame: fitdecode.FitDataMessage, parsed: _Parsed, limits: FitLimits) -> None:
    parsed.records += 1
    if parsed.records > limits.max_records:
        raise FitDecodeError(FitDecodeReason.TOO_MANY_RECORDS)
    when = _as_datetime(_value(frame, "timestamp"))
    if when is None:
        raise FitDecodeError(FitDecodeReason.CORRUPT)
    parsed.record_times.append(when)
    position = _position(frame, "position_lat", "position_long")
    if position is not None:
        parsed.pos_lat.append(position[0])
        parsed.pos_lon.append(position[1])
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
        start = _position(frame, "start_position_lat", "start_position_long")
        parsed.sessions.append(
            _Session(
                _as_datetime(_value(frame, "start_time")),
                _as_float(_value(frame, "total_elapsed_time")),
                _as_float(_value(frame, "total_timer_time")),
                _as_int(_value(frame, "sport", raw=True)),
                _as_int(_value(frame, "sub_sport", raw=True)),
                _non_negative(_value(frame, "total_distance")),
                _non_negative(_value(frame, "total_ascent")),
                start[0] if start else None,
                start[1] if start else None,
            )
        )


def _frames(data: bytes) -> Iterator[fitdecode.FitDataMessage]:
    """Messaggi di dati del file; un guasto del parser diventa `FitDecodeError(CORRUPT)`."""
    try:
        with fitdecode.FitReader(
            io.BytesIO(data),
            check_crc=fitdecode.CrcCheck.RAISE,
            error_handling=fitdecode.ErrorHandling.RAISE,
        ) as reader:
            frames = iter(reader)
            while True:
                try:
                    frame = next(frames)
                except StopIteration:
                    return
                if isinstance(frame, fitdecode.FitDataMessage):
                    yield frame
    except _PARSER_ERRORS as exc:
        raise FitDecodeError(FitDecodeReason.CORRUPT) from exc


def _parse(data: bytes, limits: FitLimits) -> _Parsed:
    parsed = _Parsed()
    for frame in _frames(data):
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


def _timing(parsed: _Parsed) -> tuple[datetime, float, float]:
    """Inizio UTC, tempo trascorso e tempo in movimento (secondi).

    Dalle sessioni; se mancano, dai record. Il tempo in movimento è `total_timer_time` e, per
    una sessione che non lo dichiara, il suo tempo trascorso (default del progetto).
    """
    times = parsed.record_times
    if not parsed.sessions and not times:
        raise FitDecodeError(FitDecodeReason.NOT_AN_ACTIVITY)
    first = parsed.sessions[0].start if parsed.sessions else None
    start = first or times[0]
    declared = [s for s in parsed.sessions if s.elapsed_s is not None]
    if declared:
        elapsed = sum(s.elapsed_s for s in declared if s.elapsed_s is not None)
        timer = sum(s.timer_s if s.timer_s is not None else s.elapsed_s or 0.0 for s in declared)
        return start, elapsed, timer
    if len(times) < 2:
        raise FitDecodeError(FitDecodeReason.CORRUPT)
    span = (times[-1] - times[0]).total_seconds()
    return start, span, span


def _series(parsed: _Parsed) -> tuple[HeartRateSeries | None, int]:
    """Serie di FC e numero di campioni scartati.

    Un campione con timestamp non maggiore di tutti i precedenti (ripetuto o all'indietro) si
    scarta: vale il primo di una ripetizione e un ritorno indietro non entra mai (default del
    progetto, deterministico).
    """
    if not parsed.hr_values:
        return None, 0
    origin = parsed.record_times[0]
    seconds = np.array([(t - origin).total_seconds() for t in parsed.hr_times])
    bpm = np.array(parsed.hr_values, dtype=np.float64)
    keep = np.ones(seconds.size, dtype=bool)
    keep[1:] = seconds[1:] > np.maximum.accumulate(seconds)[:-1]
    return HeartRateSeries(seconds[keep], bpm[keep]), int(seconds.size - np.count_nonzero(keep))


def _moving_time(elapsed_s: float, timer_s: float) -> float:
    """Tempo in movimento coerente con il trascorso.

    Oltre 1 s sopra il trascorso il file è corrotto; entro 1 s si porta al trascorso (default
    del progetto: il timer dell'orologio e il trascorso si arrotondano in modo indipendente).
    """
    excess_ms = round(timer_s * _MS_PER_S) - round(elapsed_s * _MS_PER_S)
    if excess_ms > _TIMER_EXCESS_TOLERANCE_MS:
        raise FitDecodeError(FitDecodeReason.CORRUPT)
    return min(timer_s, elapsed_s)


def _sum_declared(values: list[float | None]) -> float | None:
    declared = [v for v in values if v is not None]
    return float(sum(declared)) if declared else None


def _start_point(parsed: _Parsed) -> GeoPoint | None:
    """Partenza dalla prima sessione che la dichiara, altrimenti il primo record valido."""
    first = parsed.sessions[0] if parsed.sessions else None
    if first is not None and first.start_lat is not None and first.start_lon is not None:
        lat, lon = first.start_lat, first.start_lon
    elif parsed.pos_lat:
        lat, lon = parsed.pos_lat[0], parsed.pos_lon[0]
    else:
        return None
    return GeoPoint(lat * _DEGREES_PER_SEMICIRCLE, lon * _DEGREES_PER_SEMICIRCLE)


def _track_polyline(parsed: _Parsed) -> str | None:
    """Traccia semplificata (RDP, tetto del progetto) dai record con posizione valida."""
    if not parsed.pos_lat:
        return None
    lat = np.array(parsed.pos_lat, dtype=np.float64) * _DEGREES_PER_SEMICIRCLE
    lon = np.array(parsed.pos_lon, dtype=np.float64) * _DEGREES_PER_SEMICIRCLE
    kept = simplify_indices(lat, lon)
    return encode_polyline([GeoPoint(float(lat[i]), float(lon[i])) for i in kept])


def _summary(parsed: _Parsed) -> ActivitySummary:
    return ActivitySummary(
        _sum_declared([s.distance_m for s in parsed.sessions]),
        _sum_declared([s.ascent_m for s in parsed.sessions]),
        _start_point(parsed),
        _track_polyline(parsed),
    )


def _require_plausible_times(parsed: _Parsed, start: datetime, elapsed_s: float) -> None:
    """Inizio, fine e orari dei record dentro la finestra ammessa, altrimenti `CORRUPT`."""
    times = parsed.record_times
    stamps = [start, *(s.start for s in parsed.sessions if s.start is not None)]
    if times:
        stamps += [min(times), max(times)]
    if not (is_plausible_span(start, elapsed_s) and all(map(is_plausible_instant, stamps))):
        raise FitDecodeError(FitDecodeReason.CORRUPT)


def _build(parsed: _Parsed, limits: FitLimits) -> Activity:
    file_id = _file_id(parsed)
    start, elapsed_s, timer_s = _timing(parsed)
    start = truncate_to_millisecond(start)
    if not (math.isfinite(elapsed_s) and math.isfinite(timer_s) and elapsed_s > 0 and timer_s > 0):
        raise FitDecodeError(FitDecodeReason.CORRUPT)
    if elapsed_s > limits.max_duration_s:
        raise FitDecodeError(FitDecodeReason.TOO_LONG)
    _require_plausible_times(parsed, start, elapsed_s)
    timer_s = _moving_time(elapsed_s, timer_s)
    session = parsed.sessions[0] if parsed.sessions else None
    sport = map_sport(session.sport, session.sub_sport) if session else Sport.OTHER
    series, discarded = _series(parsed)
    return Activity(
        FitIdentity(file_id), FileFormat.FIT, sport, start, elapsed_s, timer_s, series,
        _summary(parsed), discarded,
    )  # fmt: skip


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
    return _build(_parse(data, limits), limits)
