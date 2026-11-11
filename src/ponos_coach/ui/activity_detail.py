# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Activity detail: outcomes and zone rows, testable without a window.

The use case (`GetActivityDetail`) returns an `ActivityDetail` or the reason the file cannot be
used; here the outcome becomes a value for the view, with the chart already prepared off the
UI thread. The view chooses the texts.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from ponos_coach.activities.domain import Sport
from ponos_coach.load.activity_detail import ActivityDetail
from ponos_coach.load.domain import ZONE_COUNT, zone_bpm_ranges
from ponos_coach.load.use_cases import ExclusionReason
from ponos_coach.nutrition.domain import ActivityEnergy
from ponos_coach.ui.chart_payload import PreparedChart
from ponos_coach.ui.load_figures import zone_label


@dataclass(frozen=True, slots=True)
class DetailReady:
    """The detail is available. `chart` is `None` if the activity has no HR; `energy` is `None`
    if the body data of the activity's day do not allow computing it; `speed_chart` is `None` if
    the file has no distance or speed."""

    detail: ActivityDetail
    chart: PreparedChart | None
    energy: ActivityEnergy | None = None
    speed_chart: PreparedChart | None = None


@dataclass(frozen=True, slots=True)
class DetailUnavailable:
    """The activity file cannot be used: the reason is a domain category."""

    reason: ExclusionReason


type DetailOutcome = DetailReady | DetailUnavailable

PACE_SPORTS = frozenset({Sport.RUNNING, Sport.TRAIL, Sport.HIKING})  # measured in min/km
_METERS_PER_KM = 1000.0


def average_pace_s_per_km(detail: ActivityDetail) -> float | None:
    """Moving time per kilometre, for the sports measured by pace; None for the other sports and
    when the file has no distance or no moving time."""
    distance_m = detail.summary.distance_m
    if detail.sport not in PACE_SPORTS or distance_m is None:
        return None
    if distance_m <= 0 or detail.timer_s <= 0:
        return None
    return detail.timer_s * _METERS_PER_KM / distance_m


@dataclass(frozen=True, slots=True)
class ZoneRow:
    """One zone: name with its bounds, seconds, share of the time in zone (0-1), the load
    earned there (None when the activity's load is an estimate) and the whole-bpm range of the zone
    that day (None when the zone has no bpm inside the limits)."""

    label: str
    seconds: float
    share: float
    load: float | None = None
    bpm: tuple[int, int] | None = None


def zone_rows(detail: ActivityDetail) -> tuple[ZoneRow, ...]:
    """One row per zone, Z1 to Z5. Without time in zone (estimated load) the shares are 0."""
    seconds = detail.load.zone_seconds
    total = seconds.total
    zone_load = detail.zone_load
    bpm = zone_bpm_ranges(detail.threshold_bpm, detail.limits.min_bpm, detail.limits.max_bpm)
    return tuple(
        ZoneRow(
            zone_label(zone),
            seconds.values[zone],
            seconds.values[zone] / total if total else 0.0,
            None if zone_load is None else zone_load[zone],
            bpm[zone],
        )
        for zone in range(ZONE_COUNT)
    )


class DomainKind(Enum):
    """The three intensity domains delimited by LT1 and LT2."""

    BELOW_LT1 = "below_lt1"
    BETWEEN = "between"
    ABOVE_LT2 = "above_lt2"


@dataclass(frozen=True, slots=True)
class DomainRow:
    """One domain: which one, seconds, share of the time in the three domains (0-1) and its
    whole-bpm range that day, both ends included (None when no whole bpm lies inside the
    limits). The view chooses the name."""

    kind: DomainKind
    seconds: float
    share: float
    bpm: tuple[int, int] | None


def _range_or_none(low: int, high: int) -> tuple[int, int] | None:
    return (low, high) if low <= high else None


def domain_rows(detail: ActivityDetail) -> tuple[DomainRow, ...]:
    """Three rows: below LT1 (`bpm < LT1`), between (`LT1 <= bpm < LT2`) and above LT2
    (`bpm >= LT2`), with the LT1 and LT2 of the day. Without HR the times and shares are 0."""
    measured = detail.domain_seconds
    values = (0.0, 0.0, 0.0) if measured is None else measured.as_tuple()
    total = sum(values)
    limits = detail.limits
    lt1, lt2 = detail.lt1_bpm, detail.threshold_bpm
    ranges = (
        _range_or_none(limits.min_bpm, min(lt1 - 1, limits.max_bpm)),
        _range_or_none(max(lt1, limits.min_bpm), min(lt2 - 1, limits.max_bpm)),
        _range_or_none(max(lt2, limits.min_bpm), limits.max_bpm),
    )
    return tuple(
        DomainRow(kind, seconds, seconds / total if total else 0.0, bpm)
        for kind, seconds, bpm in zip(DomainKind, values, ranges, strict=True)
    )
