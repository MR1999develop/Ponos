# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Heart-rate zones on the threshold (five zones, the user's choice of 2026-11-10).

Z1 50-74% of the threshold, Z2 75-84%, Z3 85-94%, Z4 95-102%, Z5 from 103% up to the maximum HR
(the boundaries are whole percentages of the threshold: Z2 starts at 75, Z3 at 85, Z4 at 95, Z5
at 103). Z1 collects everything below Z2, also under 50%. They live only here (the pace zones of
`assessment` have their own, on the speed). The lower boundary belongs to the higher zone
(`bpm >= 75% of the threshold` is Z2). Comparisons are on floating-point values but exact:
`bpm * 100` against `percentage * threshold` (integer), never a floating-point percentage at the
boundary.

Exactness: the decoders give integer bpm (FIT carries integers, from 0 to 255),
so `bpm * 100` and `percentage * threshold` are small integers, represented exactly by
float64, and the comparison at the boundary has no rounding. For a fractional bpm (which no
decoder produces) the product `bpm * 100` would be rounded and a sample one unit in the
last digit from the boundary could fall in the adjacent zone: it is not a supported case.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import ClassVar

import numpy as np
from numpy.typing import NDArray

ZONE_COUNT = 5
# Threshold percentages from which each zone beyond the first starts (Z2, Z3, Z4, Z5).
ZONE_STARTS_PERCENT: tuple[int, ...] = (75, 85, 95, 103)
_PERCENT = 100


@dataclass(frozen=True, slots=True)
class ZoneSeconds:
    """Seconds spent in each of the five zones (Z1 ... Z5), finite and non-negative."""

    ZERO: ClassVar[ZoneSeconds]

    values: tuple[float, float, float, float, float]

    def __post_init__(self) -> None:
        if len(self.values) != ZONE_COUNT:
            raise ValueError(f"{ZONE_COUNT} values are needed, one per zone")
        if not all(math.isfinite(v) and v >= 0 for v in self.values):
            raise ValueError("the seconds in zone must be finite and non-negative")

    @property
    def total(self) -> float:
        """Seconds in all the zones."""
        return math.fsum(self.values)


ZoneSeconds.ZERO = ZoneSeconds((0.0, 0.0, 0.0, 0.0, 0.0))


def zone_starts_bpm(threshold_bpm: int) -> tuple[float, float, float, float]:
    """HR from which Z2, Z3, Z4 and Z5 start for a threshold, in bpm (to draw them)."""
    return (
        threshold_bpm * ZONE_STARTS_PERCENT[0] / _PERCENT,
        threshold_bpm * ZONE_STARTS_PERCENT[1] / _PERCENT,
        threshold_bpm * ZONE_STARTS_PERCENT[2] / _PERCENT,
        threshold_bpm * ZONE_STARTS_PERCENT[3] / _PERCENT,
    )


def zone_bpm_ranges(
    threshold_bpm: int, min_bpm: int, max_bpm: int
) -> tuple[tuple[int, int] | None, ...]:
    """Whole-bpm range `(low, high)` of each zone Z1 ... Z5, both ends included.

    Same rule as `zone_indices`: the lower boundary belongs to the higher zone, so a zone
    starts at the smallest whole bpm with `bpm * 100 >= percentage * threshold` and ends one bpm
    before the next. Z1 starts at `min_bpm` and Z5 ends at `max_bpm`; every range is clamped to
    `[min_bpm, max_bpm]` and a zone left with no whole bpm inside the limits is `None`.
    """
    starts = [min_bpm]
    starts += [-(-p * threshold_bpm // _PERCENT) for p in ZONE_STARTS_PERCENT]  # ceiling
    ends = [start - 1 for start in starts[1:]] + [max_bpm]
    ranges: list[tuple[int, int] | None] = []
    for start, end in zip(starts, ends, strict=True):
        low, high = max(start, min_bpm), min(end, max_bpm)
        ranges.append((low, high) if low <= high else None)
    return tuple(ranges)


def zone_indices(bpm: NDArray[np.float64], threshold_bpm: int) -> NDArray[np.intp]:
    """Zone index (0 for Z1 ... 4 for Z5) of each sample, in vectorized form.

    Finite `bpm`. A sample exactly on the boundary belongs to the higher zone.
    """
    edges = np.array([p * threshold_bpm for p in ZONE_STARTS_PERCENT], dtype=np.float64)
    return np.searchsorted(edges, bpm * _PERCENT, side="right")


def seconds_per_zone(
    bpm: NDArray[np.float64], seconds: NDArray[np.float64], threshold_bpm: int
) -> ZoneSeconds:
    """Sum of the `seconds` of each sample in its zone (same length as `bpm`)."""
    totals = np.bincount(zone_indices(bpm, threshold_bpm), weights=seconds, minlength=ZONE_COUNT)
    z1, z2, z3, z4, z5 = (float(v) for v in totals)
    return ZoneSeconds((z1, z2, z3, z4, z5))
