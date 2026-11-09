# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Plotly figures of weekly load per sport and time in HR zones.

Pure functions from `WeeklyLoad` and `WeeklyZones` (already aggregated by the domain) to
`go.Figure`. Stacked
bars starting at zero, the whole history, opened on the last 26 weeks (the wheel zooms and a drag
pans, together with the PMC). A
sport's colour is always the same and never red or orange (reserved for errors and warnings);
there are at most five series: sports with less load are grouped into "Other". Zones use
a sequential scale of increasing lightness, so the order reads even without the colours
(legend and hints give the name).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np
import plotly.graph_objects as go
from numpy.typing import NDArray

from ponos_coach.activities.domain import Sport
from ponos_coach.load.domain import ZONE_COUNT, ZONE_STARTS_PERCENT, WeeklyLoad, WeeklyZones
from ponos_coach.ui.chart_payload import (
    LEGEND_Y,
    ChartChrome,
    PreparedChart,
    XNavigation,
    axis_style,
    base_layout,
    prepare_figure,
)
from ponos_coach.ui.tokens import ThemeTokens

CHART_WEEKS = 26  # weeks shown when the chart opens: about six months
WEEKLY_LOAD_UNIT = (
    "hrTSS per week"  # whole sentences: the view translates them (`unit` of the labels)
)
ZONES_AXIS_UNIT = "hours per week"
ZONE_TIME_UNIT = "h"  # in a bar's hint: the symbol is the same in every language
MAX_SPORT_SERIES = 5  # beyond that, sports with less load are grouped into "Other"
LOAD_EXPORT_STEM = "ponos-coach-weekly-load"
ZONES_EXPORT_STEM = "ponos-coach-zones"
_SECONDS_PER_HOUR = 3600.0
_BAR_GAP = 0.15
_SEPARATOR_WIDTH = 1  # background-colour line between segments: separates even without colour
_LAST_ZONE_INDEX = ZONE_COUNT - 1

# Place of each sport among the token's seven category colours: a sport always keeps its own.
# Sports adjacent in the `Sport` order (hence adjacent in the stack) get distant hues.
SPORT_COLOR_SLOT: Mapping[Sport, int] = {
    Sport.RUNNING: 0,
    Sport.TRAIL: 1,
    Sport.CYCLING: 2,
    Sport.STRENGTH: 3,
    Sport.HIKING: 4,
    Sport.WALKING: 5,
    Sport.OTHER: 6,
}


@dataclass(frozen=True, slots=True)
class WeeklyLoadLabels:
    """Texts of the weekly load chart, already translated by the view."""

    x_axis: str
    y_axis: str
    sports: Mapping[Sport, str]
    chrome: ChartChrome
    unit: str = WEEKLY_LOAD_UNIT  # "hrTSS per week", translated as a whole sentence


@dataclass(frozen=True, slots=True)
class WeeklyZonesLabels:
    """Texts of the time-in-zones chart, already translated by the view."""

    x_axis: str
    y_axis: str
    legend_title: str
    chrome: ChartChrome
    unit: str = ZONES_AXIS_UNIT  # "hours per week", translated as a whole sentence


def sport_color(sport: Sport, tokens: ThemeTokens) -> str:
    """Category colour of a sport in the theme."""
    return tokens.categories[SPORT_COLOR_SLOT[sport]]


def _percent_text(value: float) -> str:
    return f"{value:g}"


def zone_label(index: int, starts: Sequence[float] = ZONE_STARTS_PERCENT) -> str:
    """Short name of a zone with its bounds as a percentage of the threshold (index from 0).

    `Z1 <78%`, `Z2 78-86%`, ..., `Z5 ≥100%`. The bounds come from `ZONE_STARTS_PERCENT`, the only
    source; acronyms and symbols are the same in every language. With integer bounds a middle zone
    ends
    one point before the next bound; with non-integer bounds (87.5) it says "from 85 to less than
    90"
    (`Z2 85-<90%`), because "one point before" would be wrong.
    """
    name = f"Z{index + 1}"
    if index == 0:
        return f"{name} <{_percent_text(starts[0])}%"
    if index == _LAST_ZONE_INDEX:
        return f"{name} ≥{_percent_text(starts[-1])}%"
    start, end = starts[index - 1], starts[index]
    if all(float(value).is_integer() for value in starts):
        return f"{name} {_percent_text(start)}\u2013{_percent_text(end - 1)}%"
    return f"{name} {_percent_text(start)}\u2013<{_percent_text(end)}%"


def opening_range(weeks: Sequence[date]) -> list[date] | None:
    """Dates shown when the chart opens: the last `CHART_WEEKS` weeks, each bar whole (a bar
    is centred on its Monday); None with fewer weeks, so the whole series shows."""
    if len(weeks) <= CHART_WEEKS:
        return None
    margin = timedelta(days=4)  # half a week, rounded up to a whole day
    return [weeks[-CHART_WEEKS] - margin, weeks[-1] + margin]


