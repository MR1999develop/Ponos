# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Time below LT1, between LT1 and LT2, and above LT2 (the three intensity domains).

LT1 is the aerobic threshold and LT2 the lactate threshold, both in whole bpm. Boundaries, exact
because bpm are integers: below LT1 is `bpm < LT1`, between is `LT1 <= bpm < LT2`, above is
`bpm >= LT2` (a sample on a threshold belongs to the higher domain, like the zones' lower
boundary). Vectorized, no per-sample loops.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

_DOMAIN_COUNT = 3


@dataclass(frozen=True, slots=True)
class DomainSeconds:
    """Seconds below LT1, between LT1 and LT2, and above LT2; finite and non-negative."""

    below_lt1: float
    between: float
    above_lt2: float

    def __post_init__(self) -> None:
        if not all(math.isfinite(v) and v >= 0 for v in self.as_tuple()):
            raise ValueError("the seconds in a domain must be finite and non-negative")

    def as_tuple(self) -> tuple[float, float, float]:
        """The three values, in order."""
        return (self.below_lt1, self.between, self.above_lt2)

    @property
    def total(self) -> float:
        """Seconds in the three domains."""
        return math.fsum(self.as_tuple())


def seconds_per_domain(
    bpm: NDArray[np.float64], seconds: NDArray[np.float64], lt1_bpm: int, lt2_bpm: int
) -> DomainSeconds:
    """Sum of the `seconds` of each sample in its domain (same length as `bpm`).

    Raises:
        ValueError: if `lt1_bpm > lt2_bpm`.
    """
    if lt1_bpm > lt2_bpm:
        raise ValueError("LT1 must not be above LT2")
    edges = np.array([lt1_bpm, lt2_bpm], dtype=np.float64)
    index = np.searchsorted(edges, bpm, side="right")
    totals = np.bincount(index, weights=seconds, minlength=_DOMAIN_COUNT)
    below, between, above = (float(v) for v in totals)
    return DomainSeconds(below, between, above)
