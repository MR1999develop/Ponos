# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Attività con la partenza in un'area della mappa, con i totali per l'elenco a destra."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from ponos_coach.activities.domain.geo_box import GeoBox
from ponos_coach.activities.domain.record import ActivityRecord
from ponos_coach.activities.domain.track import decode_segments

_SECONDS_PER_HOUR = 3600.0
_METERS_PER_KM = 1000.0


@dataclass(frozen=True, slots=True)
class AreaTotals:
    """Totali di un gruppo di attività: numero, chilometri, dislivello (m) e ore in movimento.

    Distanza e dislivello sommano solo le attività che li dichiarano.
    """

    count: int
    distance_km: float
    ascent_m: float
    moving_hours: float

    @classmethod
    def of(cls, records: Sequence[ActivityRecord]) -> AreaTotals:
        """Totali delle attività date (somma esatta con `math.fsum`)."""
        distance = math.fsum(r.summary.distance_m or 0.0 for r in records)
        ascent = math.fsum(r.summary.ascent_m or 0.0 for r in records)
        moving = math.fsum(r.timer_s for r in records)
        return cls(len(records), distance / _METERS_PER_KM, ascent, moving / _SECONDS_PER_HOUR)


@dataclass(frozen=True, slots=True)
class AreaResult:
    """Esito di una ricerca per area: attività nell'area, totali e attività senza posizione.

    `activities` conserva l'ordine d'ingresso. `without_position` conta, tra le attività date,
    quelle che non hanno una posizione di partenza (indoor, forza): non stanno sulla mappa,
    qualunque sia l'area, e non sono comprese in `activities` né nei totali.
    """

    activities: tuple[ActivityRecord, ...]
    totals: AreaTotals
    without_position: int


class StartIndex:
    """Posizioni di partenza di un insieme di attività, in colonne, per interrogarle in fretta.

    Si costruisce una volta (per esempio a ogni cambio di filtro) e si interroga a ogni
    spostamento della mappa: la maschera dell'area è vettoriale, senza cicli per attività.
    Immutabile dopo la costruzione.
    """

    def __init__(self, records: Sequence[ActivityRecord]) -> None:
        self._records = tuple(records)
        located: list[int] = []
        lat: list[float] = []
        lon: list[float] = []
        for index, record in enumerate(self._records):
            point = record.summary.start_point
            if point is not None:
                located.append(index)
                lat.append(point.lat_deg)
                lon.append(point.lon_deg)
        self._located = np.array(located, dtype=np.intp)
        self._lat = np.array(lat, dtype=np.float64)
        self._lon = np.array(lon, dtype=np.float64)
        self._without_position = len(self._records) - len(located)

    @property
    def without_position(self) -> int:
        """Attività senza posizione di partenza."""
        return self._without_position

    def mask_in(self, area: GeoBox) -> NDArray[np.bool_]:
        """Maschera, sulle sole attività con posizione, di quelle che partono nell'area."""
        return area.contains_many(self._lat, self._lon)

    def in_area(self, area: GeoBox) -> AreaResult:
        """Le attività che partono nell'area (bordi inclusi), con i totali."""
        chosen = self._located[self.mask_in(area)]
        found = tuple(self._records[int(i)] for i in chosen)
        return AreaResult(found, AreaTotals.of(found), self._without_position)


def activities_in_area(records: Sequence[ActivityRecord], area: GeoBox) -> AreaResult:
    """Attività (già filtrate) con la partenza nell'area, con totali e conteggio senza posizione.

    Per interrogare più aree sullo stesso insieme conviene costruire un `StartIndex`.
    """
    return StartIndex(records).in_area(area)


def track_touches_area(track_polyline: str, area: GeoBox) -> bool:
    """Se almeno un punto della traccia semplificata (polyline) cade nell'area.

    Per la ricerca "con partenza o traccia nell'area" sulle poche attività vicine al bordo.

    Raises:
        ValueError: la traccia non è un polyline valido.
    """
    for segment in decode_segments(track_polyline):
        lat = np.array([p.lat_deg for p in segment], dtype=np.float64)
        lon = np.array([p.lon_deg for p in segment], dtype=np.float64)
        if area.contains_many(lat, lon).any():
            return True
    return False
