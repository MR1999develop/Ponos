# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""The PMC projected over planned loads: the same exponential averages as `load.domain.pmc`,
but starting from a known state instead of zero."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from ponos_coach.load.domain.pmc import ATL_DAYS, CTL_DAYS


@dataclass(frozen=True, slots=True)
class Fitness:
    """End-of-day CTL and ATL."""

    ctl: float
    atl: float

    @property
    def tsb(self) -> float:
        """The form of the next morning: CTL minus ATL."""
        return self.ctl - self.atl


def step(fitness: Fitness, load: float) -> Fitness:
    """CTL and ATL after a day of `load`: `X = X + (load - X) / tau`."""
    return Fitness(
        fitness.ctl + (load - fitness.ctl) / CTL_DAYS,
        fitness.atl + (load - fitness.atl) / ATL_DAYS,
    )


def ctl_after(ctl: float, loads: Sequence[float]) -> float:
    """CTL after the daily `loads`, starting from `ctl`."""
    for load in loads:
        ctl += (load - ctl) / CTL_DAYS
    return ctl


def decay(days: int) -> float:
    """Share of today's CTL that is left after `days` days with no load: `(1 - 1/42) ** days`."""
    return (1.0 - 1.0 / CTL_DAYS) ** days
