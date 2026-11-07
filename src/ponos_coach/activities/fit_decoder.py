# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Reading boundary for FIT files: `bytes` → `Activity` or `FitDecodeError`.

No I/O: the caller reads the bytes (with a maximum) and passes them here. Size, number of
records and duration are checked against the `FitLimits` limits; CRCs are verified; an invalid
file is never repaired, it is rejected with an enumerated reason. `fitdecode` errors and the
only parser exceptions observed on hostile files and random bytes become
`FitDecodeReason.CORRUPT`; a programming error propagates.
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
from numpy.typing import NDArray

from ponos_coach.activities.domain import (
    MAX_ACTIVITY_FILE_BYTES,
    MAX_ACTIVITY_KCAL,
    Activity,
    ActivitySummary,
    ActivityTrack,
    DistanceSeries,
    FileFormat,
    FileId,
    FitIdentity,
    GeoPoint,
    HeartRateSeries,
    Sport,
    build_track,
    encode_polyline,
    is_plausible_instant,
    is_plausible_span,
    simplify_indices,
    truncate_to_millisecond,
)

# FIT profile numbers (field `sport` and `sub_sport` of the `session` message).
_FIT_SPORT_RUNNING = 1
_FIT_SPORT_CYCLING = 2
_FIT_SPORT_TRAINING = 10
_FIT_SPORT_WALKING = 11
_FIT_SPORT_HIKING = 17
_FIT_SUB_SPORT_TRAIL = 3
_FIT_SUB_SPORT_STRENGTH = 20
_FIT_TYPE_ACTIVITY = 4
_MESSAGES_PER_RECORD = 5  # cap on total data messages: 5 times the allowed records
_SEMICIRCLE_INVALID = 0x7FFFFFFF  # invalid value of an sint32 field
_SEMICIRCLES_HALF_TURN = 2**31  # 180 degrees
_SEMICIRCLES_QUARTER_TURN = 2**30  # 90 degrees
_DEGREES_PER_SEMICIRCLE = 180.0 / _SEMICIRCLES_HALF_TURN
_TIMER_EXCESS_TOLERANCE_MS = 1000  # moving time above elapsed: up to 1 s it is brought down
_MS_PER_S = 1000
_SPEED_GAP_S = 10.0  # longer intervals between records are pauses: no distance is integrated

# `fitdecode` errors and the only Python exceptions observed inside its parser on `fit-ostili/`
# and on random or mutated bytes (triggered, on hostile data, by a missing check in the
# library): TypeError (date processor), struct.error (truncated header), AssertionError
# (chunk length) and ValueError (read past the end). They apply only around the reading
# of frames: an error in our code does not pass through here and propagates.
_PARSER_ERRORS = (fitdecode.FitError, TypeError, struct.error, AssertionError, ValueError)


class FitDecodeReason(Enum):
    """Reason why a file does not become an activity."""

    EMPTY = "empty"
    TOO_LARGE = "too_large"
    CORRUPT = "corrupt"
    NOT_AN_ACTIVITY = "not_an_activity"
    TOO_MANY_RECORDS = "too_many_records"
    TOO_LONG = "too_long"


class FitDecodeError(Exception):
    """The file is not a valid activity within the limits; `reason` says why."""

    def __init__(self, reason: FitDecodeReason) -> None:
        super().__init__(reason.value)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class FitLimits:
    """Reading caps (project default): 50 MB, one million records, 7 days."""

    max_bytes: int = MAX_ACTIVITY_FILE_BYTES
    max_records: int = 1_000_000
    max_duration_s: float = 7 * 86_400.0

    def __post_init__(self) -> None:
        if self.max_bytes <= 0 or self.max_records <= 0:
            raise ValueError("byte and record limits must be positive")
        if not math.isfinite(self.max_duration_s) or self.max_duration_s <= 0:
            raise ValueError("the maximum duration must be finite and positive")


def map_sport(sport: int | None, sub_sport: int | None) -> Sport:
    """FIT sport (profile numbers) → `Sport`; what is not recognized is `OTHER`."""
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
    calories_kcal: float | None
    start_lat: int | None  # semicircles
    start_lon: int | None


