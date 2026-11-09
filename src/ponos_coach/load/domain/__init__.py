# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Pure domain of the load feature (no I/O, Qt, network or logging)."""

from ponos_coach.load.domain.activity_load import (
    MAX_GAP_S,
    ActivityLoad,
    LoadSource,
    ZoneLoad,
    compute_activity_load,
    load_per_zone,
    out_of_limits,
    sample_validity_s,
)
from ponos_coach.load.domain.aggregation import (
    DayLoad,
    WeeklyLoad,
    WeeklyZones,
    iso_week_start,
    weekly_load_by_sport,
    weekly_zone_seconds,
)
from ponos_coach.load.domain.heart_rate import (
    HeartRateLimits,
    Physiology,
    Sex,
    ThresholdHeartRate,
    threshold_for_sport,
)
from ponos_coach.load.domain.parameters import (
    LOAD_MODEL_VERSION,
    LoadFingerprint,
    MissingPhysiologyError,
    PhysiologyEntry,
    load_fingerprint,
    physiology_for_day,
)
from ponos_coach.load.domain.pmc import (
    PEAK_WINDOW_DAYS,
    PmcSeries,
    compute_pmc,
    ctl_share_of_recent_peak,
)
from ponos_coach.load.domain.sport import (
    SportWeight,
    customised_weights,
    default_weight,
    effective_weight,
    effective_weights,
    typical_intensity,
)
from ponos_coach.load.domain.zones import (
    ZONE_COUNT,
    ZONE_STARTS_PERCENT,
    ZoneSeconds,
    seconds_per_zone,
    zone_bpm_ranges,
    zone_indices,
    zone_starts_bpm,
)

__all__ = [
    "LOAD_MODEL_VERSION",
    "MAX_GAP_S",
    "PEAK_WINDOW_DAYS",
    "ZONE_COUNT",
    "ZONE_STARTS_PERCENT",
    "ActivityLoad",
    "DayLoad",
    "HeartRateLimits",
    "LoadFingerprint",
    "LoadSource",
    "MissingPhysiologyError",
    "Physiology",
    "PhysiologyEntry",
    "PmcSeries",
    "Sex",
    "SportWeight",
    "ThresholdHeartRate",
    "WeeklyLoad",
    "WeeklyZones",
    "ZoneLoad",
    "ZoneSeconds",
    "compute_activity_load",
    "compute_pmc",
    "ctl_share_of_recent_peak",
    "customised_weights",
    "default_weight",
    "effective_weight",
    "effective_weights",
    "iso_week_start",
    "load_fingerprint",
    "load_per_zone",
    "out_of_limits",
    "physiology_for_day",
    "sample_validity_s",
    "seconds_per_zone",
    "threshold_for_sport",
    "typical_intensity",
    "weekly_load_by_sport",
    "weekly_zone_seconds",
    "zone_bpm_ranges",
    "zone_indices",
    "zone_starts_bpm",
]
