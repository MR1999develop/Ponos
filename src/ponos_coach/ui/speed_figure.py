# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Plotly speed or pace figure of an activity, on the same time axis as the HR figure.

Pure function from `SpeedSeries` (smoothed and thinned by the domain) to `go.Figure`. The sports
measured by pace (`PACE_SPORTS`: the rule of the summary's average pace) get a pace in min/km,
with the axis upside down so that faster is higher and the ticks written `m:ss`; the others get
a speed in km/h. A hole in the data (a pause, a stop) is a break in the line.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import plotly.graph_objects as go
from numpy.typing import NDArray

from ponos_coach.activities.domain import SpeedSeries, Sport
from ponos_coach.ui.activity_detail import PACE_SPORTS
from ponos_coach.ui.activity_text import pace_text
from ponos_coach.ui.chart_payload import (
    ChartChrome,
    PreparedChart,
    XNavigation,
    axis_style,
    base_layout,
    prepare_figure,
)
from ponos_coach.ui.tokens import ThemeTokens

SPEED_UNIT = "km/h"
PACE_UNIT = "min/km"
TIME_UNIT = "min"
SPEED_EXPORT_STEM = "ponos-coach-speed"
PACE_EXPORT_STEM = "ponos-coach-pace"
_SECONDS_PER_MINUTE = 60.0
_METERS_PER_KM = 1000.0
_KMH_PER_M_S = 3.6
_LINE_WIDTH = 1.5
_SERIES_SLOT = 2  # the green of the category palette: not the HR's blue, never red or orange
# Pace ticks: the smallest step that leaves at most this many, from the usual ones (seconds).
_MAX_PACE_TICKS = 7
_PACE_STEPS_S = (10, 15, 30, 60, 120, 300, 600)
# The pace axis spans what most of the session covers; a rare very slow point may leave the area.
_PACE_RANGE_PERCENTILES = (1.0, 99.0)


@dataclass(frozen=True, slots=True)
class SpeedLabels:
    """Speed chart texts, already translated by the view. `speed` and `pace` name both the axis
    and the series."""

    x_axis: str
    speed: str
    pace: str
    chrome: ChartChrome


def uses_pace(sport: Sport) -> bool:
    """True for the sports shown as a pace (the summary's rule), false for a speed."""
    return sport in PACE_SPORTS


def pace_s_per_km(speed_m_s: NDArray[np.float64]) -> NDArray[np.float64]:
    """Pace in seconds per kilometre; NaN stays NaN (a hole has no pace)."""
    with np.errstate(divide="ignore", invalid="ignore"):
        return _METERS_PER_KM / speed_m_s


def _pace_axis(pace: NDArray[np.float64]) -> tuple[list[float], list[str], list[float]]:
    """Tick values, their `m:ss` texts and the reversed range (slowest first) for the pace."""
    low, high = np.nanpercentile(pace, _PACE_RANGE_PERCENTILES)
    step = next(
        (s for s in _PACE_STEPS_S if (high - low) / s <= _MAX_PACE_TICKS - 1), _PACE_STEPS_S[-1]
    )
    fastest = max(math.floor(low / step) * step, 0)
    slowest = max(math.ceil(high / step) * step, fastest + step)
    values = [float(v) for v in range(int(fastest), int(slowest) + 1, step)]
    return values, [pace_text(v) for v in values], [float(slowest), float(fastest)]


def build_speed_figure(
    series: SpeedSeries, sport: Sport, labels: SpeedLabels, tokens: ThemeTokens
) -> go.Figure:
    """One curve against the time in minutes: pace for `PACE_SPORTS`, speed for the rest."""
    minutes = series.seconds / _SECONDS_PER_MINUTE
    colour = tokens.categories[_SERIES_SLOT]
    figure = go.Figure()
    figure.update_xaxes(axis_style(tokens, f"{labels.x_axis} ({TIME_UNIT})"), rangemode="tozero")
    if uses_pace(sport):
        pace = pace_s_per_km(series.meters_per_second)
        ticks, texts, span = _pace_axis(pace)
        figure.add_trace(
            go.Scatter(
                x=minutes,
                y=pace,
                name=labels.pace,
                mode="lines",
                line={"color": colour, "width": _LINE_WIDTH},
                text=[pace_text(p) if np.isfinite(p) else "" for p in pace],
                hovertemplate=(
                    f"%{{x:.1f}} {TIME_UNIT}<br>%{{text}} {PACE_UNIT}<extra>%{{fullData.name}}"
                    "</extra>"
                ),
            )
        )
        figure.update_yaxes(
            axis_style(tokens, f"{labels.pace} ({PACE_UNIT})"),
            range=span,
            tickmode="array",
            tickvals=ticks,
            ticktext=texts,
        )
    else:
        figure.add_trace(
            go.Scatter(
                x=minutes,
                y=series.meters_per_second * _KMH_PER_M_S,
                name=labels.speed,
                mode="lines",
                line={"color": colour, "width": _LINE_WIDTH},
                hovertemplate=(
                    f"%{{x:.1f}} {TIME_UNIT}<br>%{{y:.1f}} {SPEED_UNIT}<extra>%{{fullData.name}}"
                    "</extra>"
                ),
            )
        )
        figure.update_yaxes(
            axis_style(tokens, f"{labels.speed} ({SPEED_UNIT})"), rangemode="tozero"
        )
    figure.update_layout(
        **base_layout(tokens),
        showlegend=False,  # one series, named by its axis
        margin={"l": 64, "r": 16, "t": 16, "b": 48},  # the plot area lines up with the HR chart
        hovermode="x",
    )
    return figure


def prepare_speed_chart(
    series: SpeedSeries, sport: Sport, labels: SpeedLabels, tokens: ThemeTokens
) -> PreparedChart:
    """Speed or pace figure and JSON (pure work, suited to a non-UI thread). Its x axis zooms and
    pans in step with the HR chart's."""
    figure = build_speed_figure(series, sport, labels, tokens)
    return prepare_figure(
        figure,
        labels.chrome,
        PACE_EXPORT_STEM if uses_pace(sport) else SPEED_EXPORT_STEM,
        tokens,
        navigation=XNavigation.LINKED_FIXED_Y if uses_pace(sport) else XNavigation.LINKED,
    )
