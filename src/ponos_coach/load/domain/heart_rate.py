# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Tipi della frequenza cardiaca: limiti, soglia, sesso."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from ponos_coach.activities.domain import Sport

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


def threshold_for_sport(physiology: Physiology, sport: Sport) -> int:
    """Soglia usata per lo sport.

    In bici è `max(soglia_corsa - 4, FC_minima + 1)`: il valore si porta al limite invece di
    rifiutare i parametri (default del progetto); negli altri sport è la soglia della corsa.
    """
    threshold = physiology.threshold.bpm
    if sport is Sport.CYCLING:
        return max(threshold - BIKE_THRESHOLD_OFFSET_BPM, physiology.limits.min_bpm + 1)
    return threshold


@dataclass(frozen=True, slots=True)
class Physiology:
    """Parametri fisiologici dell'utente, coerenti tra loro per costruzione.

    La soglia della corsa sta tra FC minima (esclusa) e FC massima (inclusa); quella della
    bici si deriva con `threshold_for_sport` e non vincola il costruttore.
    """

    limits: HeartRateLimits
    threshold: ThresholdHeartRate
    sex: Sex

    def __post_init__(self) -> None:
        if not self.limits.min_bpm < self.threshold.bpm <= self.limits.max_bpm:
            raise ValueError("la soglia deve stare tra FC minima e FC massima")
