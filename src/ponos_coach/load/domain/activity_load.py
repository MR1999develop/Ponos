# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Carico di una singola attività: TRIMP di Banister, hrTSS e carico pesato.

Formule in `PLAN.md` sezione 4.5. Il calcolo è vettoriale (NumPy), senza cicli per campione.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum

import numpy as np
from numpy.typing import NDArray

from ponos_coach.activities.domain import HeartRateSeries, Sport
from ponos_coach.load.domain.heart_rate import Physiology, threshold_for_sport
from ponos_coach.load.domain.sport import SportWeight, typical_intensity

MAX_GAP_S = 10.0  # default del progetto: oltre questo intervallo tra campioni, non si conta
MAX_BAD_FRACTION = 0.30  # default del progetto: oltre, il carico è una stima
_SINGLE_SAMPLE_S = 1.0  # validità di un campione unico (nessun intervallo da cui dedurla)
_BANISTER_COEFFICIENT = 0.64
_MS_PER_S = 1000
_PERCENT_DENOMINATOR = 100
_GOOD_PERCENT = round((1.0 - MAX_BAD_FRACTION) * _PERCENT_DENOMINATOR)  # 70


class LoadSource(Enum):
    """Da dove viene il carico: dalla FC misurata o stimato dalla durata."""

    HEART_RATE = "heart_rate"
    ESTIMATE = "estimate"


@dataclass(frozen=True, slots=True)
class ActivityLoad:
    """Esito del calcolo del carico di un'attività.

    `used_samples` sono i campioni contati (0 per una stima); `discarded_samples` quelli con
    FC fuori da `[min, max]`; `excluded_seconds` i secondi di intervalli oltre 10 s non contati.
    """

    trimp: float
    hr_tss: float
    load: float
    used_samples: int
    discarded_samples: int
    excluded_seconds: float
    source: LoadSource


def trimp_per_minute(hrr: float, k: float) -> float:
    """TRIMP di un minuto alla riserva cardiaca relativa `hrr` (tra 0 e 1)."""
    return hrr * _BANISTER_COEFFICIENT * math.exp(k * hrr)


def hour_at_threshold_trimp(sport: Sport, physiology: Physiology) -> float:
    """TRIMP di un'ora alla FC di soglia dello sport (in bici: vedi `threshold_for_sport`)."""
    limits = physiology.limits
    threshold = threshold_for_sport(physiology, sport)
    hrr = (threshold - limits.min_bpm) / limits.reserve_bpm
    return 60.0 * trimp_per_minute(hrr, physiology.sex.banister_k)


def _sample_validity_s(seconds: NDArray[np.float64]) -> NDArray[np.float64]:
    """Secondi di validità di ogni campione: fino al successivo; l'ultimo vale la mediana."""
    if seconds.size == 1:
        return np.array([_SINGLE_SAMPLE_S])
    gaps = np.diff(seconds)
    return np.append(gaps, np.median(gaps))


def _estimated(
    sport: Sport,
    moving_time_s: float,
    physiology: Physiology,
    weight: SportWeight,
    discarded: int,
    excluded_s: float,
) -> ActivityLoad:
    hr_tss = moving_time_s / 3600.0 * 100.0 * typical_intensity(sport) ** 2
    trimp = hr_tss / 100.0 * hour_at_threshold_trimp(sport, physiology)
    return ActivityLoad(
        trimp, hr_tss, hr_tss * weight.value, 0, discarded, excluded_s, LoadSource.ESTIMATE
    )


def _is_mostly_missing(counted_s: float, moving_time_s: float) -> bool:
    """Vero se i secondi contati sono meno del 70% del tempo in movimento (millisecondi interi)."""
    counted_ms = round(counted_s * _MS_PER_S)
    moving_ms = round(moving_time_s * _MS_PER_S)
    return counted_ms * _PERCENT_DENOMINATOR < _GOOD_PERCENT * moving_ms


def compute_activity_load(
    sport: Sport,
    moving_time_s: float,
    series: HeartRateSeries | None,
    physiology: Physiology,
    weight: SportWeight,
) -> ActivityLoad:
    """Calcola TRIMP, hrTSS e carico pesato di un'attività.

    Ogni campione vale fino al successivo (l'ultimo, la mediana degli intervalli); i campioni
    con FC fuori dai limiti si scartano e si contano; gli intervalli oltre 10 s non si contano.
    `moving_time_s` è il tempo in movimento (timer dell'attività), non il tempo trascorso, che
    include le pause. Se i secondi contati sono meno del 70% del tempo in movimento (cioè oltre
    il 30% scartato o mancante) o non c'è FC, il carico è la stima `durata_h * 100 * IF^2` con
    l'intensità tipica dello sport, marcata `LoadSource.ESTIMATE`. Il confronto col 70% è in
    millisecondi interi, senza virgola mobile al limite. Funzione pura e deterministica.

    Raises:
        ValueError: se `moving_time_s` non è finito e positivo.
    """
    if not math.isfinite(moving_time_s) or moving_time_s <= 0:
        raise ValueError("la durata deve essere finita e positiva")
    if series is None:
        return _estimated(sport, moving_time_s, physiology, weight, 0, 0.0)

    limits = physiology.limits
    validity = _sample_validity_s(series.seconds)
    in_range = (series.bpm >= limits.min_bpm) & (series.bpm <= limits.max_bpm)
    counted = in_range & (validity <= MAX_GAP_S)
    discarded = int(np.count_nonzero(~in_range))
    excluded_s = float(validity[in_range & ~counted].sum())
    counted_s = float(validity[counted].sum())
    if _is_mostly_missing(counted_s, moving_time_s):
        return _estimated(sport, moving_time_s, physiology, weight, discarded, excluded_s)

    hrr = (series.bpm[counted] - limits.min_bpm) / limits.reserve_bpm
    per_minute = hrr * _BANISTER_COEFFICIENT * np.exp(physiology.sex.banister_k * hrr)
    trimp = float(np.sum(validity[counted] / 60.0 * per_minute))
    hr_tss = 100.0 * trimp / hour_at_threshold_trimp(sport, physiology)
    return ActivityLoad(
        trimp,
        hr_tss,
        hr_tss * weight.value,
        int(np.count_nonzero(counted)),
        discarded,
        excluded_s,
        LoadSource.HEART_RATE,
    )
