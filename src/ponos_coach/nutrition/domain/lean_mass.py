# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Measured lean (fat-free) mass: a dated, optional measurement.

When the user has a measurement valid on a day (from a body scan or an impedance scale), it
replaces the Boer estimate in the limit on energy availability. Health data: never in logs or
messages.
"""

from __future__ import annotations

import math
from bisect import bisect_right
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from ponos_coach.nutrition.domain import defaults
from ponos_coach.nutrition.domain.settings import MIN_VALID_DATE

_TENTHS_PER_KG = 10


@dataclass(frozen=True, slots=True)
class LeanMass:
    """Lean mass in kilograms with one decimal digit, between 15.0 and 200.0 kg.

    An integer in tenths of a kilogram, like the weight: no comparison goes through binary
    floating point.
    """

    tenths_kg: int

    def __post_init__(self) -> None:
        if (
            not defaults.MIN_LEAN_MASS_TENTHS_KG
            <= self.tenths_kg
            <= defaults.MAX_LEAN_MASS_TENTHS_KG
        ):
            raise ValueError("the lean mass must be between 15.0 and 200.0 kg")

    @classmethod
    def from_kg(cls, kg: Decimal) -> LeanMass:
        """Lean mass from a `Decimal` in kilograms with at most one decimal digit.

        Raises:
            ValueError: not finite, more than one decimal digit, or outside 15.0-200.0 kg.
        """
        if not kg.is_finite():
            raise ValueError("the lean mass must be a number")
        low = Decimal(defaults.MIN_LEAN_MASS_TENTHS_KG) / _TENTHS_PER_KG
        high = Decimal(defaults.MAX_LEAN_MASS_TENTHS_KG) / _TENTHS_PER_KG
        if not low <= kg <= high:  # before multiplying: huge exponents overflow
            raise ValueError("the lean mass must be between 15.0 and 200.0 kg")
        tenths = kg * _TENTHS_PER_KG
        if tenths != tenths.to_integral_value():
            raise ValueError("the lean mass has at most one decimal digit")
        return cls(int(tenths))

    @property
    def kg(self) -> float:
        """The mass in kilograms."""
        return self.tenths_kg / _TENTHS_PER_KG


def is_plausible_lean_mass(lean_mass: LeanMass, weight_kg: float) -> bool:
    """True if the lean mass fits the weight: not above 95% of it (under 5% fat is not a
    measurement to trust) and not under 40% of it (project defaults)."""
    if not (math.isfinite(weight_kg) and weight_kg > 0):
        return False
    return (
        defaults.MIN_LEAN_MASS_FRACTION * weight_kg
        <= lean_mass.kg
        <= defaults.MAX_LEAN_MASS_FRACTION * weight_kg
    )


@dataclass(frozen=True, slots=True)
class LeanMassEntry:
    """Lean mass measured on a day, valid from it (included) until the next entry."""

    valid_from: date
    lean_mass: LeanMass

    def __post_init__(self) -> None:
        if self.valid_from < MIN_VALID_DATE:
            raise ValueError("the validity date cannot be earlier than 1900-01-01")


def lean_mass_on(entries: Sequence[LeanMassEntry], day: date) -> LeanMass | None:
    """Lean mass valid on a day: the entry with the latest date not after it, None before the
    first one (a measurement does not hold for days before it was taken).

    `entries` is sorted by ascending date, one entry per date (as the store gives them).
    """
    index = bisect_right([entry.valid_from for entry in entries], day) - 1
    return None if index < 0 else entries[index].lean_mass
