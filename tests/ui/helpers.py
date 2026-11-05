# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Valori e costruttori comuni ai test dell'interfaccia."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from ponos_coach.activities.domain import FileId, FitIdentity, Sport
from ponos_coach.load.domain import ActivityLoad, LoadSource, PmcSeries, compute_pmc
from ponos_coach.load.use_cases import ActivityEntry
from ponos_coach.ui.pmc_figure import PLOTLY_MODEBAR_KEYS, PmcChartLabels

LABELS = PmcChartLabels(
    x_axis="Date",
    load_axis="Load",
    form_axis="Form",
    ctl="Fitness (CTL)",
    atl="Fatigue (ATL)",
    tsb="Form (TSB)",
    daily_load="Daily load",
    export_png="Save as PNG",
    export_svg="Save as SVG",
    modebar={key: f"{key} (test)" for key in PLOTLY_MODEBAR_KEYS},
)

FIRST_DAY = date(2026, 3, 2)


def entry(day: date, load: float, name: str = "a.fit") -> ActivityEntry:
    """Un'attivita di corsa con il carico dato, il giorno dato."""
    start = datetime(day.year, day.month, day.day, 7, 0, tzinfo=UTC)
    activity_load = ActivityLoad(load, load, load, 100, 0, 0.0, LoadSource.HEART_RATE)
    return ActivityEntry(
        name,
        FitIdentity(FileId(1, 1, 1, start)),
        Sport.RUNNING,
        start,
        day,
        3600.0,
        3600.0,
        activity_load,
    )


def small_history() -> tuple[PmcSeries, list[ActivityEntry]]:
    """Dieci giorni di PMC con tre attivita (due nello stesso giorno)."""
    entries = [
        entry(FIRST_DAY, 50.0, "one.fit"),
        entry(FIRST_DAY, 30.0, "two.fit"),
        entry(FIRST_DAY + timedelta(days=3), 80.0, "three.fit"),
    ]
    daily = {FIRST_DAY: 80.0, FIRST_DAY + timedelta(days=3): 80.0}
    return compute_pmc(daily, FIRST_DAY, FIRST_DAY + timedelta(days=9)), entries
