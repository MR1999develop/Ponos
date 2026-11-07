# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Traccia completa di un'attività: posizioni e quota sull'asse dei secondi della FC.

La traccia completa non si salva nell'indice (ci sta la polyline semplificata di
`ActivitySummary`): si rilegge dal file archiviato quando serve. Le posizioni sono dati
personali sensibili: mai nei log. Un buco di posizione (record senza GPS, tratto GPX diverso o
pausa più lunga di `TRACK_GAP_S`) spezza la traccia in tratti: tra due tratti non si disegna
nessun segmento.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from ponos_coach.activities.domain.geo import (
    EARTH_RADIUS_M,
    MAX_POLYLINE_CHARS,
    GeoPoint,
    decode_polyline,
    encode_polyline,
    simplify_indices,
)
from ponos_coach.activities.domain.geo_box import GeoBox
from ponos_coach.activities.domain.limits import (
    MAX_RAW_TRACK_POINTS,
    MAX_TRACK_POINTS,
    MAX_TRACK_ZOOM,
    TRACK_GAP_S,
)

_TILE_PIXELS = 256  # lato di una tile di mappa
_SEGMENT_SEPARATOR = " "  # fuori dall'alfabeto dei polyline (ASCII 63-126)
_MAX_TOLERANCE_DOUBLINGS = 24
_MIN_SEGMENT_POINTS = 2


def _frozen(values: object) -> NDArray[np.float64]:
    array = np.array(values, dtype=np.float64)
    array.setflags(write=False)
    return array


@dataclass(frozen=True, slots=True, eq=False)
class TrackSegment:
    """Un tratto continuo: secondi dal primo record (come la serie di FC), posizione, quota.

    Invarianti: almeno un punto; colonne della stessa lunghezza; secondi finiti e strettamente
    crescenti; latitudine in [-90, 90] e longitudine in [-180, 180], finite; la quota, se c'è,
    in metri, `NaN` dove manca ma mai infinita. Gli array sono copiati e di sola lettura.
    """

    seconds: NDArray[np.float64]
    lat_deg: NDArray[np.float64]
    lon_deg: NDArray[np.float64]
    altitude_m: NDArray[np.float64] | None = None

    def __post_init__(self) -> None:
        seconds, lat, lon = _frozen(self.seconds), _frozen(self.lat_deg), _frozen(self.lon_deg)
        altitude = None if self.altitude_m is None else _frozen(self.altitude_m)
        if seconds.ndim != 1 or seconds.size == 0 or not (seconds.shape == lat.shape == lon.shape):
            raise ValueError("tratto vuoto o con colonne di lunghezza diversa")
        if altitude is not None and altitude.shape != seconds.shape:
            raise ValueError("la quota ha una lunghezza diversa dalle posizioni")
        if not (np.isfinite(seconds).all() and np.isfinite(lat).all() and np.isfinite(lon).all()):
            raise ValueError("secondi e coordinate devono essere finiti")
        if (np.abs(lat) > 90.0).any() or (np.abs(lon) > 180.0).any():
            raise ValueError("coordinate fuori dai limiti")
        if seconds.size > 1 and not (np.diff(seconds) > 0).all():
            raise ValueError("i secondi devono essere strettamente crescenti")
        if altitude is not None and np.isinf(altitude).any():
            raise ValueError("la quota non può essere infinita")
        object.__setattr__(self, "seconds", seconds)
        object.__setattr__(self, "lat_deg", lat)
        object.__setattr__(self, "lon_deg", lon)
        object.__setattr__(self, "altitude_m", altitude)

    def __len__(self) -> int:
        return int(self.seconds.size)


