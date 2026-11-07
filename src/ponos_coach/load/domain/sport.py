# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Default sport weights and typical intensities for the estimate without HR."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass

from ponos_coach.activities.domain import Sport


@dataclass(frozen=True, slots=True)
class SportWeight:
    """Weight of a sport in the load (unitless), between 0 and `MAX`; 0 zeroes the load."""

    MAX = 5.0

    value: float

    def __post_init__(self) -> None:
        if not math.isfinite(self.value) or not 0.0 <= self.value <= self.MAX:
            raise ValueError(f"sport weight outside [0, {self.MAX}]")


# Project default (PLAN.md section 2 point 7), provisional, to be tuned in phase 3.
_DEFAULT_WEIGHTS: dict[Sport, float] = {
    Sport.RUNNING: 1.0,
    Sport.TRAIL: 1.1,
    Sport.CYCLING: 0.8,
    Sport.HIKING: 0.8,
    Sport.WALKING: 0.6,
    Sport.STRENGTH: 1.2,
    Sport.OTHER: 0.8,
}

# Project default: typical intensity IF (fraction of the threshold, unitless) for the
# estimate without HR; estimated hrTSS = duration_h * 100 * IF^2. To be tuned in phase 3.
_TYPICAL_INTENSITY: dict[Sport, float] = {
    Sport.RUNNING: 0.80,
    Sport.TRAIL: 0.80,
    Sport.CYCLING: 0.65,
    Sport.HIKING: 0.55,
    Sport.WALKING: 0.45,
    Sport.STRENGTH: 0.50,
    Sport.OTHER: 0.55,
}


def default_weight(sport: Sport) -> SportWeight:
    """Default weight of the sport."""
    return SportWeight(_DEFAULT_WEIGHTS[sport])


def effective_weight(sport: Sport, saved: Mapping[Sport, SportWeight]) -> SportWeight:
    """The weight of a sport: the one saved by the user, otherwise the default (single point)."""
    return saved.get(sport, default_weight(sport))


def effective_weights(saved: Mapping[Sport, SportWeight]) -> dict[Sport, SportWeight]:
    """The weight of each sport: the one saved by the user, otherwise the default."""
    return {sport: effective_weight(sport, saved) for sport in Sport}


def customised_weights(weights: Mapping[Sport, SportWeight]) -> dict[Sport, SportWeight]:
    """Only the weights different from the default: what is worth saving (canonical form)."""
    return {sport: w for sport, w in weights.items() if w != default_weight(sport)}


def typical_intensity(sport: Sport) -> float:
    """Typical intensity (IF, between 0 and 1) used to estimate the load without HR."""
    return _TYPICAL_INTENSITY[sport]
