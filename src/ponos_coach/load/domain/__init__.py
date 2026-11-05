# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Pure domain of the load feature (no I/O, Qt, network or logging)."""

from ponos_coach.load.domain.activity_load import (
    ActivityLoad,
    LoadSource,
    compute_activity_load,
)
from ponos_coach.load.domain.heart_rate import (
    HeartRateLimits,
    Physiology,
    Sex,
    ThresholdHeartRate,
    threshold_for_sport,
)
from ponos_coach.load.domain.pmc import PmcSeries, compute_pmc
from ponos_coach.load.domain.sport import SportWeight, default_weight, typical_intensity

__all__ = [
    "ActivityLoad",
    "HeartRateLimits",
    "LoadSource",
    "Physiology",
    "PmcSeries",
    "Sex",
    "SportWeight",
    "ThresholdHeartRate",
    "compute_activity_load",
    "compute_pmc",
    "default_weight",
    "threshold_for_sport",
    "typical_intensity",
]
