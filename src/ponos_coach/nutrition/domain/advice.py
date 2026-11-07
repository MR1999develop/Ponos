# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Categories the model gives to the interface: day load level and advisories.

The domain gives categories, never texts or colors: the interface words them.
"""

from __future__ import annotations

from collections.abc import Iterable
from enum import Enum

from ponos_coach.nutrition.domain import defaults


class DayLoadLevel(Enum):
    """How demanding a day's training is; the order of the members is the order of the effort."""

    REST = "rest"
    LIGHT = "light"
    MODERATE = "moderate"
    HIGH = "high"
    VERY_HIGH = "very_high"

    @property
    def rank(self) -> int:
        """0 for rest up to 4 for very high."""
        return _ORDER.index(self)

    @property
    def deficit_factor(self) -> float:
        """Share of the requested deficit kept on a day of this level."""
        return defaults.DEFICIT_FACTORS[self.rank]

    @property
    def carbs_g_per_kg(self) -> tuple[float, float]:
        """Carbohydrate band (low, high), g per kg of body weight per day."""
        return defaults.CARBS_G_PER_KG[self.rank]


_ORDER = tuple(DayLoadLevel)


def classify_day_load(load: float, training_minutes: float) -> DayLoadLevel:
    """Level of a day from its total load and total training time.

    No training time means rest; otherwise the higher of the level given by the load and the one
    given by the time. A negative or non-finite input is a programming error.

    Raises:
        ValueError: `load` or `training_minutes` negative or not finite.
    """
    if not (0.0 <= load < float("inf") and 0.0 <= training_minutes < float("inf")):
        raise ValueError("load and training time must be finite and not negative")
    if training_minutes == 0.0:
        return DayLoadLevel.REST
    by_load = _level_from(
        load,
        (defaults.LOAD_MODERATE_FROM, defaults.LOAD_HIGH_FROM, defaults.LOAD_VERY_HIGH_FROM),
    )
    by_time = _level_from(
        training_minutes,
        (
            defaults.MINUTES_MODERATE_FROM,
            defaults.MINUTES_HIGH_FROM,
            defaults.MINUTES_VERY_HIGH_FROM,
        ),
    )
    return max(by_load, by_time, key=lambda level: level.rank)


def _level_from(value: float, starts: tuple[float, float, float]) -> DayLoadLevel:
    """Light below the first start, then moderate, high, very high."""
    levels = (DayLoadLevel.LIGHT, DayLoadLevel.MODERATE, DayLoadLevel.HIGH, DayLoadLevel.VERY_HIGH)
    return levels[sum(value >= start for start in starts)]


class Advisory(Enum):
    """Something the user should be told about the advice; the order is the display order."""

    UNDER_18 = "under_18"  # the person is under 18: general guidance only, ask a professional
    LOW_BMI = "low_bmi"  # body mass index under 18.5
    ACTIVITY_LEVEL_ASSUMED = "activity_level_assumed"  # no level set: sedentary assumed
    DEFICIT_NOT_APPLIED = "deficit_not_applied"  # weight loss asked but no deficit is safe
    DEFICIT_LIMITED = "deficit_limited"  # a safety limit cut the requested deficit
    DEFICIT_REDUCED_FOR_LOAD = "deficit_reduced_for_load"  # heavy training day: smaller deficit
    CARBS_LIMITED_BY_ENERGY = "carbs_limited_by_energy"  # the energy does not allow the usual carbs
    INTAKES_SCALED = "intakes_scaled"  # training intakes cut to fit the day's totals
    # a weight-loss period has lasted over 12 weeks with energy availability under 45 kcal/kg:
    # suggest a pause at maintenance
    LONG_WEIGHT_LOSS = "long_weight_loss"


_ADVISORY_ORDER = tuple(Advisory)


def ordered(advisories: Iterable[Advisory]) -> tuple[Advisory, ...]:
    """The advisories without repetitions, in display order."""
    return tuple(sorted(set(advisories), key=_ADVISORY_ORDER.index))
