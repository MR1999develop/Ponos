# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Pace zones on the threshold pace (five zones aligned with the HR zones).

A pace zone is defined on the speed: Z1 below 78% of the threshold speed, Z2 78-88%, Z3 88-94%,
Z4 94-100%, Z5 100% and over (percentages in `defaults`). The lower boundary belongs to the
faster zone, as for the HR zones. Paces are whole seconds per kilometre and the comparison is on
integers: `threshold_pace * 100 >= percentage * pace`, never a floating-point percentage.
"""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray

from ponos_coach.assessment.domain.defaults import PACE_ZONE_STARTS_PERCENT

_PERCENT = 100


def pace_zone_starts_s_per_km(threshold_pace_s_per_km: int) -> tuple[float, float, float, float]:
    """Pace at which Z2, Z3, Z4 and Z5 start, in s/km (to draw and print them).

    From the slowest to the fastest: a pace at or below each value is in that zone or a faster
    one. Z1 is slower than the first value.
    """
    first, second, third, fourth = (
        threshold_pace_s_per_km * _PERCENT / percent for percent in PACE_ZONE_STARTS_PERCENT
    )
    return first, second, third, fourth


def pace_zone_indices(
    pace_s_per_km: NDArray[np.float64], threshold_pace_s_per_km: int
) -> NDArray[np.intp]:
    """Zone index (0 for Z1 ... 4 for Z5) of each pace, in vectorized form.

    Finite, positive `pace_s_per_km`. A pace exactly on a boundary belongs to the faster zone.
    """
    edges = np.array(PACE_ZONE_STARTS_PERCENT, dtype=np.float64)
    # speed share >= p  <=>  threshold_pace * 100 >= p * pace (exact for whole-second paces)
    reached = threshold_pace_s_per_km * _PERCENT >= edges[:, np.newaxis] * pace_s_per_km
    counted: NDArray[np.intp] = np.count_nonzero(reached, axis=0).astype(np.intp)
    return counted
