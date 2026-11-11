# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Series of heart-rate samples of an activity."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray


@dataclass(frozen=True, slots=True, eq=False)
class HeartRateSeries:
    """HR samples: increasing seconds from a reference instant and bpm, in columns.

    Invariants: at least one sample, same length, finite values, strictly
    increasing seconds. Arrays are copied and read-only. The bpm may be out of range:
    the load computation discards them, not the series.
    """

    seconds: NDArray[np.float64]
    bpm: NDArray[np.float64]

    def __post_init__(self) -> None:
        seconds = np.array(self.seconds, dtype=np.float64)
        bpm = np.array(self.bpm, dtype=np.float64)
        if seconds.ndim != 1 or seconds.shape != bpm.shape or seconds.size == 0:
            raise ValueError("empty series or columns of different length")
        if not (np.isfinite(seconds).all() and np.isfinite(bpm).all()):
            raise ValueError("the series contains non-finite values")
        if seconds.size > 1 and not (np.diff(seconds) > 0).all():
            raise ValueError("seconds must be strictly increasing")
        seconds.setflags(write=False)
        bpm.setflags(write=False)
        object.__setattr__(self, "seconds", seconds)
        object.__setattr__(self, "bpm", bpm)

    def __len__(self) -> int:
        return int(self.seconds.size)