@dataclass(frozen=True, slots=True, eq=False)
class ActivityTrack:
    """Tratti della traccia di un'attività, in ordine di tempo.

    Invarianti: almeno un tratto; al massimo `MAX_RAW_TRACK_POINTS` punti in tutto; i tratti
    non si sovrappongono nel tempo (il primo secondo di un tratto segue l'ultimo del precedente).
    """

    segments: tuple[TrackSegment, ...]

    def __post_init__(self) -> None:
        if not self.segments:
            raise ValueError("una traccia ha almeno un tratto")
        if sum(len(s) for s in self.segments) > MAX_RAW_TRACK_POINTS:
            raise ValueError("troppi punti nella traccia")
        for before, after in zip(self.segments, self.segments[1:], strict=False):
            if after.seconds[0] <= before.seconds[-1]:
                raise ValueError("i tratti si sovrappongono nel tempo")

    @property
    def point_count(self) -> int:
        """Numero di punti in tutti i tratti."""
        return sum(len(s) for s in self.segments)

    def bounding_box(self) -> GeoBox:
        """Riquadro minimo della traccia (antimeridiano compreso)."""
        lat = np.concatenate([s.lat_deg for s in self.segments])
        lon = np.concatenate([s.lon_deg for s in self.segments])
        return GeoBox.of_points(lat, lon)


def build_track(
    seconds: NDArray[np.float64],
    lat_deg: NDArray[np.float64],
    lon_deg: NDArray[np.float64],
    altitude_m: NDArray[np.float64] | None,
    run_id: NDArray[np.intp],
    max_gap_s: float = TRACK_GAP_S,
) -> ActivityTrack | None:
    """Traccia dalle colonne dei soli punti con posizione valida, o `None` se non ce ne sono.

    `run_id` identifica il tratto d'origine di ogni punto (per esempio il `trkseg` di un GPX,
    o il numero di buchi di posizione già visti in un FIT): cambiando, il tratto si spezza.
    Si spezza anche quando due punti consecutivi distano più di `max_gap_s` secondi. Un punto
    con secondi non maggiori di tutti i precedenti (ripetuto o all'indietro) si scarta, come
    per la serie di FC: vale il primo di una ripetizione.

    Raises:
        ValueError: colonne di lunghezza diversa o valori non validi per un `TrackSegment`.
    """
    if not (seconds.shape == lat_deg.shape == lon_deg.shape == run_id.shape):
        raise ValueError("colonne di lunghezza diversa")
    if seconds.size == 0:
        return None
    keep = np.ones(seconds.size, dtype=bool)
    keep[1:] = seconds[1:] > np.maximum.accumulate(seconds)[:-1]
    seconds, lat_deg, lon_deg, run_id = seconds[keep], lat_deg[keep], lon_deg[keep], run_id[keep]
    altitude = None if altitude_m is None else altitude_m[keep]
    breaks = (run_id[1:] != run_id[:-1]) | (np.diff(seconds) > max_gap_s)
    starts = np.concatenate(([0], np.flatnonzero(breaks) + 1, [seconds.size]))
    segments = tuple(
        TrackSegment(
            seconds[a:b], lat_deg[a:b], lon_deg[a:b], None if altitude is None else altitude[a:b]
        )
        for a, b in zip(starts[:-1].tolist(), starts[1:].tolist(), strict=True)
    )
    return ActivityTrack(segments)


def zoom_tolerance_m(zoom: int, lat_deg: float) -> float:
    """Tolleranza di semplificazione per un livello di zoom: un pixel della mappa, in metri.

    Metri per pixel di una mappa a tile da 256 px alla latitudine data (proiezione di
    Web Mercator): `2π R cos(lat) / (256 · 2^zoom)`. Lo zoom si limita a [0, `MAX_TRACK_ZOOM`];
    vicino ai poli la tolleranza non scende sotto quella a 85 gradi.

    Raises:
        ValueError: latitudine non finita.
    """
    if not math.isfinite(lat_deg):
        raise ValueError("la latitudine deve essere finita")
    level = max(0, min(MAX_TRACK_ZOOM, zoom))
    scale = math.cos(math.radians(min(abs(lat_deg), 85.0)))
    return 2.0 * math.pi * EARTH_RADIUS_M * scale / (_TILE_PIXELS * 2.0**level)


