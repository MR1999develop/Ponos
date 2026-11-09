# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Activity detail: outcomes and zone rows, testable without a window.

The use case (`GetActivityDetail`) returns an `ActivityDetail` or the reason the file cannot be
used; here the outcome becomes a value for the view, with the chart already prepared off the
UI thread. The view chooses the texts.
"""

from __future__ import annotations

from dataclasses import dataclass

from ponos_coach.load.activity_detail import ActivityDetail
from ponos_coach.load.domain import ZONE_COUNT
from ponos_coach.load.use_cases import ExclusionReason
from ponos_coach.ui.chart_payload import PreparedChart
from ponos_coach.ui.load_figures import zone_label


@dataclass(frozen=True, slots=True)
class DetailReady:
    """The detail is available. `chart` is `None` if the activity has no HR."""

    detail: ActivityDetail
    chart: PreparedChart | None


@dataclass(frozen=True, slots=True)
class DetailUnavailable:
    """The activity file cannot be used: the reason is a domain category."""

    reason: ExclusionReason


type DetailOutcome = DetailReady | DetailUnavailable


@dataclass(frozen=True, slots=True)
class ZoneRow:
    """One zone: name with its bounds, seconds, share of the time in zone (0-1) and the load
    earned there (None when the activity's load is an estimate)."""

    label: str
    seconds: float
    share: float
    load: float | None = None


def zone_rows(detail: ActivityDetail) -> tuple[ZoneRow, ...]:
    """One row per zone, Z1 to Z5. Without time in zone (estimated load) the shares are 0."""
    seconds = detail.load.zone_seconds
    total = seconds.total
    zone_load = detail.zone_load
    return tuple(
        ZoneRow(
            zone_label(zone),
            seconds.values[zone],
            seconds.values[zone] / total if total else 0.0,
            None if zone_load is None else zone_load[zone],
        )
        for zone in range(ZONE_COUNT)
    )
