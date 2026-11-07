# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Riquadro geografico (bbox) con l'antimeridiano: contenimento e riquadro minimo."""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from numpy.typing import ArrayLike, NDArray

_FULL_TURN = 360.0


def _wrap_longitude(lon_deg: float) -> float:
    """Longitudine riportata in [-180, 180] (la mappa mostra copie del mondo).

    Un valore già nei limiti resta esatto: il modulo introdurrebbe errori da 1e-14 gradi.
    """
    if -180.0 <= lon_deg <= 180.0:
        return lon_deg
    return (lon_deg + 180.0) % _FULL_TURN - 180.0


def _clamp_latitude(lat_deg: float) -> float:
    return max(-90.0, min(90.0, lat_deg))


@dataclass(frozen=True, slots=True)
class GeoBox:
    """Riquadro in gradi: `south <= north` in [-90, 90], `west` e `east` in [-180, 180].

    Se `west > east` il riquadro attraversa l'antimeridiano (da `west` verso est fino a 180 e
    da -180 fino a `east`). `west == -180` e `east == 180` è tutto il globo in longitudine. I
    bordi sono inclusi; la longitudine 180 e -180 sono lo stesso meridiano.
    """

    south: float
    west: float
    north: float
    east: float

    def __post_init__(self) -> None:
        if not all(math.isfinite(v) for v in (self.south, self.west, self.north, self.east)):
            raise ValueError("i limiti del riquadro devono essere finiti")
        if not -90.0 <= self.south <= self.north <= 90.0:
            raise ValueError("latitudini del riquadro non valide")
        if not (-180.0 <= self.west <= 180.0 and -180.0 <= self.east <= 180.0):
            raise ValueError("longitudini del riquadro fuori da [-180, 180]")

    @property
    def crosses_antimeridian(self) -> bool:
        """Se il riquadro attraversa l'antimeridiano (ovest maggiore di est)."""
        return self.west > self.east

    @classmethod
    def from_view(cls, south: float, west: float, north: float, east: float) -> GeoBox:
        """Riquadro dall'area inquadrata da una mappa, che può uscire dai limiti del globo.

        Le latitudini si limitano a [-90, 90] (e si scambiano se invertite); una larghezza di
        360 gradi o più è tutto il globo; altrimenti le longitudini tornano in [-180, 180].

        Raises:
            ValueError: valori non finiti o longitudine est minore di quella ovest.
        """
        if not all(math.isfinite(v) for v in (south, west, north, east)):
            raise ValueError("i limiti dell'area devono essere finiti")
        if east < west:
            raise ValueError("l'est dell'area precede l'ovest")
        low, high = sorted((_clamp_latitude(south), _clamp_latitude(north)))
        if east - west >= _FULL_TURN:
            return cls(low, -180.0, high, 180.0)
        return cls(low, _wrap_longitude(west), high, _wrap_longitude(east))

    @classmethod
    def of_points(cls, lat_deg: ArrayLike, lon_deg: ArrayLike) -> GeoBox:
        """Riquadro minimo che contiene i punti, scegliendo il lato corto dell'antimeridiano.

        La longitudine copre il complemento del più largo intervallo senza punti sul cerchio.

        Raises:
            ValueError: nessun punto, colonne di lunghezza diversa, valori non finiti o fuori
                dai limiti.
        """
        lat = np.asarray(lat_deg, dtype=np.float64)
        lon = np.asarray(lon_deg, dtype=np.float64)
        if lat.ndim != 1 or lat.shape != lon.shape or lat.size == 0:
            raise ValueError("servono due colonne non vuote della stessa lunghezza")
        if not (np.isfinite(lat).all() and np.isfinite(lon).all()):
            raise ValueError("le coordinate devono essere finite")
        if (np.abs(lat) > 90.0).any() or (np.abs(lon) > 180.0).any():
            raise ValueError("coordinate fuori dai limiti")
        ordered = np.unique(lon)
        gaps = np.append(np.diff(ordered), ordered[0] + _FULL_TURN - ordered[-1])
        widest = int(np.argmax(gaps))  # a parità vince il primo: deterministico
        if widest == ordered.size - 1:  # il vuoto più largo è quello sull'antimeridiano
            west, east = float(ordered[0]), float(ordered[-1])
        else:
            west, east = float(ordered[widest + 1]), float(ordered[widest])
        return cls(float(lat.min()), west, float(lat.max()), east)

    def contains(self, lat_deg: float, lon_deg: float) -> bool:
        """Se il punto sta nel riquadro (bordi inclusi)."""
        mask = self.contains_many(
            np.array([lat_deg], dtype=np.float64), np.array([lon_deg], dtype=np.float64)
        )
        return bool(mask[0])

    def contains_many(
        self, lat_deg: NDArray[np.float64], lon_deg: NDArray[np.float64]
    ) -> NDArray[np.bool_]:
        """Maschera dei punti nel riquadro, in forma vettoriale."""
        in_lat = (lat_deg >= self.south) & (lat_deg <= self.north)
        # 180 e -180 sono lo stesso meridiano: si prova anche l'equivalente.
        twin = np.where(lon_deg == 180.0, -180.0, np.where(lon_deg == -180.0, 180.0, lon_deg))
        return in_lat & (self._lon_inside(lon_deg) | self._lon_inside(twin))

    def _lon_inside(self, lon_deg: NDArray[np.float64]) -> NDArray[np.bool_]:
        if self.west <= self.east:
            return (lon_deg >= self.west) & (lon_deg <= self.east)
        return (lon_deg >= self.west) | (lon_deg <= self.east)

    def center(self) -> tuple[float, float]:
        """Centro (latitudine, longitudine), anche attraverso l'antimeridiano."""
        width = self.east - self.west + (_FULL_TURN if self.crosses_antimeridian else 0.0)
        return (self.south + self.north) / 2.0, _wrap_longitude(self.west + width / 2.0)
