# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Confine di lettura dei file GPX: `bytes` → `Activity` oppure `GpxDecodeError`.

Il GPX è XML non fidato: si legge a eventi (SAX) con `defusedxml`, senza DTD, entità né
risorse esterne, a blocchi, e i limiti di dimensione, profondità, numero di punti e lunghezza
dei testi si controllano prima e durante la lettura (mai si costruisce l'albero intero). Nessun
I/O: il chiamante legge i byte con un massimo. Un file non valido non si ripara mai; si
rifiuta con un errore proprio (`GpxDecodeError`) che usa gli stessi motivi del lettore FIT
(`FitDecodeReason`).

Regole del progetto (PLAN.md sezione 2 punto 12): identità = SHA-256 dei byte; FC da
`gpxtpx:hr` di TrackPointExtension v1 e v2; sport dal `type` della traccia; tempo in movimento =
somma degli intervalli tra punti consecutivi fino a 30 s; distanza con l'emisenoverso;
dislivello positivo con una soglia di 3 m; senza orari non è un'attività. Ogni `trkseg` è un
tratto a sé: tra l'ultimo punto di un segmento e il primo del successivo non si contano né
distanza, né tempo in movimento, né dislivello.
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
MOVING_GAP_MAX_S = 30.0  # intervalli più lunghi tra due punti non sono tempo in movimento
ASCENT_THRESHOLD_M = 3.0  # variazioni di quota più piccole sono rumore
# Errori del parser su XML ostile: sintassi (SAX), DTD o entità vietati (defusedxml) e codifica
# dichiarata sconosciuta (`LookupError`) o non decodificabile (`UnicodeError`).
_XML_ERRORS = (SAXException, DefusedXmlException, LookupError, UnicodeError)
_CHUNK_BYTES = 64 * 1024
_SNIFF_BYTES = 64 * 1024
_NO_TIME = -(2**63)  # sentinella del punto senza orario, mai un orario valido
_US = timedelta(microseconds=1)
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_MAX_ATTRIBUTE_CHARS = 32

_TIME = re.compile(r"(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2}:\d{2})(?:\.(\d{1,9}))?(Z|[+-]\d{2}:\d{2})")
_DECIMAL = re.compile(r"[+-]?\d{1,9}(?:\.\d{1,15})?")
_HEART_RATE = re.compile(r"\d{1,3}")

# Valori del campo `type` (minuscolo, spazi e trattini come `_`) → sport.
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
    """Il file GPX non è un'attività valida entro i limiti; `reason` dice perché."""

    def __init__(self, reason: FitDecodeReason) -> None:
        super().__init__(reason.value)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class GpxLimits:
    """Tetti di lettura (default del progetto): 50 MB, profondità 32, un milione di punti.

    `max_text_chars` limita ogni testo (e ogni valore di attributo letto); `max_duration_s` è
    la durata massima dell'attività (7 giorni, come per il FIT).
    """

    max_bytes: int = MAX_ACTIVITY_FILE_BYTES
    max_depth: int = 32
    max_points: int = 1_000_000
    max_text_chars: int = 4096
    max_duration_s: float = 7 * 86_400.0

    def __post_init__(self) -> None:
        if min(self.max_bytes, self.max_depth, self.max_points, self.max_text_chars) <= 0:
            raise ValueError("i limiti di byte, profondità, punti e testi devono essere positivi")
        if not math.isfinite(self.max_duration_s) or self.max_duration_s <= 0:
            raise ValueError("la durata massima deve essere finita e positiva")


@dataclass(frozen=True, slots=True)
class XmlRoot:
    """Elemento radice di un documento XML: nome locale e spazio dei nomi (None se assente)."""

    name: str
    namespace: str | None


# --- Riconoscimento della radice -------------------------------------------------------------


class _RootFoundError(Exception):
    """Interrompe la lettura al primo elemento: serve solo il nome della radice."""

    def __init__(self, root: XmlRoot) -> None:
        super().__init__("radice trovata")
        self.root = root


class _RootSniffer(ContentHandler):
    def startElementNS(  # noqa: N802 - nome imposto da xml.sax
        self, name: tuple[str | None, str], _qname: str | None, _attrs: object
    ) -> None:
        raise _RootFoundError(XmlRoot(name[1], name[0]))


def _new_parser(handler: ContentHandler) -> expatreader.DefusedExpatParser:
    """Parser SAX senza DTD, entità né riferimenti esterni, con gli spazi dei nomi."""
    parser = expatreader.create_parser(
        namespaceHandling=1, forbid_dtd=True, forbid_entities=True, forbid_external=True
    )
    parser.setContentHandler(handler)
    return parser


def read_xml_root(data: bytes) -> XmlRoot | None:
    """Radice del documento XML, o None se `data` non comincia come XML ammesso.

    Guarda solo i primi 64 KiB, senza DTD né entità: un documento con DOCTYPE non ha radice
    (si rifiuta come qualsiasi XML ostile). Non verifica il resto del documento.
    """
    parser = _new_parser(_RootSniffer())
    try:
        parser.feed(data[:_SNIFF_BYTES])
    except _RootFoundError as found:
        return found.root
    except _XML_ERRORS:
        return None
    return None


# --- Valori ----------------------------------------------------------------------------------


def _corrupt() -> GpxDecodeError:
    return GpxDecodeError(FitDecodeReason.CORRUPT)


def _parse_time_us(text: str) -> int:
    """Istante ISO 8601 con fuso (`Z` o `±hh:mm`) in microsecondi da 1970-01-01 UTC."""
    match = _TIME.fullmatch(text)
    if match is None:
        raise _corrupt()
    day, clock, fraction, zone = match.groups()
    micro = (fraction or "").ljust(6, "0")[:6]
    try:
        moment = datetime.fromisoformat(f"{day}T{clock}.{micro}{zone}")
    except ValueError as exc:
        raise _corrupt() from exc
    return (moment - _EPOCH) // _US


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
    """Un valore compare una volta sola per punto: una ripetizione è un file incoerente."""
    if current is not None:
        raise _corrupt()
    return new


def map_gpx_sport(text: str | None) -> Sport:
    """Sport dal campo `type` (senza distinguere maiuscole); ciò che non si riconosce è OTHER."""
    if text is None:
        return Sport.OTHER
    key = re.sub(r"[\s-]+", "_", text.strip().lower())
    return _SPORT_BY_TYPE.get(key, Sport.OTHER)


# --- Lettura a eventi --------------------------------------------------------------------------


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
    """Colonne dei punti (un valore per punto, NaN o sentinella dove manca)."""

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
    """Raccoglie i punti delle tracce; ogni limite scatta all'evento che lo supera."""

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

    def startElementNS(  # noqa: N802 - nome imposto da xml.sax
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
            raise _corrupt()  # irraggiungibile: ele, time e hr stanno solo dentro un punto
        elif kind is _Kind.ELEVATION:
            point.ele = _set_once(point.ele, _parse_decimal(text))
        elif kind is _Kind.TIME:
            point.when_us = _set_once(point.when_us, _parse_time_us(text))
        else:
            point.heart_rate = _set_once(point.heart_rate, _parse_heart_rate(text))

    def _finish_point(self) -> None:
        point, self._point = self._point, None
        if point is None:
            raise _corrupt()  # irraggiungibile: ogni fine di punto segue il suo inizio
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


# --- Calcoli sulle colonne ---------------------------------------------------------------------


def _microseconds(columns: _Columns) -> NDArray[np.int64]:
    """Orari dei punti in microsecondi: tutti presenti e non decrescenti, altrimenti errore."""
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
    """Tempo trascorso e tempo in movimento (secondi), in interi di microsecondi."""
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
    """Somma delle distanze (emisenoverso) tra punti consecutivi dello stesso segmento."""
    lat, lon = np.radians(lat_deg), np.radians(lon_deg)
    half_dlat, half_dlon = np.diff(lat) / 2.0, np.diff(lon) / 2.0
    a = np.sin(half_dlat) ** 2 + np.cos(lat[:-1]) * np.cos(lat[1:]) * np.sin(half_dlon) ** 2
    a = np.clip(a, 0.0, 1.0)
    steps = 2.0 * EARTH_RADIUS_M * np.arctan2(np.sqrt(a), np.sqrt(1.0 - a))
    return float(steps[same_segment].sum())


def _turning_points(ele: NDArray[np.float64]) -> list[float]:
    """Quote senza ripetizioni consecutive né punti intermedi di un tratto monotono.

    Il dislivello con soglia non cambia: conta solo da dove a dove la quota sale o scende.
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
    """Dislivello positivo con isteresi: una salita conta quando la quota scende di `threshold`
    dal suo massimo o la serie finisce, e solo se è alta almeno `threshold`."""

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
    """Dislivello positivo di una serie di quote, in metri, contro il rumore della quota.

    Le salite più piccole di `threshold_m` non contano; una salita conta intera (dal suo punto
    più basso al più alto) quando la quota scende di almeno `threshold_m` o la serie finisce.
    Una serie con meno di due quote dà 0.
    """
    points = _turning_points(np.asarray(elevation_m, dtype=np.float64))
    if len(points) < 2:
        return 0.0
    zigzag = _Zigzag(threshold_m, points[0], points[0])
    for elevation in points[1:]:
        zigzag.feed(elevation)
    return zigzag.finish()


def _ascent_m(columns: _Columns) -> float | None:
    """Dislivello positivo dei segmenti sommati (None se nessun punto ha la quota)."""
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
    """Serie di FC (secondi dal primo punto) e campioni scartati.

    Come per il FIT, un campione con orario non maggiore di tutti i precedenti (ripetuto) si
    scarta: vale il primo di una ripetizione.
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
    """Decodifica un file GPX 1.0 o 1.1 di attività.

    Controlla prima la dimensione, poi legge a eventi con i limiti di profondità, punti e
    testi. Considera solo le tracce (`trk`/`trkseg`/`trkpt`): rotte e waypoint non sono
    attività. Ogni punto ha `lat` e `lon` validi; gli orari sono ISO 8601 con fuso, presenti su
    tutti i punti e non decrescenti. La FC viene dalle estensioni Garmin `hr` (v1 e v2).
    L'inizio è l'orario del primo punto; il tempo trascorso va dal primo all'ultimo.

    Raises:
        GpxDecodeError: l'unico errore in uscita, con il motivo in `reason`. NOT_AN_ACTIVITY se
            non ci sono punti, orari, durata o intervalli entro 30 s; CORRUPT per XML non valido
            o ostile, valori malformati, orari incompleti o all'indietro, limiti di profondità
            e di testo superati.
    """
    limits = limits or GpxLimits()
    if not data:
        raise GpxDecodeError(FitDecodeReason.EMPTY)
    if len(data) > limits.max_bytes:
        raise GpxDecodeError(FitDecodeReason.TOO_LARGE)
    return _build(data, _read_points(data, limits), limits)
