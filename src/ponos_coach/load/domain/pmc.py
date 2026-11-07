# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Performance Management Chart: daily ATL, CTL and TSB (PLAN.md section 4.5)."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np
from numpy.typing import NDArray

ATL_DAYS = 7.0  # time constant of ATL (fatigue)
CTL_DAYS = 42.0  # time constant of CTL (fitness)


@dataclass(frozen=True, slots=True, eq=False)
class PmcSeries:
    """Consecutive daily series from `first_day`; empty if there are no days.

    `atl` and `ctl` are the end-of-day values; `tsb[i]` is the morning form of day
    `i`, i.e. `ctl[i-1] - atl[i-1]` (0 for the first day). Read-only arrays.
    """

    first_day: date
    atl: NDArray[np.float64]
    ctl: NDArray[np.float64]
    tsb: NDArray[np.float64]

    def __post_init__(self) -> None:
        if not self.atl.shape == self.ctl.shape == self.tsb.shape or self.atl.ndim != 1:
            raise ValueError("ATL, CTL and TSB must have the same length")
        for array in (self.atl, self.ctl, self.tsb):
            array.setflags(write=False)

    @classmethod
    def empty(cls, first_day: date) -> PmcSeries:
        """Series with no days."""
        nothing = np.zeros(0, dtype=np.float64)
        return cls(first_day, nothing, nothing.copy(), nothing.copy())

    def __len__(self) -> int:
        return int(self.atl.size)

    @property
    def days(self) -> tuple[date, ...]:
        """The days of the series, one per value."""
        return tuple(self.first_day + timedelta(days=i) for i in range(len(self)))


def _exponential_average(loads: NDArray[np.float64], tau_days: float) -> NDArray[np.float64]:
    """`X(d) = X(d-1) + (L(d) - X(d-1)) / tau`, with X(-1) = 0.

    Sequential recurrence over days (a few thousand), not over samples: a loop is right.
    """
    out = np.empty_like(loads)
    value = 0.0
    for index, load in enumerate(loads.tolist()):
        value += (load - value) / tau_days
        out[index] = value
    return out


def compute_pmc(daily_loads: Mapping[date, float], first_day: date, last_day: date) -> PmcSeries:
    """ATL, CTL and TSB for each day from `first_day` to `last_day` inclusive.

    `daily_loads` is the load (sum of the activities) of each day with activities; an absent
    day is worth 0 and days outside the range are ignored. Start from 0 the day
    before `first_day`. Pure and deterministic function.

    Raises:
        ValueError: if `last_day < first_day` or a load is not finite and >= 0.
    """
    if last_day < first_day:
        raise ValueError("the last day precedes the first")
    count = (last_day - first_day).days + 1
    loads = np.zeros(count, dtype=np.float64)
    for day, load in daily_loads.items():
        if not math.isfinite(load) or load < 0:
            raise ValueError("a day's load must be finite and >= 0")
        if first_day <= day <= last_day:
            loads[(day - first_day).days] = load
    atl = _exponential_average(loads, ATL_DAYS)
    ctl = _exponential_average(loads, CTL_DAYS)
    tsb = np.zeros(count, dtype=np.float64)
    tsb[1:] = ctl[:-1] - atl[:-1]
    return PmcSeries(first_day, atl, ctl, tsb)
