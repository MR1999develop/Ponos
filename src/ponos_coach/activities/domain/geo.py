# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Punti geografici, semplificazione di una traccia e polyline codificati.

Le posizioni GPS sono dati personali sensibili: questo modulo non le registra né le stampa
(il `repr` di `GeoPoint` le mostra, quindi i chiamanti non devono metterlo nei log).
"""

from __future__ import annotations

import heapq
import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from numpy.typing import ArrayLike, NDArray

from ponos_coach.activities.domain.limits import MAX_TRACK_POINTS, TRACK_TOLERANCE_M

_EARTH_RADIUS_M = 6_371_008.8
_POLYLINE_SCALE = 1e5  # precisione standard dei polyline: 1e-5 gradi
_POLYLINE_FIRST_CHAR = 63  # '?'
_POLYLINE_LAST_CHAR = 126  # '~'
_CHUNK_BITS = 5
_CHUNK_MASK = 0x1F
_CONTINUATION = 0x20
_MAX_CHUNKS_PER_VALUE = 7  # 35 bit: oltre ogni scarto di coordinate valido
# Un valore occupa al massimo 6 caratteri per le coordinate valide (scarto fino a 360 gradi).
MAX_POLYLINE_CHARS = MAX_TRACK_POINTS * 2 * 6


@dataclass(frozen=True, slots=True)
class GeoPoint:
    """Una posizione in gradi decimali: latitudine in [-90, 90], longitudine in [-180, 180]."""

    lat_deg: float
    lon_deg: float

    def __post_init__(self) -> None:
        if not (math.isfinite(self.lat_deg) and math.isfinite(self.lon_deg)):
            raise ValueError("le coordinate devono essere finite")
        if not -90.0 <= self.lat_deg <= 90.0:
            raise ValueError("latitudine fuori da [-90, 90]")
        if not -180.0 <= self.lon_deg <= 180.0:
            raise ValueError("longitudine fuori da [-180, 180]")


def _farthest(
    x: NDArray[np.float64], y: NDArray[np.float64], first: int, last: int
) -> tuple[float, int]:
    """Distanza (m) e indice del punto interno più lontano dal segmento first-last."""
    px, py = x[first + 1 : last] - x[first], y[first + 1 : last] - y[first]
    sx, sy = x[last] - x[first], y[last] - y[first]
    length_sq = sx * sx + sy * sy
    if length_sq == 0.0:
        distance = np.hypot(px, py)
    else:
        t = np.clip((px * sx + py * sy) / length_sq, 0.0, 1.0)
        distance = np.hypot(px - t * sx, py - t * sy)
    offset = int(np.argmax(distance))
    return float(distance[offset]), first + 1 + offset


def _projected(
    lat_deg: NDArray[np.float64], lon_deg: NDArray[np.float64]
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """Coordinate piane in metri (equirettangolare attorno alla latitudine media)."""
    lat = np.radians(lat_deg)
    lon = np.unwrap(np.radians(lon_deg))  # attraversa l'antimeridiano senza salti
    scale = math.cos(float(lat.mean()))
    x = (lon - lon[0]) * scale * _EARTH_RADIUS_M
    y = (lat - lat[0]) * _EARTH_RADIUS_M
    return x, y


def _columns(
    lat_deg: ArrayLike, lon_deg: ArrayLike, tolerance_m: float, max_points: int
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    lat = np.asarray(lat_deg, dtype=np.float64)
    lon = np.asarray(lon_deg, dtype=np.float64)
    if lat.ndim != 1 or lat.shape != lon.shape:
        raise ValueError("servono due colonne monodimensionali della stessa lunghezza")
    if not (math.isfinite(tolerance_m) and tolerance_m >= 0.0) or max_points < 2:
        raise ValueError("tolleranza non valida o meno di 2 punti ammessi")
    if not (np.isfinite(lat).all() and np.isfinite(lon).all()):
        raise ValueError("le coordinate devono essere finite")
    return lat, lon


def _split_until(
    x: NDArray[np.float64], y: NDArray[np.float64], tolerance_m: float, max_points: int
) -> NDArray[np.bool_]:
    """Maschera dei punti tenuti: si divide sempre il tratto con lo scostamento maggiore."""
    keep = np.zeros(x.size, dtype=bool)
    keep[0] = keep[-1] = True
    kept = 2
    # Heap di (-scostamento, primo, ultimo, indice): a parità vince il tratto più a sinistra.
    heap: list[tuple[float, int, int, int]] = []

    def push(first: int, last: int) -> None:
        if last - first >= 2:  # serve almeno un punto interno
            distance, index = _farthest(x, y, first, last)
            heapq.heappush(heap, (-distance, first, last, index))

    push(0, x.size - 1)
    while heap and kept < max_points:
        negative, first, last, index = heapq.heappop(heap)
        if -negative <= tolerance_m:
            break
        keep[index] = True
        kept += 1
        push(first, index)
        push(index, last)
    return keep


def simplify_indices(
    lat_deg: ArrayLike,
    lon_deg: ArrayLike,
    tolerance_m: float = TRACK_TOLERANCE_M,
    max_points: int = MAX_TRACK_POINTS,
) -> NDArray[np.intp]:
    """Indici (crescenti) dei punti tenuti da Ramer-Douglas-Peucker, in tempo vettoriale.

    Come `simplify_track` ma su colonne di coordinate, senza creare un oggetto per punto.
    Primo e ultimo punto restano sempre. L'algoritmo è iterativo (nessuna ricorsione): divide
    sempre il tratto con lo scostamento maggiore e si ferma quando questo non supera
    `tolerance_m` oppure sono stati tenuti `max_points` punti; il tetto lascia quindi i punti
    più significativi. Deterministico.

    Raises:
        ValueError: colonne di lunghezza diversa o non unidimensionali, valori non finiti,
            tolleranza negativa o non finita, `max_points` < 2.
    """
    lat, lon = _columns(lat_deg, lon_deg, tolerance_m, max_points)
    if lat.size <= 2:
        return np.arange(lat.size, dtype=np.intp)
    x, y = _projected(lat, lon)
    return np.flatnonzero(_split_until(x, y, tolerance_m, max_points))


def simplify_track(
    points: Sequence[GeoPoint],
    tolerance_m: float = TRACK_TOLERANCE_M,
    max_points: int = MAX_TRACK_POINTS,
) -> tuple[GeoPoint, ...]:
    """Traccia semplificata con Ramer-Douglas-Peucker (iterativo) e un tetto ai punti.

    Ogni punto scartato dista al più `tolerance_m` metri dal tratto che lo sostituisce, salvo
    quando il tetto `max_points` (default del progetto: 2000) obbliga a scartare di più.
    Primo e ultimo punto restano; l'ordine è conservato; una traccia già corta e senza punti
    ridondanti torna invariata. Deterministica.

    Raises:
        ValueError: tolleranza negativa o non finita, `max_points` < 2.
    """
    indices = simplify_indices(
        [p.lat_deg for p in points], [p.lon_deg for p in points], tolerance_m, max_points
    )
    return tuple(points[int(i)] for i in indices)


def _encode_value(value: int, out: list[str]) -> None:
    value = ~(value << 1) if value < 0 else value << 1
    while value >= _CONTINUATION:
        out.append(chr(((value & _CHUNK_MASK) | _CONTINUATION) + _POLYLINE_FIRST_CHAR))
        value >>= _CHUNK_BITS
    out.append(chr(value + _POLYLINE_FIRST_CHAR))


def encode_polyline(points: Sequence[GeoPoint]) -> str:
    """Polyline codificato (algoritmo standard, precisione 1e-5 gradi, circa 1 m).

    Ogni coordinata si arrotonda a 1e-5 gradi; decodificando si ritrova il punto entro
    0,5e-5 gradi. Una sequenza vuota dà la stringa vuota.

    Raises:
        ValueError: più di `MAX_TRACK_POINTS` punti (il risultato non sarebbe decodificabile).
    """
    if len(points) > MAX_TRACK_POINTS:
        raise ValueError("troppi punti per un polyline")
    scaled = np.rint(
        np.array([[p.lat_deg, p.lon_deg] for p in points], dtype=np.float64).reshape(-1, 2)
        * _POLYLINE_SCALE
    ).astype(np.int64)
    deltas = np.diff(scaled, axis=0, prepend=np.zeros((1, 2), dtype=np.int64))
    out: list[str] = []
    for value in deltas.ravel().tolist():
        _encode_value(int(value), out)
    return "".join(out)


def _decode_values(encoded: str) -> list[int]:
    """Valori interi (con segno) di un polyline, in ordine."""
    values: list[int] = []
    accumulated = shift = chunks = 0
    for char in encoded:
        code = ord(char) - _POLYLINE_FIRST_CHAR
        if not 0 <= code <= _POLYLINE_LAST_CHAR - _POLYLINE_FIRST_CHAR:
            raise ValueError("carattere non valido in un polyline")
        chunks += 1
        if chunks > _MAX_CHUNKS_PER_VALUE:
            raise ValueError("valore troppo grande in un polyline")
        accumulated |= (code & _CHUNK_MASK) << shift
        shift += _CHUNK_BITS
        if code < _CONTINUATION:
            values.append(~(accumulated >> 1) if accumulated & 1 else accumulated >> 1)
            accumulated = shift = chunks = 0
    if chunks:
        raise ValueError("polyline incompleto")
    return values


def decode_polyline(encoded: str) -> tuple[GeoPoint, ...]:
    """Punti di un polyline codificato con `encode_polyline`.

    Raises:
        ValueError: stringa più lunga di `MAX_POLYLINE_CHARS`, caratteri fuori dall'intervallo
            ASCII 63-126, valore o coppia incompleti, valori troppo grandi o coordinate fuori
            intervallo.
    """
    if len(encoded) > MAX_POLYLINE_CHARS:
        raise ValueError("polyline troppo lungo")
    values = _decode_values(encoded)
    if len(values) % 2:
        raise ValueError("polyline incompleto")
    lat = lon = 0
    points: list[GeoPoint] = []
    for d_lat, d_lon in zip(values[0::2], values[1::2], strict=True):
        lat += d_lat
        lon += d_lon
        points.append(GeoPoint(lat / _POLYLINE_SCALE, lon / _POLYLINE_SCALE))
    return tuple(points)
