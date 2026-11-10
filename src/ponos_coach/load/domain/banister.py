# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""TRIMPeq, the program's only measure of load: Banister on the running-equivalent intensity.

For each sample of an activity of sport `s` the reserve ratio `HRr` is brought to a running
equivalent, `HRr_eq = min(1, HRr * HRr_T(run) / HRr_T(s))`, weighted with Banister's
`w = HRr_eq * 0.64 * exp(k * HRr_eq)` and summed over time; the sum is scaled so that one hour at
the threshold of the sport is 100: `load = 100 * sum(dt_min * w) / (60 * w(HRr_T(run)))`.
The threshold of each sport comes from one function, `threshold_for_sport`.
"""

from __future__ import annotations

import math

import numpy as np
from numpy.typing import NDArray

from ponos_coach.activities.domain import Sport
from ponos_coach.load.domain.heart_rate import Physiology, threshold_for_sport

BANISTER_COEFFICIENT = 0.64
POINTS_PER_THRESHOLD_HOUR = 100.0  # the scale: 100 = one hour at the threshold of the sport
_MINUTES_PER_HOUR = 60.0


def trimp_per_minute(hrr: float, k: float) -> float:
    """One-minute Banister weight at reserve ratio `hrr` (between 0 and 1)."""
    return hrr * BANISTER_COEFFICIENT * math.exp(k * hrr)


def threshold_reserve_ratio(physiology: Physiology, sport: Sport) -> float:
    """`HRr_T(sport)`: the threshold of the sport as a ratio of the heart-rate reserve."""
    limits = physiology.limits
    return (threshold_for_sport(physiology, sport) - limits.min_bpm) / limits.reserve_bpm


def equivalence_factor(physiology: Physiology, sport: Sport) -> float:
    """`HRr_T(run) / HRr_T(sport)`: 1 for running and the sports with no threshold of their own."""
    return threshold_reserve_ratio(physiology, Sport.RUNNING) / threshold_reserve_ratio(
        physiology, sport
    )


def equivalent_reserve_ratio(
    hrr: NDArray[np.float64], physiology: Physiology, sport: Sport
) -> NDArray[np.float64]:
    """Running-equivalent ratios `min(1, HRr * HRr_T(run) / HRr_T(sport))`; 0 stays 0."""
    return np.minimum(1.0, hrr * equivalence_factor(physiology, sport))


def reference_hour_weight(physiology: Physiology) -> float:
    """`60 * w(HRr_T(run))`: the weight of one hour at the running threshold, the 100 of the
    scale."""
    hrr = threshold_reserve_ratio(physiology, Sport.RUNNING)
    return _MINUTES_PER_HOUR * trimp_per_minute(hrr, physiology.sex.banister_k)
