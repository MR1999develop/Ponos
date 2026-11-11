# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Typical intensity of each sport, for the load estimate of an activity without usable HR."""

from __future__ import annotations

from ponos_coach.activities.domain import Sport

# Project default: typical intensity IF (fraction of the threshold, unitless) for the
# estimate without HR: the estimated load is `duration_h * 100 * IF^2` (TRIMPeq). To be tuned in
# phase 3.
_TYPICAL_INTENSITY: dict[Sport, float] = {
    Sport.RUNNING: 0.80,
    Sport.TRAIL: 0.80,
    Sport.CYCLING: 0.65,
    Sport.HIKING: 0.55,
    Sport.WALKING: 0.45,
    Sport.STRENGTH: 0.50,
    Sport.OTHER: 0.55,
}


def typical_intensity(sport: Sport) -> float:
    """Typical intensity (IF, between 0 and 1) used to estimate the load without HR."""
    return _TYPICAL_INTENSITY[sport]
