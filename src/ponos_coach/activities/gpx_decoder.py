# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Reading boundary for GPX files: `bytes` → `Activity` or `GpxDecodeError`.

GPX is untrusted XML: it is read by events (SAX) with `defusedxml`, without DTD, entities or
external resources, in blocks, and the limits on size, depth, number of points and text length
are checked before and during reading (the whole tree is never built). No
I/O: the caller reads the bytes with a maximum. An invalid file is never repaired; it is
rejected with its own error (`GpxDecodeError`) that uses the same reasons as the FIT reader
(`FitDecodeReason`).

Project rules (PLAN.md section 2 point 12): identity = SHA-256 of the bytes; HR from
`gpxtpx:hr` of TrackPointExtension v1 and v2; sport from the `type` of the track; moving time =
sum of the intervals between consecutive points up to 30 s; distance with the haversine;
positive ascent with a 3 m threshold; without times it is not an activity. Each `trkseg` is a
stretch of its own: between the last point of a segment and the first of the next neither
distance, nor moving time, nor ascent is counted.
"""

from __future__ import annotations

import hashlib
import math
import re
from array import array
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import Enum
from xml.sax import SAXException
from xml.sax.handler import ContentHandler
from xml.sax.xmlreader import AttributesNSImpl

import numpy as np
from defusedxml import DefusedXmlException, expatreader
from numpy.typing import NDArray

from ponos_coach.activities.domain import (
    EARTH_RADIUS_M,
    MAX_ACTIVITY_FILE_BYTES,
    Activity,
    ActivitySummary,
    FileFormat,
    GeoPoint,
    GpxIdentity,
    HeartRateSeries,
    Sport,
    encode_polyline,
    is_plausible_instant,
    simplify_indices,
    truncate_to_millisecond,
)
from ponos_coach.activities.fit_decoder import FitDecodeReason

_GPX_NAMESPACES = frozenset(
    {"http://www.topografix.com/GPX/1/0", "http://www.topografix.com/GPX/1/1"}
)
_HR_NAMESPACES = frozenset(
    {
        "http://www.garmin.com/xmlschemas/TrackPointExtension/v1",
        "http://www.garmin.com/xmlschemas/TrackPointExtension/v2",
    }
)
MOVING_GAP_MAX_S = 30.0  # longer intervals between two points are not moving time
ASCENT_THRESHOLD_M = 3.0  # smaller altitude changes are noise
# Parser errors on hostile XML: syntax (SAX), forbidden DTD or entities (defusedxml) and
# unknown declared encoding (`LookupError`) or undecodable (`UnicodeError`).
_XML_ERRORS = (SAXException, DefusedXmlException, LookupError, UnicodeError)
_CHUNK_BYTES = 64 * 1024
_SNIFF_BYTES = 64 * 1024
_NO_TIME = -(2**63)  # sentinel for a point without time, never a valid time
_US = timedelta(microseconds=1)
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_MAX_ATTRIBUTE_CHARS = 32

_TIME = re.compile(
    r"(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2}:\d{2})(?:\.(\d{1,9}))?(Z|[+-]\d{2}:\d{2})", re.ASCII
)
_DECIMAL = re.compile(r"[+-]?\d{1,9}(?:\.\d{1,15})?", re.ASCII)
_HEART_RATE = re.compile(r"\d{1,3}", re.ASCII)

# Values of the `type` field (lowercase, spaces and hyphens as `_`) → sport.
_SPORT_BY_TYPE = {
    **dict.fromkeys(
        ("running", "run", "street_running", "treadmill_running", "virtual_run"), Sport.RUNNING
    ),
    **dict.fromkeys(("trail_running", "trail", "trailrunning"), Sport.TRAIL),
    **dict.fromkeys(
        (
            "cycling",
            "biking",
            "bike",
            "ride",
            "road_biking",
            "gravel_cycling",
            "mountain_biking",
            "indoor_cycling",
            "virtual_ride",
        ),
        Sport.CYCLING,
    ),
    **dict.fromkeys(("hiking", "hike"), Sport.HIKING),
    **dict.fromkeys(("walking", "walk"), Sport.WALKING),
    **dict.fromkeys(("strength_training", "strength", "weight_training"), Sport.STRENGTH),
}


class GpxDecodeError(Exception):
    """The GPX file is not a valid activity within the limits; `reason` says why."""

    def __init__(self, reason: FitDecodeReason) -> None:
        super().__init__(reason.value)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class GpxLimits:
    """Reading caps (project default): 50 MB, depth 32, one million points.

    `max_text_chars` limits each text (and each attribute value read); `max_duration_s` is
    the maximum duration of the activity (7 days, as for FIT).
    """

    max_bytes: int = MAX_ACTIVITY_FILE_BYTES
    max_depth: int = 32
    max_points: int = 1_000_000
    max_text_chars: int = 4096
    max_duration_s: float = 7 * 86_400.0

    def __post_init__(self) -> None:
        if min(self.max_bytes, self.max_depth, self.max_points, self.max_text_chars) <= 0:
            raise ValueError("byte, depth, point and text limits must be positive")
        if not math.isfinite(self.max_duration_s) or self.max_duration_s <= 0:
            raise ValueError("the maximum duration must be finite and positive")


@dataclass(frozen=True, slots=True)
class XmlRoot:
    """Root element of an XML document: local name and namespace (None if absent)."""

    name: str
    namespace: str | None


# --- Root recognition ------------------------------------------------------------------------


class _RootFoundError(Exception):
    """Stops reading at the first element: only the root name is needed."""

    def __init__(self, root: XmlRoot) -> None:
        super().__init__("root found")
        self.root = root


class _RootSniffer(ContentHandler):
    def startElementNS(  # noqa: N802 - name imposed by xml.sax
        self, name: tuple[str | None, str], _qname: str | None, _attrs: object
    ) -> None:
        raise _RootFoundError(XmlRoot(name[1], name[0]))


def _new_parser(handler: ContentHandler) -> expatreader.DefusedExpatParser:
    """SAX parser without DTD, entities or external references, with namespaces."""
    parser = expatreader.create_parser(
        namespaceHandling=1, forbid_dtd=True, forbid_entities=True, forbid_external=True
    )
    parser.setContentHandler(handler)
    return parser


def read_xml_root(data: bytes) -> XmlRoot | None:
    """Root of the XML document, or None if `data` does not start as allowed XML.

    Looks only at the first 64 KiB, without DTD or entities: a document with a DOCTYPE has no root
    (it is rejected like any hostile XML). It does not verify the rest of the document.
    """
    parser = _new_parser(_RootSniffer())
    try:
        parser.feed(data[:_SNIFF_BYTES])
    except _RootFoundError as found:
        return found.root
    except _XML_ERRORS:
        return None
    return None


# --- Values ----------------------------------------------------------------------------------


def _corrupt() -> GpxDecodeError:
    return GpxDecodeError(FitDecodeReason.CORRUPT)


def _parse_time_us(text: str) -> int:
    """ISO 8601 instant with time zone (`Z` or `±hh:mm`) in microseconds since 1970-01-01 UTC."""
    match = _TIME.fullmatch(text)
    if match is None:
        raise _corrupt()
    day, clock, fraction, zone = match.groups()
    micro = (fraction or "").ljust(6, "0")[:6]
    try:
        moment = datetime.fromisoformat(f"{day}T{clock}.{micro}{zone}")
        if not is_plausible_instant(moment):
            raise _corrupt()
        return (moment - _EPOCH) // _US
    except (ValueError, OverflowError) as exc:
        raise _corrupt() from exc


def _parse_decimal(text: str) -> float:
    if _DECIMAL.fullmatch(text) is None:
        raise _corrupt()
    return float(text)


def _parse_heart_rate(text: str) -> float:
    if _HEART_RATE.fullmatch(text) is None:
        raise _corrupt()
    return float(int(text))


def _coordinate(attrs: AttributesNSImpl, name: str, bound: float) -> float:
    text = attrs.get((None, name))
    if text is None or len(text) > _MAX_ATTRIBUTE_CHARS:
        raise _corrupt()
    value = _parse_decimal(text.strip())
    if abs(value) > bound:
        raise _corrupt()
    return value


def _set_once[T](current: T | None, new: T) -> T:
    """A value appears only once per point: a repetition is an inconsistent file."""
    if current is not None:
        raise _corrupt()
    return new


def map_gpx_sport(text: str | None) -> Sport:
    """Sport from the `type` field (case-insensitive); what is not recognized is OTHER."""
    if text is None:
        return Sport.OTHER
    key = re.sub(r"[\s-]+", "_", text.strip().lower())
    return _SPORT_BY_TYPE.get(key, Sport.OTHER)


# --- Event-based reading -----------------------------------------------------------------


class _Kind(Enum):
    ROOT = "root"
    TRACK = "track"
    SEGMENT = "segment"
    POINT = "point"
    ELEVATION = "elevation"
    TIME = "time"
    TYPE = "type"
    HEART_RATE = "heart_rate"
    OTHER = "other"


_CHILD_KINDS = {
    (_Kind.ROOT, "trk"): _Kind.TRACK,
    (_Kind.TRACK, "trkseg"): _Kind.SEGMENT,
    (_Kind.TRACK, "type"): _Kind.TYPE,
    (_Kind.SEGMENT, "trkpt"): _Kind.POINT,
    (_Kind.POINT, "ele"): _Kind.ELEVATION,
    (_Kind.POINT, "time"): _Kind.TIME,
}
_CAPTURED = frozenset({_Kind.ELEVATION, _Kind.TIME, _Kind.TYPE, _Kind.HEART_RATE})


@dataclass(slots=True)
class _Point:
    lat: float
    lon: float
    ele: float | None = None
    when_us: int | None = None
    heart_rate: float | None = None


class _Columns:
    """Point columns (one value per point, NaN or sentinel where missing)."""

    def __init__(self) -> None:
        self.lat = array("d")
        self.lon = array("d")
        self.ele = array("d")
        self.heart_rate = array("d")
        self.when_us = array("q")
        self.segment = array("i")

    def append(self, point: _Point, segment: int) -> None:
        nan = math.nan
        self.lat.append(point.lat)
        self.lon.append(point.lon)
        self.ele.append(nan if point.ele is None else point.ele)
        self.heart_rate.append(nan if point.heart_rate is None else point.heart_rate)
        self.when_us.append(_NO_TIME if point.when_us is None else point.when_us)
        self.segment.append(segment)


class _GpxHandler(ContentHandler):
    """Collects the points of the tracks; each limit trips at the event that exceeds it."""

    def __init__(self, limits: GpxLimits) -> None:
        super().__init__()
        self._limits = limits
        self._kinds: list[_Kind] = []
        self._namespace = ""
        self._point: _Point | None = None
        self._capture: _Kind | None = None
        self._text: list[str] = []
        self._text_len = 0
        self._segments = 0
        self._points = 0
        self.columns = _Columns()
        self.sport_text: str | None = None

    def _kind_of(self, namespace: str | None, local: str) -> _Kind:
        if not self._kinds:
            if namespace not in _GPX_NAMESPACES or local != "gpx":
                raise _corrupt()
            self._namespace = namespace or ""
            return _Kind.ROOT
        if namespace in _HR_NAMESPACES and local == "hr":
            inside_point = self._point is not None and self._capture is None
            return _Kind.HEART_RATE if inside_point else _Kind.OTHER
        if namespace != self._namespace:
            return _Kind.OTHER
        return _CHILD_KINDS.get((self._kinds[-1], local), _Kind.OTHER)

    def _start_point(self, attrs: AttributesNSImpl) -> None:
        self._points += 1
        if self._points > self._limits.max_points:
            raise GpxDecodeError(FitDecodeReason.TOO_MANY_RECORDS)
        self._point = _Point(_coordinate(attrs, "lat", 90.0), _coordinate(attrs, "lon", 180.0))

    def startElementNS(  # noqa: N802 - name imposed by xml.sax
        self, name: tuple[str | None, str], _qname: str | None, attrs: AttributesNSImpl
    ) -> None:
        if len(self._kinds) >= self._limits.max_depth:
            raise _corrupt()
        kind = self._kind_of(*name)
        self._kinds.append(kind)
        self._text_len = 0
        if kind is _Kind.POINT:
            self._start_point(attrs)
        elif kind is _Kind.SEGMENT:
            self._segments += 1
        elif kind in _CAPTURED:
            self._capture = kind
            self._text = []

    def characters(self, content: str) -> None:
        self._text_len += len(content)
        if self._text_len > self._limits.max_text_chars:
            raise _corrupt()
        if self._capture is not None:
            self._text.append(content)

    def endElementNS(  # noqa: N802
        self, _name: tuple[str | None, str], _qname: str | None
    ) -> None:
        kind = self._kinds.pop()
        if kind in _CAPTURED:
            self._finish_capture(kind)
        elif kind is _Kind.POINT:
            self._finish_point()
        self._text_len = 0

    def _finish_capture(self, kind: _Kind) -> None:
        text = "".join(self._text).strip()
        self._capture = None
        point = self._point
        if kind is _Kind.TYPE:
            self.sport_text = self.sport_text or text
        elif point is None:
            raise _corrupt()  # unreachable: ele, time and hr appear only inside a point
        elif kind is _Kind.ELEVATION:
            point.ele = _set_once(point.ele, _parse_decimal(text))
        elif kind is _Kind.TIME:
            point.when_us = _set_once(point.when_us, _parse_time_us(text))
        else:
            point.heart_rate = _set_once(point.heart_rate, _parse_heart_rate(text))

    def _finish_point(self) -> None:
        point, self._point = self._point, None
        if point is None:
            raise _corrupt()  # unreachable: every point end follows its start
        self.columns.append(point, self._segments)


def _read_points(data: bytes, limits: GpxLimits) -> _GpxHandler:
    handler = _GpxHandler(limits)
    parser = _new_parser(handler)
    try:
        for start in range(0, len(data), _CHUNK_BYTES):
            parser.feed(data[start : start + _CHUNK_BYTES])
        parser.close()
    except _XML_ERRORS as exc:
        raise _corrupt() from exc
    return handler


# --- Computations on the columns ---------------------------------------------------------------


def _microseconds(columns: _Columns) -> NDArray[np.int64]:
    """Point times in microseconds: all present and non-decreasing, otherwise error."""
    when = np.frombuffer(columns.when_us, dtype=np.int64)
    missing = when == _NO_TIME
    if missing.all():
        raise GpxDecodeError(FitDecodeReason.NOT_AN_ACTIVITY)
    if missing.any() or (np.diff(when) < 0).any():
        raise _corrupt()
    return when


def _durations(
    when_us: NDArray[np.int64], same_segment: NDArray[np.bool_], limits: GpxLimits
) -> tuple[float, float]:
    """Elapsed time and moving time (seconds), in whole microseconds."""
    elapsed_us = int(when_us[-1] - when_us[0])
    if elapsed_us <= 0:
        raise GpxDecodeError(FitDecodeReason.NOT_AN_ACTIVITY)
    if elapsed_us / 1e6 > limits.max_duration_s:
        raise GpxDecodeError(FitDecodeReason.TOO_LONG)
    gap_us = np.diff(when_us)
    moving = same_segment & (gap_us <= round(MOVING_GAP_MAX_S * 1e6))
    timer_us = int(gap_us[moving].sum())
    if timer_us <= 0:
        raise GpxDecodeError(FitDecodeReason.NOT_AN_ACTIVITY)
    return elapsed_us / 1e6, timer_us / 1e6


def _distance_m(
    lat_deg: NDArray[np.float64], lon_deg: NDArray[np.float64], same_segment: NDArray[np.bool_]
) -> float:
    """Sum of the distances (haversine) between consecutive points of the same segment."""
    lat, lon = np.radians(lat_deg), np.radians(lon_deg)
    half_dlat, half_dlon = np.diff(lat) / 2.0, np.diff(lon) / 2.0
    a = np.sin(half_dlat) ** 2 + np.cos(lat[:-1]) * np.cos(lat[1:]) * np.sin(half_dlon) ** 2
    a = np.clip(a, 0.0, 1.0)
    steps = 2.0 * EARTH_RADIUS_M * np.arctan2(np.sqrt(a), np.sqrt(1.0 - a))
    return float(steps[same_segment].sum())


def _turning_points(ele: NDArray[np.float64]) -> list[float]:
    """Altitudes without consecutive repetitions or intermediate points of a monotonic stretch.

    The ascent with threshold does not change: only from where to where the altitude rises or falls
    matters.
    """
    steps = np.diff(ele)
    distinct = ele[np.concatenate(([True], steps != 0.0))]
    if distinct.size <= 2:
        return [float(v) for v in distinct]
    direction = np.sign(np.diff(distinct))
    inner = np.flatnonzero(direction[1:] != direction[:-1]) + 1
    keep = np.concatenate(([0], inner, [distinct.size - 1]))
    return [float(v) for v in distinct[keep]]


@dataclass(slots=True)
class _Zigzag:
    """Positive ascent with hysteresis: a climb counts when the altitude drops by `threshold`
    from its maximum or the series ends, and only if it is at least `threshold` high."""

    threshold: float
    low: float
    high: float
    rising: bool = True
    total: float = 0.0

    def feed(self, elevation: float) -> None:
        if self.rising:
            self._rising(elevation)
        else:
            self._falling(elevation)

    def _rising(self, elevation: float) -> None:
        if elevation > self.high:
            self.high = elevation
        elif self.high - elevation >= self.threshold:
            self._close_climb()
            self.rising, self.low = False, elevation
        elif elevation < self.low:
            self.low = self.high = elevation

    def _falling(self, elevation: float) -> None:
        if elevation < self.low:
            self.low = elevation
        elif elevation - self.low >= self.threshold:
            self.rising, self.high = True, elevation

    def _close_climb(self) -> None:
        if self.high - self.low >= self.threshold:
            self.total += self.high - self.low

    def finish(self) -> float:
        if self.rising:
            self._close_climb()
        return self.total


def positive_ascent_m(
    elevation_m: NDArray[np.float64], threshold_m: float = ASCENT_THRESHOLD_M
) -> float:
    """Positive ascent of a series of altitudes, in meters, against altitude noise.

    Climbs smaller than `threshold_m` do not count; a climb counts whole (from its lowest
    point to the highest) when the altitude drops by at least `threshold_m` or the series ends.
    A series with fewer than two altitudes gives 0.
    """
    points = _turning_points(np.asarray(elevation_m, dtype=np.float64))
    if len(points) < 2:
        return 0.0
    zigzag = _Zigzag(threshold_m, points[0], points[0])
    for elevation in points[1:]:
        zigzag.feed(elevation)
    return zigzag.finish()


def _ascent_m(columns: _Columns) -> float | None:
    """Positive ascent of the summed segments (None if no point has altitude)."""
    ele = np.frombuffer(columns.ele, dtype=np.float64)
    segment = np.frombuffer(columns.segment, dtype=np.intc)
    known = ~np.isnan(ele)
    if not known.any():
        return None
    ele, segment = ele[known], segment[known]
    runs = np.split(ele, np.flatnonzero(np.diff(segment)) + 1)
    return float(sum(positive_ascent_m(run) for run in runs))


def _summary(columns: _Columns, same_segment: NDArray[np.bool_]) -> ActivitySummary:
    lat = np.frombuffer(columns.lat, dtype=np.float64)
    lon = np.frombuffer(columns.lon, dtype=np.float64)
    kept = simplify_indices(lat, lon)
    track = encode_polyline([GeoPoint(float(lat[i]), float(lon[i])) for i in kept])
    return ActivitySummary(
        _distance_m(lat, lon, same_segment),
        _ascent_m(columns),
        GeoPoint(float(lat[0]), float(lon[0])),
        track,
    )


def _heart_rate(
    columns: _Columns, when_us: NDArray[np.int64]
) -> tuple[HeartRateSeries | None, int]:
    """HR series (seconds from the first point) and discarded samples.

    As for FIT, a sample with a time not greater than all the previous ones (repeated) is
    discarded: the first of a repetition counts.
    """
    bpm = np.frombuffer(columns.heart_rate, dtype=np.float64)
    known = ~np.isnan(bpm)
    if not known.any():
        return None, 0
    seconds = (when_us[known] - when_us[0]) / 1e6
    keep = np.ones(seconds.size, dtype=bool)
    keep[1:] = seconds[1:] > np.maximum.accumulate(seconds)[:-1]
    series = HeartRateSeries(seconds[keep], bpm[known][keep])
    return series, int(seconds.size - np.count_nonzero(keep))


def _build(data: bytes, handler: _GpxHandler, limits: GpxLimits) -> Activity:
    columns = handler.columns
    if not columns.lat:
        raise GpxDecodeError(FitDecodeReason.NOT_AN_ACTIVITY)
    when_us = _microseconds(columns)
    segment = np.frombuffer(columns.segment, dtype=np.intc)
    same_segment = segment[1:] == segment[:-1]
    elapsed_s, timer_s = _durations(when_us, same_segment, limits)
    series, discarded = _heart_rate(columns, when_us)
    return Activity(
        GpxIdentity(hashlib.sha256(data).hexdigest()),
        FileFormat.GPX,
        map_gpx_sport(handler.sport_text),
        truncate_to_millisecond(_EPOCH + int(when_us[0]) * _US),
        elapsed_s,
        timer_s,
        series,
        _summary(columns, same_segment),
        discarded,
    )


def decode_gpx(data: bytes, limits: GpxLimits | None = None) -> Activity:
    """Decodes a GPX 1.0 or 1.1 activity file.

    Checks the size first, then reads by events with the depth, point and text
    limits. Considers only the tracks (`trk`/`trkseg`/`trkpt`): routes and waypoints are not
    activities. Every point has valid `lat` and `lon`; times are ISO 8601 with time zone, present on
    all points and non-decreasing. HR comes from the Garmin `hr` extensions (v1 and v2).
    The start is the time of the first point; the elapsed time goes from the first to the last.

    Raises:
        GpxDecodeError: the only error raised, with the reason in `reason`. NOT_AN_ACTIVITY if
            there are no points, times, duration or intervals within 30 s; CORRUPT for invalid
            or hostile XML, malformed values, incomplete or backwards times, depth and
            text limits exceeded.
    """
    limits = limits or GpxLimits()
    if not data:
        raise GpxDecodeError(FitDecodeReason.EMPTY)
    if len(data) > limits.max_bytes:
        raise GpxDecodeError(FitDecodeReason.TOO_LARGE)
    return _build(data, _read_points(data, limits), limits)