@dataclass(slots=True)
class _Parsed:
    file_id: dict[str, object] | None = None
    sessions: list[_Session] = field(default_factory=list)
    record_times: list[datetime] = field(default_factory=list)
    hr_times: list[datetime] = field(default_factory=list)
    hr_values: list[int] = field(default_factory=list)
    distance_times: list[datetime] = field(default_factory=list)
    distance_values: list[float] = field(default_factory=list)  # cumulative metres
    speed_times: list[datetime] = field(default_factory=list)
    speed_values: list[float] = field(default_factory=list)  # m/s
    pos_lat: list[int] = field(default_factory=list)  # semicircles, valid pairs only
    pos_lon: list[int] = field(default_factory=list)
    pos_record: list[int] = field(default_factory=list)  # indice del record di ogni posizione
    pos_altitude: list[float] = field(default_factory=list)  # metri, NaN se manca
    messages: int = 0
    records: int = 0


def _value(frame: fitdecode.FitDataMessage, name: str, *, raw: bool = False) -> object:
    if not frame.has_field(name):
        return None
    return frame.get_value(name, raw_value=raw, fallback=None)


def _as_datetime(value: object) -> datetime | None:
    """UTC instant, or None if the time zone is missing or the conversion is not representable."""
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
    """Valid semicircles (not 0x7FFFFFFF and within the range of a coordinate) or None."""
    number = _as_int(value)
    if number is None or number == _SEMICIRCLE_INVALID or abs(number) > _SEMICIRCLES_HALF_TURN:
        return None
    return number


def _position(frame: fitdecode.FitDataMessage, lat: str, lon: str) -> tuple[int, int] | None:
    """Coordinate pair in semicircles if both valid (±90° of latitude)."""
    latitude = _semicircles(_value(frame, lat, raw=True))
    longitude = _semicircles(_value(frame, lon, raw=True))
    if latitude is None or longitude is None or abs(latitude) > _SEMICIRCLES_QUARTER_TURN:
        return None
    return latitude, longitude


def _non_negative(value: object) -> float | None:
    number = _as_float(value)
    return number if number is not None and number >= 0.0 else None


def _declared_kcal(value: object) -> float | None:
    """Energy declared by the device, only if credible (0 means "not computed")."""
    number = _as_float(value)
    return number if number is not None and 0.0 < number <= MAX_ACTIVITY_KCAL else None


def _take_motion(frame: fitdecode.FitDataMessage, when: datetime, parsed: _Parsed) -> None:
    """Cumulative distance and speed of a record, when it has them."""
    distance = _non_negative(_value(frame, "distance"))
    if distance is not None:
        parsed.distance_times.append(when)
        parsed.distance_values.append(distance)
    speed = _non_negative(_value(frame, "enhanced_speed"))
    if speed is None:
        speed = _non_negative(_value(frame, "speed"))
    if speed is not None:
        parsed.speed_times.append(when)
        parsed.speed_values.append(speed)


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
        parsed.pos_record.append(len(parsed.record_times) - 1)
        altitude = _as_float(_value(frame, "enhanced_altitude"))
        if altitude is None:
            altitude = _as_float(_value(frame, "altitude"))
        parsed.pos_altitude.append(math.nan if altitude is None else altitude)
    heart_rate = _as_int(_value(frame, "heart_rate"))
    if heart_rate is not None:
        parsed.hr_times.append(when)
        parsed.hr_values.append(heart_rate)
    _take_motion(frame, when, parsed)


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
                _declared_kcal(_value(frame, "total_calories")),
                start[0] if start else None,
                start[1] if start else None,
            )
        )


