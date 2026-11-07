# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""The food table shipped with the app, read the way the Composition Root reads it."""

from __future__ import annotations

from importlib import resources

from ponos_coach.nutrition.food_table import (
    FOOD_TABLE_RESOURCE,
    MAX_FOOD_TABLE_BYTES,
    FoodTable,
    parse_food_table,
)


def load_food_table() -> FoodTable:
    """The packaged `foods.json`, parsed."""
    resource = resources.files("ponos_coach.nutrition").joinpath(FOOD_TABLE_RESOURCE)
    with resource.open("rb") as handle:
        return parse_food_table(handle.read(MAX_FOOD_TABLE_BYTES + 1))