def _simplified_segments(track: ActivityTrack, tolerance_m: float) -> list[tuple[GeoPoint, ...]]:
    result: list[tuple[GeoPoint, ...]] = []
    for segment in track.segments:
        kept = simplify_indices(segment.lat_deg, segment.lon_deg, tolerance_m, MAX_TRACK_POINTS)
        if len(kept) >= _MIN_SEGMENT_POINTS:  # un punto solo non disegna nulla
            result.append(
                tuple(GeoPoint(float(segment.lat_deg[i]), float(segment.lon_deg[i])) for i in kept)
            )
    return result


def _fit_budget(segments: list[tuple[GeoPoint, ...]]) -> list[tuple[GeoPoint, ...]]:
    """Tiene i tratti più lunghi finché il totale sta in `MAX_TRACK_POINTS` (ordine di tempo)."""
    order = sorted(range(len(segments)), key=lambda i: (-len(segments[i]), i))
    chosen: set[int] = set()
    total = 0
    for index in order:
        if total + len(segments[index]) <= MAX_TRACK_POINTS:
            chosen.add(index)
            total += len(segments[index])
    return [segments[i] for i in sorted(chosen)]


def simplify_for_zoom(track: ActivityTrack, zoom: int) -> tuple[tuple[GeoPoint, ...], ...]:
    """Tratti semplificati (Douglas-Peucker) con la tolleranza di un pixel al livello di zoom.

    Ogni tratto tiene primo e ultimo punto; i tratti con un solo punto sono omessi. Se il
    totale supera `MAX_TRACK_POINTS` la tolleranza raddoppia fino a farcelo stare (al più 24
    volte), poi si tengono i tratti più lunghi: il risultato è sempre codificabile con
    `encode_segments`. Deterministica; una traccia che sparisce del tutto dà una tupla vuota.
    """
    lat = float(np.mean(np.concatenate([s.lat_deg for s in track.segments])))
    tolerance = zoom_tolerance_m(zoom, lat)
    segments = _simplified_segments(track, tolerance)
    for _ in range(_MAX_TOLERANCE_DOUBLINGS):
        if sum(len(s) for s in segments) <= MAX_TRACK_POINTS:
            break
        tolerance *= 2.0
        segments = _simplified_segments(track, tolerance)
    return tuple(_fit_budget(segments))


def encode_segments(segments: Sequence[Sequence[GeoPoint]]) -> str:
    """Codifica compatta dei tratti: un polyline (1e-5 gradi, circa 1 m) per tratto, separati
    da uno spazio. Nessun tratto dà la stringa vuota.

    Raises:
        ValueError: più di `MAX_TRACK_POINTS` punti in tutto o un tratto vuoto.
    """
    if sum(len(s) for s in segments) > MAX_TRACK_POINTS:
        raise ValueError("troppi punti per una traccia codificata")
    if any(len(s) == 0 for s in segments):
        raise ValueError("tratto vuoto")
    return _SEGMENT_SEPARATOR.join(encode_polyline(s) for s in segments)


def decode_segments(encoded: str) -> tuple[tuple[GeoPoint, ...], ...]:
    """Tratti di una stringa prodotta da `encode_segments` (valida l'input come un confine).

    Raises:
        ValueError: stringa troppo lunga, tratto vuoto, caratteri o valori non validi, più di
            `MAX_TRACK_POINTS` punti in tutto.
    """
    if len(encoded) > MAX_POLYLINE_CHARS + MAX_TRACK_POINTS:
        raise ValueError("traccia codificata troppo lunga")
    if not encoded:
        return ()
    segments = tuple(decode_polyline(part) for part in encoded.split(_SEGMENT_SEPARATOR))
    if any(len(s) == 0 for s in segments):
        raise ValueError("tratto vuoto")
    if sum(len(s) for s in segments) > MAX_TRACK_POINTS:
        raise ValueError("troppi punti in una traccia codificata")
    return segments
