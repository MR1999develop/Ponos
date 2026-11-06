# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Zone di frequenza cardiaca sulla soglia (cinque zone, secondo Friel).

Z1 sotto l'85% della soglia, Z2 85-89%, Z3 90-94%, Z4 95-99%, Z5 dal 100% in su. Percentuali
di default del progetto, rivedute in fase 4; stanno solo qui. Il confine inferiore appartiene
alla zona più alta (`bpm >= 85% della soglia` è Z2). I confronti sono su interi: `bpm * 100`
contro `percentuale * soglia`, mai una percentuale in virgola mobile al limite.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import ClassVar

import numpy as np
from numpy.typing import NDArray

ZONE_COUNT = 5
# Percentuali della soglia da cui comincia ciascuna zona oltre la prima (Z2, Z3, Z4, Z5).
ZONE_STARTS_PERCENT: tuple[int, ...] = (85, 90, 95, 100)
_PERCENT = 100


@dataclass(frozen=True, slots=True)
class ZoneSeconds:
    """Secondi trascorsi in ciascuna delle cinque zone (Z1 ... Z5), finiti e non negativi."""

    ZERO: ClassVar[ZoneSeconds]

    values: tuple[float, float, float, float, float]

    def __post_init__(self) -> None:
        if len(self.values) != ZONE_COUNT:
            raise ValueError(f"servono {ZONE_COUNT} valori, uno per zona")
        if not all(math.isfinite(v) and v >= 0 for v in self.values):
            raise ValueError("i secondi in zona devono essere finiti e non negativi")

    @property
    def total(self) -> float:
        """Secondi in tutte le zone."""
        return math.fsum(self.values)


ZoneSeconds.ZERO = ZoneSeconds((0.0, 0.0, 0.0, 0.0, 0.0))


def zone_starts_bpm(threshold_bpm: int) -> tuple[float, float, float, float]:
    """FC da cui cominciano Z2, Z3, Z4 e Z5 per una soglia, in bpm (per disegnarle)."""
    return (
        threshold_bpm * ZONE_STARTS_PERCENT[0] / _PERCENT,
        threshold_bpm * ZONE_STARTS_PERCENT[1] / _PERCENT,
        threshold_bpm * ZONE_STARTS_PERCENT[2] / _PERCENT,
        threshold_bpm * ZONE_STARTS_PERCENT[3] / _PERCENT,
    )


def zone_indices(bpm: NDArray[np.float64], threshold_bpm: int) -> NDArray[np.intp]:
    """Indice di zona (0 per Z1 ... 4 per Z5) di ogni campione, in modo vettoriale.

    `bpm` finiti. Un campione esattamente sul confine appartiene alla zona più alta.
    """
    edges = np.array([p * threshold_bpm for p in ZONE_STARTS_PERCENT], dtype=np.float64)
    return np.searchsorted(edges, bpm * _PERCENT, side="right")


def seconds_per_zone(
    bpm: NDArray[np.float64], seconds: NDArray[np.float64], threshold_bpm: int
) -> ZoneSeconds:
    """Somma dei `seconds` di ogni campione nella sua zona (stessa lunghezza di `bpm`)."""
    totals = np.bincount(zone_indices(bpm, threshold_bpm), weights=seconds, minlength=ZONE_COUNT)
    z1, z2, z3, z4, z5 = (float(v) for v in totals)
    return ZoneSeconds((z1, z2, z3, z4, z5))