def _style_weekly_layout(
    figure: go.Figure,
    tokens: ThemeTokens,
    x_title: str,
    y_title: str,
    weeks: Sequence[date],
    legend_title: str = "",
) -> None:
    figure.update_xaxes(axis_style(tokens, x_title), type="date", hoverformat="%Y-%m-%d")
    shown = opening_range(weeks)
    if shown is not None:
        figure.update_xaxes(range=shown)
    figure.update_yaxes(axis_style(tokens, y_title), rangemode="tozero")  # bars from zero
    figure.update_layout(
        **base_layout(tokens),
        barmode="stack",
        bargap=_BAR_GAP,
        legend={
            "orientation": "h",
            "traceorder": "normal",
            "y": LEGEND_Y,
            "x": 0,
            "title": {"text": legend_title},
        },
        margin={"l": 64, "r": 16, "t": 48, "b": 48},
        hovermode="x unified",
    )


def sport_series(
    sports: Sequence[Sport], load: NDArray[np.float64]
) -> list[tuple[Sport, NDArray[np.float64]]]:
    """The series to draw: sports with load in the window, in `Sport` order.

    With more than `MAX_SPORT_SERIES` sports, those with the most load remain (one fewer than the
    cap) and
    all the others, together with `Sport.OTHER`, are summed into `Sport.OTHER`, which closes the
    stack. On
    a load tie the `Sport` order applies.
    """
    present = [
        (sport, load[:, column])
        for column, sport in enumerate(sports)
        if np.any(load[:, column] > 0)
    ]
    if len(present) <= MAX_SPORT_SERIES:
        return present
    named = [item for item in present if item[0] is not Sport.OTHER]
    ranked = sorted(named, key=lambda item: -float(item[1].sum()))  # stable: ties by order
    kept = {sport for sport, _ in ranked[: MAX_SPORT_SERIES - 1]}
    merged = np.zeros(load.shape[0], dtype=np.float64)
    for sport, values in present:
        if sport not in kept:
            merged = merged + values
    return [*(item for item in named if item[0] in kept), (Sport.OTHER, merged)]


def build_weekly_load_figure(
    weekly: WeeklyLoad, labels: WeeklyLoadLabels, tokens: ThemeTokens
) -> go.Figure:
    """Load per week, one segment per sport with load in the history (at most
    `MAX_SPORT_SERIES`: see `sport_series`), in `Sport` order."""
    weeks = weekly.weeks
    load = weekly.load
    figure = go.Figure()
    hover = f"%{{y:.0f}} {labels.unit}<extra>%{{fullData.name}}</extra>"
    for sport, values in sport_series(weekly.sports, load):
        figure.add_trace(
            go.Bar(
                x=list(weeks),
                y=values,
                name=labels.sports[sport],
                marker={
                    "color": sport_color(sport, tokens),
                    "line": {"color": tokens.background, "width": _SEPARATOR_WIDTH},
                },
                hovertemplate=hover,
            )
        )
    _style_weekly_layout(figure, tokens, labels.x_axis, f"{labels.y_axis} ({labels.unit})", weeks)
    return figure


def build_weekly_zones_figure(
    weekly: WeeklyZones, labels: WeeklyZonesLabels, tokens: ThemeTokens
) -> go.Figure:
    """Hours per zone and week (Z1 at the bottom, Z5 at the top), the whole history."""
    weeks = weekly.weeks
    hours = weekly.seconds / _SECONDS_PER_HOUR
    figure = go.Figure()
    hover = f"%{{y:.1f}} {ZONE_TIME_UNIT}<extra>%{{fullData.name}}</extra>"
    for zone in range(ZONE_COUNT):
        figure.add_trace(
            go.Bar(
                x=list(weeks),
                y=hours[:, zone],
                name=zone_label(zone),
                marker={
                    "color": tokens.zones[zone],
                    "line": {"color": tokens.background, "width": _SEPARATOR_WIDTH},
                },
                hovertemplate=hover,
            )
        )
    _style_weekly_layout(
        figure,
        tokens,
        labels.x_axis,
        f"{labels.y_axis} ({labels.unit})",
        weeks,
        labels.legend_title,
    )
    return figure


def prepare_weekly_load_chart(
    weekly: WeeklyLoad, labels: WeeklyLoadLabels, tokens: ThemeTokens
) -> PreparedChart:
    """Figure and JSON of the weekly load (pure work, suited to a non-UI thread)."""
    figure = build_weekly_load_figure(weekly, labels, tokens)
    return prepare_figure(
        figure, labels.chrome, LOAD_EXPORT_STEM, tokens, navigation=XNavigation.LINKED
    )


def prepare_weekly_zones_chart(
    weekly: WeeklyZones, labels: WeeklyZonesLabels, tokens: ThemeTokens
) -> PreparedChart:
    """Time-in-zones figure and JSON (pure work, suited to a non-UI thread)."""
    figure = build_weekly_zones_figure(weekly, labels, tokens)
    return prepare_figure(
        figure, labels.chrome, ZONES_EXPORT_STEM, tokens, navigation=XNavigation.LINKED
    )
