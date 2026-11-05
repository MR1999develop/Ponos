# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Tipi della frequenza cardiaca: limiti, soglia, sesso, serie di campioni."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import numpy as np
from numpy.typing import NDArray

from ponos_coach.load.domain.sport import Sport

# Intervalli ammessi (default del progetto): scartano valori palesemente errati.
_MIN_BPM_LOW = 20
_MAX_BPM_HIGH = 250
BIKE_THRESHOLD_OFFSET_BPM = 4  # default del progetto: soglia bici = soglia corsa - 4


class Sex(Enum):
    """Sesso del profilo; determina il coefficiente k di Banister."""

    MALE = "male"
    FEMALE = "female"

    @property
    def banister_k(self) -> float:
        """Coefficiente k dell'esponenziale del TRIMP: 1,92 (uomo), 1,67 (donna)."""
        return 1.92 if self is Sex.MALE else 1.67


@dataclass(frozen=True, slots=True)
class HeartRateLimits:
    """FC minima (a riposo) e massima, in bpm; `20 <= min < max <= 250`."""

    min_bpm: int
    max_bpm: int

    def __post_init__(self) -> None:
        if not _MIN_BPM_LOW <= self.min_bpm < self.max_bpm <= _MAX_BPM_HIGH:
            raise ValueError(f"serve {_MIN_BPM_LOW} <= FC minima < FC massima <= {_MAX_BPM_HIGH}")

    @property
    def reserve_bpm(self) -> int:
        """Riserva cardiaca `max - min`, sempre positiva."""
        return self.max_bpm - self.min_bpm


@dataclass(frozen=True, slots=True)
class ThresholdHeartRate:
    """FC di soglia della corsa, in bpm, tra 40 e 250."""

    bpm: int

    def __post_init__(self) -> None:
        if not 40 <= self.bpm <= _MAX_BPM_HIGH:
            raise ValueError("FC di soglia fuori da [40, 250] bpm")


def threshold_for_sport(threshold: ThresholdHeartRate, sport: Sport) -> int:
    """Soglia usata per lo sport: in bici quella della corsa meno 4 bpm, altrimenti uguale."""
    if sport is Sport.CYCLING:
        return threshold.bpm - BIKE_THRESHOLD_OFFSET_BPM
    return threshold.bpm


@dataclass(frozen=True, slots=True)
class Physiology:
    """Parametri fisiologici dell'utente, coerenti tra loro per costruzione.

    La soglia (anche quella in bici) sta strettamente tra FC minima e massima.
    """

    limits: HeartRateLimits
    threshold: ThresholdHeartRate
    sex: Sex

    def __post_init__(self) -> None:
        lowest = self.threshold.bpm - BIKE_THRESHOLD_OFFSET_BPM
        if not self.limits.min_bpm < lowest or not self.threshold.bpm <= self.limits.max_bpm:
            raise ValueError("la soglia deve stare tra FC minima e FC massima")


@dataclass(frozen=True, slots=True, eq=False)
class HeartRateSeries:
    """Campioni di FC: secondi crescenti da un istante di riferimento e bpm, in colonne.

    Invarianti: almeno un campione, stessa lunghezza, valori finiti, secondi strettamente
    crescenti. Gli array sono copiati e di sola lettura. I bpm possono essere fuori limiti:
    li scarta il calcolo del carico, non la serie.
    """

    seconds: NDArray[np.float64]
    bpm: NDArray[np.float64]

    def __post_init__(self) -> None:
        seconds = np.array(self.seconds, dtype=np.float64)
        bpm = np.array(self.bpm, dtype=np.float64)
        if seconds.ndim != 1 or seconds.shape != bpm.shape or seconds.size == 0:
            raise ValueError("serie vuota o con colonne di lunghezza diversa")
        if not (np.isfinite(seconds).all() and np.isfinite(bpm).all()):
            raise ValueError("la serie contiene valori non finiti")
        if seconds.size > 1 and not (np.diff(seconds) > 0).all():
            raise ValueError("i secondi devono essere strettamente crescenti")
        seconds.setflags(write=False)
        bpm.setflags(write=False)
        object.__setattr__(self, "seconds", seconds)
        object.__setattr__(self, "bpm", bpm)

    def __len__(self) -> int:
        return int(self.seconds.size)
