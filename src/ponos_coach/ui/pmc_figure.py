# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Plotly PMC figure: pure function from `PmcSeries` and activities to `go.Figure`.

Two panels with the same date axis: on top CTL, ATL and daily load (from zero, drawn as one
stepped area: a bar per day costs the browser a shape each, and with years of days zooming
lagged), below the form (TSB), which can be negative. The legend follows the series order.
CTL and ATL are told apart even without colour: ATL is dashed and each line carries its name
next to the last point.
The page payload is a JSON produced by `json.dumps` (`render_script`).
"""

from __future__ import annotations

import html
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta

import plotly.graph_objects as go
from plotly.subplots import make_subplots

from ponos_coach.goals.domain import Priority
from ponos_coach.load.domain import PmcSeries
from ponos_coach.planning.domain import PmcPoint
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
_PROJECTION_DASH = "dot"  # the plan's part: dotted; the real part is solid (CTL, TSB) or dashed
_PAST_DAYS_SHOWN = 90  # with a projection the chart opens on the last three months and the plan
_MARKER_SIZE = 11
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
    # Texts of the projection (used only with one): whole names, translated by the view.
    ctl_plan: str = "Fitness (CTL), plan"
    atl_plan: str = "Fatigue (ATL), plan"
    tsb_plan: str = "Form (TSB), plan"
    races: str = "Races"
    race_hint: str = "Race {priority}: {name}"  # {priority}: A, B or C; {name}: the race
    today: str = "Today"


@dataclass(frozen=True, slots=True)
class ProjectedRace:
    """A race of the plan, as a marker on the projection: day, priority and a name to show."""

    day: date
    priority: Priority
    name: str


@dataclass(frozen=True, slots=True)
class PmcProjection:
    """The future part of the PMC: the plan's points (CTL and ATL at the end of each day, TSB of
    the morning) and the races on them."""

    points: tuple[PmcPoint, ...]
    races: tuple[ProjectedRace, ...] = ()


def _with_opacity(color: str, opacity: float) -> str:
    """A `#rrggbb` colour with an opacity, as CSS `rgba()`."""
    red, green, blue = (int(color[i : i + 2], 16) for i in (1, 3, 5))
    return f"rgba({red},{green},{blue},{opacity})"


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
    projection: PmcProjection | None = None,
) -> go.Figure:
    """PMC figure. Series, in this order: CTL, ATL, TSB (lines), daily load (bars); with a
    projection then the plan's CTL, ATL and TSB (dotted, named "plan") and the races."""
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
        go.Scatter(
            x=days,
            y=daily_load_of(activities, days),
            name=labels.daily_load,
            mode="lines",
            line={"color": tokens.daily_load, "width": 0, "shape": "hv"},
            fill="tozeroy",  # from zero, like the bars it replaces
            fillcolor=_with_opacity(tokens.daily_load, tokens.daily_load_opacity),
            customdata=_day_hover_text(activities, days),
            hovertemplate=hover.replace("<extra>", "<br>%{customdata}<extra>"),
        ),
        row=1,
        col=1,
    )
    has_projection = projection is not None and len(projection.points) > 0
    if projection is not None and has_projection:
        _add_projection(figure, pmc, projection, labels, tokens, hover)
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
    if projection is not None and has_projection:
        _frame_projection(figure, pmc, projection, labels, tokens)
    return figure


def _add_projection(
    figure: go.Figure,
    pmc: PmcSeries,
    projection: PmcProjection,
    labels: PmcChartLabels,
    tokens: ThemeTokens,
    hover: str,
) -> None:
    """The plan's CTL, ATL and TSB after the last real day (joined to it, so the line does not
    break), dotted and named "plan"; and the races as markers on the CTL line."""
    points = projection.points
    days = ([pmc.days[-1]] if len(pmc) else []) + [point.day for point in points]

    def series(name: str, joined: list[float], color: str, row: int) -> None:
        figure.add_trace(
            go.Scatter(
                x=days,
                y=joined,
                name=name,
                mode="lines",
                line={"color": color, "width": _LINE_WIDTH, "dash": _PROJECTION_DASH},
                hovertemplate=hover,
            ),
            row=row,
            col=1,
        )

    ends = [(float(pmc.ctl[-1]), float(pmc.atl[-1]), float(pmc.tsb[-1]))] if len(pmc) else []
    series(labels.ctl_plan, [end[0] for end in ends] + [p.ctl for p in points], tokens.ctl, 1)
    series(labels.atl_plan, [end[1] for end in ends] + [p.atl for p in points], tokens.atl, 1)
    series(labels.tsb_plan, [end[2] for end in ends] + [p.tsb for p in points], tokens.tsb, 2)
    _add_race_markers(figure, projection, labels, tokens)


def _add_race_markers(
    figure: go.Figure,
    projection: PmcProjection,
    labels: PmcChartLabels,
    tokens: ThemeTokens,
) -> None:
    """A marker with the priority letter on the projected CTL of each race day."""
    ctl_of_day = {point.day: point.ctl for point in projection.points}
    races = [race for race in projection.races if race.day in ctl_of_day]
    if not races:
        return
    figure.add_trace(
        go.Scatter(
            x=[race.day for race in races],
            y=[ctl_of_day[race.day] for race in races],
            name=labels.races,
            mode="markers+text",
            text=[race.priority.value for race in races],
            textposition="top center",
            textfont={"color": tokens.text},
            marker={
                "symbol": "diamond",
                "size": _MARKER_SIZE,
                "color": tokens.categories[0],
                "line": {"color": tokens.text, "width": 1},
            },
            customdata=[
                labels.race_hint.format(priority=race.priority.value, name=html.escape(race.name))
                for race in races
            ],
            hovertemplate="%{x|%Y-%m-%d}<br>%{customdata}<extra></extra>",
        ),
        row=1,
        col=1,
    )


def _frame_projection(
    figure: go.Figure,
    pmc: PmcSeries,
    projection: PmcProjection,
    labels: PmcChartLabels,
    tokens: ThemeTokens,
) -> None:
    """Open the chart on the last three months and the plan; a dotted line marks where the real
    data ends and the plan begins."""
    if len(pmc) == 0:
        return
    last_real = pmc.days[-1]
    first_shown = max(pmc.days[0], last_real - timedelta(days=_PAST_DAYS_SHOWN))
    figure.update_xaxes(range=[first_shown, projection.points[-1].day])
    figure.add_shape(
        type="line",
        xref="x",
        yref="paper",
        x0=last_real,
        x1=last_real,
        y0=0,
        y1=1,
        line={"color": tokens.axis_line, "width": 1, "dash": _PROJECTION_DASH},
    )
    figure.add_annotation(
        xref="x",
        yref="paper",
        x=last_real,
        y=1,
        text=labels.today,
        showarrow=False,
        xanchor="right",
        yanchor="bottom",
        font={"color": tokens.text_muted},
    )


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
    projection: PmcProjection | None = None,
) -> PreparedChart:
    """Build figure and JSON: pure, heavy work (about 100 ms for the first chart), suited
    to a thread other than the UI."""
    figure = build_pmc_figure(pmc, activities, labels, tokens, projection)
    return prepare_figure(figure, labels.chrome, EXPORT_FILE_STEM, tokens, linked_x=True)
