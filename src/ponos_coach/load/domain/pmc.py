# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Performance Management Chart: ATL, CTL e TSB giornalieri (PLAN.md sezione 4.5)."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np
from numpy.typing import NDArray

ATL_DAYS = 7.0  # costante di tempo di ATL (fatica)
CTL_DAYS = 42.0  # costante di tempo di CTL (forma)


@dataclass(frozen=True, slots=True, eq=False)
class PmcSeries:
    """Serie giornaliere consecutive da `first_day`; vuota se non ci sono giorni.

    `atl` e `ctl` sono i valori a fine giornata; `tsb[i]` è la forma del mattino del giorno
    `i`, cioè `ctl[i-1] - atl[i-1]` (0 per il primo giorno). Array di sola lettura.
    """

    first_day: date
    atl: NDArray[np.float64]
    ctl: NDArray[np.float64]
    tsb: NDArray[np.float64]

    def __post_init__(self) -> None:
        if not self.atl.shape == self.ctl.shape == self.tsb.shape or self.atl.ndim != 1:
            raise ValueError("ATL, CTL e TSB devono avere la stessa lunghezza")
        for array in (self.atl, self.ctl, self.tsb):
            array.setflags(write=False)

    @classmethod
    def empty(cls, first_day: date) -> PmcSeries:
        """Serie senza giorni."""
        nothing = np.zeros(0, dtype=np.float64)
        return cls(first_day, nothing, nothing.copy(), nothing.copy())

    def __len__(self) -> int:
        return int(self.atl.size)

    @property
    def days(self) -> tuple[date, ...]:
        """I giorni della serie, uno per valore."""
        return tuple(self.first_day + timedelta(days=i) for i in range(len(self)))


def _exponential_average(loads: NDArray[np.float64], tau_days: float) -> NDArray[np.float64]:
    """`X(g) = X(g-1) + (L(g) - X(g-1)) / tau`, con X(-1) = 0.

    Ricorrenza sequenziale sui giorni (poche migliaia), non sui campioni: un ciclo è giusto.
    """
    out = np.empty_like(loads)
    value = 0.0
    for index, load in enumerate(loads.tolist()):
        value += (load - value) / tau_days
        out[index] = value
    return out


def compute_pmc(daily_loads: Mapping[date, float], first_day: date, last_day: date) -> PmcSeries:
    """ATL, CTL e TSB per ogni giorno da `first_day` a `last_day` compresi.

    `daily_loads` è il carico (somma delle attività) di ogni giorno con attività; un giorno
    assente vale 0 e i giorni fuori dall'intervallo sono ignorati. Partenza da 0 il giorno
    prima di `first_day`. Funzione pura e deterministica.

    Raises:
        ValueError: se `last_day < first_day` o un carico non è finito e >= 0.
    """
    if last_day < first_day:
        raise ValueError("l'ultimo giorno precede il primo")
    count = (last_day - first_day).days + 1
    loads = np.zeros(count, dtype=np.float64)
    for day, load in daily_loads.items():
        if not math.isfinite(load) or load < 0:
            raise ValueError("il carico di un giorno deve essere finito e >= 0")
        if first_day <= day <= last_day:
            loads[(day - first_day).days] = load
    atl = _exponential_average(loads, ATL_DAYS)
    ctl = _exponential_average(loads, CTL_DAYS)
    tsb = np.zeros(count, dtype=np.float64)
    tsb[1:] = ctl[:-1] - atl[:-1]
    return PmcSeries(first_day, atl, ctl, tsb)
