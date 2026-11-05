# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Serie di campioni di frequenza cardiaca di un'attivita."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray


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
