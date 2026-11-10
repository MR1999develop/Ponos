# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Values and builders common to the interface tests."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from ponos_coach.activities.domain import ActivitySummary, FileId, FitIdentity, Sport
from ponos_coach.load.domain import ActivityLoad, LoadSource, PmcSeries, compute_pmc
from ponos_coach.load.use_cases import ProfileActivity
from ponos_coach.ui.chart_payload import PLOTLY_MODEBAR_KEYS, ChartChrome
from ponos_coach.ui.pmc_figure import ChartActivity, PmcChartLabels

CHROME = ChartChrome(
    export_png="Save as PNG",
    export_svg="Save as SVG",
    modebar={key: f"{key} (test)" for key in PLOTLY_MODEBAR_KEYS},
)

LABELS = PmcChartLabels(
    x_axis="Date",
    load_axis="Load",
    form_axis="Form",
    ctl="Fitness (CTL)",
    atl="Fatigue (ATL)",
    tsb="Form (TSB)",
    daily_load="Daily load",
    chrome=CHROME,
)

FIRST_DAY = date(2026, 3, 2)


def entry(day: date, load: float, name: str = "a.fit") -> ChartActivity:
    """An activity for the chart, with the given load, on the given day."""
    return ChartActivity(day, load, name)


def profile_activity(
    start: datetime,
    *,
    sport: Sport = Sport.RUNNING,
    distance_m: float | None = 10_000.0,
    ascent_m: float | None = 120.0,
    load: float = 80.0,
    source: LoadSource = LoadSource.HEART_RATE,
    serial: int = 1,
    avg_bpm: int | None = 148,
    max_bpm: int | None = 176,
) -> ProfileActivity:
    """A profile activity with the given values (local day = UTC day)."""
    activity_load = ActivityLoad(load, 100, 0, 0.0, source, avg_bpm=avg_bpm, max_bpm=max_bpm)
    return ProfileActivity(
        FitIdentity(FileId(1, serial, 1, start)),
        sport,
        start,
        start.date(),
        3700.0,
        3600.0,
        ActivitySummary(distance_m=distance_m, ascent_m=ascent_m),
        activity_load,
    )


def small_history() -> tuple[PmcSeries, list[ChartActivity]]:
    """Ten days of PMC with three activities (two on the same day)."""
    entries = [
        entry(FIRST_DAY, 50.0, "one.fit"),
        entry(FIRST_DAY, 30.0, "two.fit"),
        entry(FIRST_DAY + timedelta(days=3), 80.0, "three.fit"),
    ]
    daily = {FIRST_DAY: 80.0, FIRST_DAY + timedelta(days=3): 80.0}
    return compute_pmc(daily, FIRST_DAY, FIRST_DAY + timedelta(days=9)), entries
