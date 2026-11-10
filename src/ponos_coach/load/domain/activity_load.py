# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Load of a single activity: TRIMPeq, the only measure of load.

TRIMPeq is Banister on the running-equivalent intensity, 100 = one hour at the threshold of the
sport (see `banister`).

Formulas in `PLAN.md` section 4.5. The computation is vectorized (NumPy), with no per-sample loops.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum

import numpy as np
from numpy.typing import NDArray

from ponos_coach.activities.domain import HeartRateSeries, Sport
from ponos_coach.load.domain.banister import (
    BANISTER_COEFFICIENT,
    POINTS_PER_THRESHOLD_HOUR,
    equivalent_reserve_ratio,
    reference_hour_weight,
)
from ponos_coach.load.domain.heart_rate import HeartRateLimits, Physiology, threshold_for_sport
from ponos_coach.load.domain.sport import typical_intensity
from ponos_coach.load.domain.zones import ZONE_COUNT, ZoneSeconds, seconds_per_zone, zone_indices

MAX_GAP_S = 10.0  # project default: beyond this interval between samples, it is not counted
MAX_BAD_FRACTION = 0.30  # project default: beyond this, the load is an estimate
_SINGLE_SAMPLE_S = 1.0  # validity of a single sample (no interval to infer it from)
_MS_PER_S = 1000
_PERCENT_DENOMINATOR = 100
_GOOD_PERCENT = round((1.0 - MAX_BAD_FRACTION) * _PERCENT_DENOMINATOR)  # 70


class LoadSource(Enum):
    """Where the load comes from: measured HR or estimated from the duration."""

    HEART_RATE = "heart_rate"
    ESTIMATE = "estimate"


@dataclass(frozen=True, slots=True)
class ActivityLoad:
    """Outcome of the load computation of an activity.

    `trimp_eq` is the load, in TRIMPeq (an estimate: see `LoadSource`).
    `used_samples` are the samples counted (0 for an estimate); `discarded_samples` those with
    HR outside `[min, max]`; `excluded_seconds` the seconds of intervals over 10 s not counted.
    `zone_seconds` are the seconds of the counted samples in each HR zone (on the sport's
    threshold): the same samples as the load, hence with no discards or gaps; all zero if there is
    no
    HR series (even for an estimate made with few valid samples the seconds in zone are
    the measured ones).
    """

    trimp_eq: float
    used_samples: int
    discarded_samples: int
    excluded_seconds: float
    source: LoadSource
    zone_seconds: ZoneSeconds = ZoneSeconds.ZERO


def sample_validity_s(seconds: NDArray[np.float64]) -> NDArray[np.float64]:
    """Validity seconds of each sample: until the next; the last one takes the median."""
    if seconds.size == 1:
        return np.array([_SINGLE_SAMPLE_S])
    gaps = np.diff(seconds)
    return np.append(gaps, np.median(gaps))


def out_of_limits(series: HeartRateSeries, limits: HeartRateLimits) -> NDArray[np.bool_]:
    """Mask of the samples with HR outside `[min, max]`: those discarded by the load."""
    return ~_in_range(series, limits)


def _in_range(series: HeartRateSeries, limits: HeartRateLimits) -> NDArray[np.bool_]:
    return (series.bpm >= limits.min_bpm) & (series.bpm <= limits.max_bpm)


def _estimated(
    sport: Sport,
    moving_time_s: float,
    *,
    discarded: int,
    excluded_s: float,
    zones: ZoneSeconds,
) -> ActivityLoad:
    """Duration times the typical intensity of the sport: `duration_h * 100 * IF^2`, already in
    the scale where 100 is one hour at the threshold."""
    trimp_eq = moving_time_s / 3600.0 * POINTS_PER_THRESHOLD_HOUR * typical_intensity(sport) ** 2
    return ActivityLoad(trimp_eq, 0, discarded, excluded_s, LoadSource.ESTIMATE, zones)


def _is_mostly_missing(counted_s: float, moving_time_s: float) -> bool:
    """True if the counted seconds are less than 70% of the moving time (whole milliseconds)."""
    counted_ms = round(counted_s * _MS_PER_S)
    moving_ms = round(moving_time_s * _MS_PER_S)
    return counted_ms * _PERCENT_DENOMINATOR < _GOOD_PERCENT * moving_ms