def _frames(data: bytes) -> Iterator[fitdecode.FitDataMessage]:
    """Data messages of the file; a parser failure becomes `FitDecodeError(CORRUPT)`."""
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
    """UTC start, elapsed time and moving time (seconds).

    From the sessions; if missing, from the records. The moving time is `total_timer_time` and, for
    a session that does not declare it, its elapsed time (project default).
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
    """HR series and number of discarded samples.

    A sample with a timestamp not greater than all the previous ones (repeated or backwards) is
    discarded: the first of a repetition counts and a step backwards never enters (project
    default, deterministic).
    """
    if not parsed.hr_values:
        return None, 0
    seconds = _seconds_from_first_record(parsed, parsed.hr_times)
    bpm = np.array(parsed.hr_values, dtype=np.float64)
    keep = _increasing_mask(seconds)
    if not keep.any():
        return None, int(seconds.size)
    return HeartRateSeries(seconds[keep], bpm[keep]), int(seconds.size - np.count_nonzero(keep))


def _seconds_from_first_record(parsed: _Parsed, times: list[datetime]) -> NDArray[np.float64]:
    origin = parsed.record_times[0]
    return np.array([(t - origin).total_seconds() for t in times], dtype=np.float64)


def _increasing_mask(seconds: NDArray[np.float64]) -> NDArray[np.bool_]:
    """Samples on the series' axis whose time is greater than all the previous ones (the first
    of a repetition counts, a step backwards never enters). A sample before the first record of
    the file (negative second) is outside the axis and never enters."""
    keep = seconds >= 0
    inside = seconds[keep]
    increasing = np.ones(inside.size, dtype=bool)
    if inside.size:
        increasing[1:] = inside[1:] > np.maximum.accumulate(inside)[:-1]
    keep[keep] = increasing
    return keep


def _integrated(seconds: NDArray[np.float64], speed: NDArray[np.float64]) -> NDArray[np.float64]:
    """Cumulative metres from speed samples (trapezoid); an interval over 10 s adds nothing."""
    interval = np.diff(seconds)
    step = 0.5 * (speed[1:] + speed[:-1]) * interval
    step[interval > _SPEED_GAP_S] = 0.0
    return np.concatenate(([0.0], np.cumsum(step)))


def _distance_series(parsed: _Parsed) -> DistanceSeries | None:
    """Cumulative distance of the records: the `distance` field or, if no record has it, the
    integral of `speed`. A distance that goes backwards is not usable: no series (never repaired).
    """
    if parsed.distance_values:
        seconds = _seconds_from_first_record(parsed, parsed.distance_times)
        meters = np.array(parsed.distance_values, dtype=np.float64)
        keep = _increasing_mask(seconds)
        seconds, meters = seconds[keep], meters[keep]
    elif parsed.speed_values:
        seconds = _seconds_from_first_record(parsed, parsed.speed_times)
        speed = np.array(parsed.speed_values, dtype=np.float64)
        keep = _increasing_mask(seconds)
        seconds = seconds[keep]
        meters = _integrated(seconds, speed[keep])
    else:
        return None
    if seconds.size == 0:
        return None
    if not np.isfinite(meters).all() or (np.diff(meters) < 0).any():
        return None
    return DistanceSeries(seconds, meters)


def _moving_time(elapsed_s: float, timer_s: float) -> float:
    """Moving time consistent with the elapsed time.

    Above 1 s over the elapsed time the file is corrupt; within 1 s it is brought to the elapsed
    time (project
    default: the watch timer and the elapsed time are rounded independently).
    """
    excess_ms = round(timer_s * _MS_PER_S) - round(elapsed_s * _MS_PER_S)
    if excess_ms > _TIMER_EXCESS_TOLERANCE_MS:
        raise FitDecodeError(FitDecodeReason.CORRUPT)
    return min(timer_s, elapsed_s)


def _sum_declared(values: list[float | None]) -> float | None:
    declared = [v for v in values if v is not None]
    return float(sum(declared)) if declared else None


def _total_kcal(parsed: _Parsed) -> float | None:
    """Sum of the sessions' declared energy, only if every session declares it and the total is
    credible: a partial sum would understate the activity."""
    declared = [s.calories_kcal for s in parsed.sessions]
    if not declared or any(v is None for v in declared):
        return None
    total = float(sum(v for v in declared if v is not None))
    return total if total <= MAX_ACTIVITY_KCAL else None


def _start_point(parsed: _Parsed) -> GeoPoint | None:
    """Start from the first session that declares it, otherwise the first valid record."""
    first = parsed.sessions[0] if parsed.sessions else None
    if first is not None and first.start_lat is not None and first.start_lon is not None:
        lat, lon = first.start_lat, first.start_lon
    elif parsed.pos_lat:
        lat, lon = parsed.pos_lat[0], parsed.pos_lon[0]
    else:
        return None
    return GeoPoint(lat * _DEGREES_PER_SEMICIRCLE, lon * _DEGREES_PER_SEMICIRCLE)


def _track_polyline(parsed: _Parsed) -> str | None:
    """Simplified track (RDP, project cap) from the records with a valid position."""
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
        _total_kcal(parsed),
    )


def _require_plausible_times(parsed: _Parsed, start: datetime, elapsed_s: float) -> None:
    """Start, end and times of the records within the allowed window, otherwise `CORRUPT`."""
    times = parsed.record_times
    stamps = [start, *(s.start for s in parsed.sessions if s.start is not None)]
    if times:
        stamps += [min(times), max(times)]
    if not (is_plausible_span(start, elapsed_s) and all(map(is_plausible_instant, stamps))):
        raise FitDecodeError(FitDecodeReason.CORRUPT)


def _first_record_offset_s(parsed: _Parsed, start: datetime) -> float:
    """Seconds from the start of the activity to its first record (the origin of the series)."""
    return (parsed.record_times[0] - start).total_seconds() if parsed.record_times else 0.0


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
        _summary(parsed), discarded, _distance_series(parsed),
        _first_record_offset_s(parsed, start),
    )  # fmt: skip


def decode_activity(data: bytes, limits: FitLimits | None = None) -> Activity:
    """Decodes an activity FIT file.

    Checks the size first, then reads verifying the CRCs and the record and duration
    limits. The identity (`FileId`) requires an activity-type `file_id` with manufacturer, product,
    serial number and creation time; without it, the file is not an activity. Seconds of the
    HR and distance series from the first record; samples with invalid HR (0xFF) omitted.

    Raises:
        FitDecodeError: the only error raised, with the reason in `reason`.
    """
    limits = limits or FitLimits()
    if not data:
        raise FitDecodeError(FitDecodeReason.EMPTY)
    if len(data) > limits.max_bytes:
        raise FitDecodeError(FitDecodeReason.TOO_LARGE)
    return _build(_parse(data, limits), limits)


def _track(parsed: _Parsed) -> ActivityTrack | None:
    """Traccia completa dai record con posizione; un record senza GPS spezza il tratto.

    I secondi partono dal primo record, come la serie di FC.
    """
    if not parsed.pos_record:
        return None
    origin = parsed.record_times[0]
    record = np.array(parsed.pos_record, dtype=np.intp)
    seconds = np.array(
        [(parsed.record_times[i] - origin).total_seconds() for i in parsed.pos_record]
    )
    run_id = np.concatenate(([0], np.cumsum(np.diff(record) > 1))).astype(np.intp)
    return build_track(
        seconds,
        np.array(parsed.pos_lat, dtype=np.float64) * _DEGREES_PER_SEMICIRCLE,
        np.array(parsed.pos_lon, dtype=np.float64) * _DEGREES_PER_SEMICIRCLE,
        np.array(parsed.pos_altitude, dtype=np.float64),
        run_id,
    )


def decode_track(data: bytes, limits: FitLimits | None = None) -> ActivityTrack | None:
    """Traccia completa (posizioni e quota) di un file FIT, o `None` se non ha posizioni.

    Pensata per un file già accettato da `decode_activity` e archiviato: stessi controlli di
    dimensione, record e CRC, ma nessuna delle verifiche sull'identità o sulla durata. Il
    carico e la serie di FC non dipendono da questa funzione.

    Raises:
        FitDecodeError: file vuoto, troppo grande, corrotto o con troppi record.
    """
    limits = limits or FitLimits()
    if not data:
        raise FitDecodeError(FitDecodeReason.EMPTY)
    if len(data) > limits.max_bytes:
        raise FitDecodeError(FitDecodeReason.TOO_LARGE)
    return _track(_parse(data, limits))
