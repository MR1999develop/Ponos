# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Macronutrient amounts in whole grams and their exact splitting."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from ponos_coach.nutrition.domain import defaults


@dataclass(frozen=True, slots=True)
class Macros:
    """Carbohydrates, protein and fat in whole grams, never negative."""

    carbs_g: int = 0
    protein_g: int = 0
    fat_g: int = 0

    def __post_init__(self) -> None:
        if min(self.carbs_g, self.protein_g, self.fat_g) < 0:
            raise ValueError("macronutrient grams cannot be negative")

    @property
    def kcal(self) -> int:
        """Energy of these grams (4 kcal/g carbohydrates and protein, 9 kcal/g fat)."""
        return (
            defaults.KCAL_PER_G_CARBS * self.carbs_g
            + defaults.KCAL_PER_G_PROTEIN * self.protein_g
            + defaults.KCAL_PER_G_FAT * self.fat_g
        )

    def __add__(self, other: Macros) -> Macros:
        return Macros(
            self.carbs_g + other.carbs_g,
            self.protein_g + other.protein_g,
            self.fat_g + other.fat_g,
        )


def split_integer(total: int, weights: Sequence[float]) -> list[int]:
    """Splits `total` into whole parts proportional to `weights`, summing exactly to `total`.

    Largest-remainder method; ties go to the earlier part, so the result is deterministic.

    Raises:
        ValueError: `total` negative, no weights, or weights negative or all zero.
    """
    if total < 0 or not weights or min(weights) < 0 or sum(weights) <= 0:
        raise ValueError("a non-negative total and positive weights are needed")
    scale = total / sum(weights)
    exact = [weight * scale for weight in weights]
    parts = [int(value) for value in exact]
    by_remainder = sorted(range(len(parts)), key=lambda i: (parts[i] - exact[i], i))
    for i in by_remainder[: total - sum(parts)]:
        parts[i] += 1
    return parts