def compute_activity_load(
    sport: Sport,
    moving_time_s: float,
    series: HeartRateSeries | None,
    physiology: Physiology,
) -> ActivityLoad:
    """Computes the TRIMPeq of an activity (its load).

    Each sample is valid until the next (the last, the median of the intervals); samples
    with HR out of limits are discarded and counted; intervals over 10 s are not counted.
    `moving_time_s` is the moving time (activity timer), not the elapsed time, which
    includes pauses. If the counted seconds are less than 70% of the moving time (i.e. over
    30% discarded or missing) or there is no HR, the load is the estimate: duration times the
    typical intensity of the sport (`duration_h * 100 * IF^2`), marked `LoadSource.ESTIMATE`.
    The comparison with 70% is in whole milliseconds, with no floating point at the boundary.
    Pure and deterministic function.

    Raises:
        ValueError: if `moving_time_s` is not finite and positive.
    """
    if not math.isfinite(moving_time_s) or moving_time_s <= 0:
        raise ValueError("the duration must be finite and positive")
    if series is None:
        return _estimated(
            sport,
            moving_time_s,
            discarded=0,
            excluded_s=0.0,
            zones=ZoneSeconds.ZERO,
        )

    limits = physiology.limits
    validity = sample_validity_s(series.seconds)
    in_range = _in_range(series, limits)
    counted = in_range & (validity <= MAX_GAP_S)
    discarded = int(np.count_nonzero(~in_range))
    excluded_s = float(validity[in_range & ~counted].sum())
    counted_s = float(validity[counted].sum())
    zones = seconds_per_zone(
        series.bpm[counted], validity[counted], threshold_for_sport(physiology, sport)
    )
    if _is_mostly_missing(counted_s, moving_time_s):
        return _estimated(
            sport,
            moving_time_s,
            discarded=discarded,
            excluded_s=excluded_s,
            zones=zones,
        )

    samples = _sample_load(series.bpm[counted], validity[counted], physiology, sport)
    return ActivityLoad(
        float(np.sum(samples)),
        int(np.count_nonzero(counted)),
        discarded,
        excluded_s,
        LoadSource.HEART_RATE,
        zones,
    )


def _sample_load(
    bpm: NDArray[np.float64],
    validity_s: NDArray[np.float64],
    physiology: Physiology,
    sport: Sport,
) -> NDArray[np.float64]:
    """TRIMPeq of each counted sample, over its validity seconds: Banister weight of the
    running-equivalent intensity, 100 = one hour at the threshold of the sport."""
    limits = physiology.limits
    hrr = (bpm - limits.min_bpm) / limits.reserve_bpm
    eq = equivalent_reserve_ratio(hrr, physiology, sport)
    weight = eq * BANISTER_COEFFICIENT * np.exp(physiology.sex.banister_k * eq)
    return (
        POINTS_PER_THRESHOLD_HOUR * (validity_s / 60.0) * weight / reference_hour_weight(physiology)
    )


type ZoneLoad = tuple[float, float, float, float, float]
"""Part of an activity's load earned in each HR zone (Z1 ... Z5), in TRIMPeq."""


def load_per_zone(
    sport: Sport,
    series: HeartRateSeries | None,
    physiology: Physiology,
    load: ActivityLoad,
) -> ZoneLoad | None:
    """How the TRIMPeq of an activity splits among the HR zones.

    Each counted sample (the same as `compute_activity_load`) brings its TRIMPeq to its zone, so the
    five parts add up to `load.trimp_eq`. None if the load is an estimate (it does not come from the
    HR) or nothing was counted. Pure and deterministic.
    """
    if series is None or load.source is not LoadSource.HEART_RATE:
        return None
    validity = sample_validity_s(series.seconds)
    counted = _in_range(series, physiology.limits) & (validity <= MAX_GAP_S)
    trimp = _sample_load(series.bpm[counted], validity[counted], physiology, sport)
    zones = zone_indices(series.bpm[counted], threshold_for_sport(physiology, sport))
    per_zone = np.bincount(zones, weights=trimp, minlength=ZONE_COUNT)
    total = float(per_zone.sum())
    if not total > 0:
        return None
    z1, z2, z3, z4, z5 = (float(value) * load.trimp_eq / total for value in per_zone)
    return (z1, z2, z3, z4, z5)
