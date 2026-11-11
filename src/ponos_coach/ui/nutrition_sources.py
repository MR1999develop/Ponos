# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""The stores of the open profile that the nutrition and the energy of an activity read."""

from __future__ import annotations

from ponos_coach.profiles.day_nutrition import NutritionSources
from ponos_coach.ui.workspace import ProfileAccess


def nutrition_sources(access: ProfileAccess) -> NutritionSources:
    """The sources of the open profile (to be called on the data thread)."""
    session = access.session
    return NutritionSources(
        session.activities,
        access.archive,
        session.load_cache,
        session.physiology,
        session.weights,
        session.fixed_data,
        session.activity_levels,
        session.meal_times,
        session.goal_periods,
        session.lean_masses,
    )
