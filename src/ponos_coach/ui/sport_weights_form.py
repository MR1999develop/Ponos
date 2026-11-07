# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Form "Sport weights": values and rules, testable without a window.

A weight ranges from 0 to `SportWeight.MAX` with two decimals; out of range it is clamped to the
limit.
"Restore defaults" puts back the domain's default weights. The form knows neither widgets nor
texts.
"""

from __future__ import annotations

import math
from collections.abc import Mapping

from ponos_coach.activities.domain import Sport
from ponos_coach.load.domain import SportWeight, default_weight

WEIGHT_DECIMALS = 2
WEIGHT_STEP = 0.05


def _clamped(value: float) -> float:
    return round(min(max(value, 0.0), SportWeight.MAX), WEIGHT_DECIMALS)


class SportWeightsForm:
    """One weight for each sport, starting from those in force."""

    def __init__(self, current: Mapping[Sport, SportWeight]) -> None:
        self._initial = {sport: _clamped(current[sport].value) for sport in Sport}
        self._values = dict(self._initial)

    def value(self, sport: Sport) -> float:
        """The weight shown for the sport."""
        return self._values[sport]

    def set_value(self, sport: Sport, value: float) -> None:
        """The user changes a weight; out of range it goes to the nearest limit. A non-numeric
        value (NaN) changes nothing: zeroing the weight would remove the sport from the load."""
        if not math.isnan(value):
            self._values[sport] = _clamped(value)

    def restore_defaults(self) -> None:
        """Put back the default weights of all sports."""
        self._values = {sport: _clamped(default_weight(sport).value) for sport in Sport}

    @property
    def is_default(self) -> bool:
        """True if all weights are the default ones."""
        return all(self._values[sport] == default_weight(sport).value for sport in Sport)

    @property
    def has_changes(self) -> bool:
        """True if at least one weight differs from the one in force at opening."""
        return self._values != self._initial

    def build(self) -> dict[Sport, SportWeight]:
        """The weights to save: one per sport (the use case keeps only those different from the
        default)."""
        return {sport: SportWeight(value) for sport, value in self._values.items()}
