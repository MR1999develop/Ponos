# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Plotly PMC figure: pure function from `PmcSeries` and activities to `go.Figure`.

Two panels with the same date axis: on top CTL, ATL and daily load (bars from
zero), below the form (TSB), which can be negative. The legend follows the series order.
CTL and ATL are told apart even without colour: ATL is dashed and each line carries its name
next to the last point.
The page payload is a JSON produced by `json.dumps` (`render_script`).
"""

from __future__ import annotations

import html
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

import plotly.graph_objects as go
from plotly.subplots import make_subplots

from ponos_coach.load.domain import PmcSeries
from ponos_coach.ui.chart_payload import (
    LEGEND_Y,
    ChartChrome,
    PreparedChart,
    axis_style,
    base_layout,
    prepare_figure,
)
from ponos_coach.ui.tokens import ThemeTokens

LOAD_UNIT = "hrTSS per day"  # unit of the daily load and its averages (CTL, ATL, TSB)
MAX_NAMES_PER_DAY = 5  # activity names shown in a bar's hint
_LINE_WIDTH = 2
_ATL_DASH = "dash"  # ATL is dashed: CTL and ATL differ even without colour
_END_LABEL_SHIFT = 4  # pixels between the last point and the line's name
_TOP_ROW_SHARE = 0.68
EXPORT_FILE_STEM = "ponos-coach-pmc"


@dataclass(frozen=True, slots=True)
class ChartActivity:
    """What the chart knows about an activity: local day, load and name to show."""

    local_day: date
    load: float
    name: str


@dataclass(frozen=True, slots=True)
class PmcChartLabels:
    """Chart texts, already translated by the view."""

    x_axis: str
    load_axis: str
    form_axis: str
    ctl: str
    atl: str
    tsb: str
    daily_load: str
    chrome: ChartChrome
    load_unit: str = LOAD_UNIT  # "hrTSS per day", translated as a whole sentence


def daily_load_of(activities: Sequence[ChartActivity], days: Sequence[date]) -> list[float]:
    """(Weighted) load of each day of the series: sum of that local day's activities."""
    totals: dict[date, float] = defaultdict(float)
    for entry in activities:
        totals[entry.local_day] += entry.load
    return [totals.get(day, 0.0) for day in days]


def _day_hover_text(activities: Sequence[ChartActivity], days: Sequence[date]) -> list[str]:
    """File names of each day as text: HTML entities neutralise `<` and `&`."""
    names: dict[date, list[str]] = defaultdict(list)
    for entry in activities:
        names[entry.local_day].append(html.escape(entry.name, quote=True))
    texts: list[str] = []
    for day in days:
        shown = names.get(day, [])
        extra = len(shown) - MAX_NAMES_PER_DAY
        lines = shown[:MAX_NAMES_PER_DAY] + ([f"+{extra}"] if extra > 0 else [])
        texts.append("<br>".join(lines))
    return texts


def build_pmc_figure(
    pmc: PmcSeries,
    activities: Sequence[ChartActivity],
    labels: PmcChartLabels,
    tokens: ThemeTokens,
) -> go.Figure:
    """PMC figure. Series, in this order: CTL, ATL, TSB (lines), daily load (bars)."""
    days = pmc.days
    figure = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        row_heights=[_TOP_ROW_SHARE, 1 - _TOP_ROW_SHARE],
        vertical_spacing=0.06,
    )
    unit = labels.load_unit
    hover = f"%{{x|%Y-%m-%d}}<br>%{{y:.1f}} {unit}<extra>%{{fullData.name}}</extra>"

    def line(name: str, values: object, color: str, dash: str = "solid") -> go.Scatter:
        return go.Scatter(
            x=days,
            y=values,
            name=name,
            mode="lines",
            line={"color": color, "width": _LINE_WIDTH, "dash": dash},
            hovertemplate=hover,
        )

    figure.add_trace(line(labels.ctl, pmc.ctl, tokens.ctl), row=1, col=1)
    figure.add_trace(line(labels.atl, pmc.atl, tokens.atl, _ATL_DASH), row=1, col=1)
    figure.add_trace(line(labels.tsb, pmc.tsb, tokens.tsb), row=2, col=1)
    figure.add_trace(
        go.Bar(
            x=days,
            y=daily_load_of(activities, days),
            name=labels.daily_load,
            marker={"color": tokens.daily_load, "opacity": tokens.daily_load_opacity},
            customdata=_day_hover_text(activities, days),
            hovertemplate=hover.replace("<extra>", "<br>%{customdata}<extra>"),
        ),
        row=1,
        col=1,
    )
    figure.update_xaxes(axis_style(tokens, ""))
    figure.update_xaxes(axis_style(tokens, labels.x_axis), type="date", row=2, col=1)
    figure.update_yaxes(
        axis_style(tokens, f"{labels.load_axis} ({unit})"),
        rangemode="tozero",  # bars start at zero
        row=1,
        col=1,
    )
    figure.update_yaxes(
        axis_style(tokens, f"{labels.form_axis} ({unit})"), zeroline=True, row=2, col=1
    )
    figure.update_layout(
        **base_layout(tokens),
        legend={"orientation": "h", "traceorder": "normal", "y": LEGEND_Y, "x": 0},
        margin={"l": 64, "r": 16, "t": 40, "b": 48},
        bargap=0.1,
        hovermode="x unified",
        annotations=_end_labels(pmc, labels, tokens),
    )
    return figure


def _end_labels(
    pmc: PmcSeries, labels: PmcChartLabels, tokens: ThemeTokens
) -> list[dict[str, object]]:
    """The name of CTL and ATL next to the last point: the higher line has its name above, the other
    below, so the two names do not overlap; the text colour is the theme's."""
    if len(pmc) == 0:
        return []
    last_day = pmc.days[-1]
    ends = ((labels.ctl, float(pmc.ctl[-1])), (labels.atl, float(pmc.atl[-1])))
    higher = max(ends, key=lambda end: end[1])
    return [
        {
            "xref": "x",
            "yref": "y",
            "x": last_day,
            "y": value,
            "text": name,
            "showarrow": False,
            "xanchor": "right",
            "yanchor": "bottom" if (name, value) == higher else "top",
            "yshift": _END_LABEL_SHIFT if (name, value) == higher else -_END_LABEL_SHIFT,
            "font": {"color": tokens.text},
        }
        for name, value in ends
    ]


def prepare_chart(
    pmc: PmcSeries,
    activities: Sequence[ChartActivity],
    labels: PmcChartLabels,
    tokens: ThemeTokens,
) -> PreparedChart:
    """Build figure and JSON: pure, heavy work (about 100 ms for the first chart), suited
    to a thread other than the UI."""
    figure = build_pmc_figure(pmc, activities, labels, tokens)
    return prepare_figure(figure, labels.chrome, EXPORT_FILE_STEM, tokens)
