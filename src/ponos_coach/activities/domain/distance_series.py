# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Series of cumulative distance of an activity."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray


@dataclass(frozen=True, slots=True, eq=False)
class DistanceSeries:
    """Cumulative distance: increasing seconds from the same reference instant as the HR series
    and metres covered since the start, in columns.

    Invariants: at least one sample, same length, finite values, strictly increasing seconds,
    metres non-negative and non-decreasing. A file whose distance goes backwards has no valid
    series: it is not repaired (the reader leaves the series out). Arrays are copied and
    read-only.
    """

    seconds: NDArray[np.float64]
    meters: NDArray[np.float64]

    def __post_init__(self) -> None:
        seconds = np.array(self.seconds, dtype=np.float64)
        meters = np.array(self.meters, dtype=np.float64)
        if seconds.ndim != 1 or seconds.shape != meters.shape or seconds.size == 0:
            raise ValueError("empty series or columns of different length")
        if not (np.isfinite(seconds).all() and np.isfinite(meters).all()):
            raise ValueError("the series contains non-finite values")
        if seconds.size > 1 and not (np.diff(seconds) > 0).all():
            raise ValueError("seconds must be strictly increasing")
        if meters[0] < 0 or (meters.size > 1 and not (np.diff(meters) >= 0).all()):
            raise ValueError("the cumulative distance must be non-negative and non-decreasing")
        seconds.setflags(write=False)
        meters.setflags(write=False)
        object.__setattr__(self, "seconds", seconds)
        object.__setattr__(self, "meters", meters)

    def __len__(self) -> int:
        return int(self.seconds.size)
