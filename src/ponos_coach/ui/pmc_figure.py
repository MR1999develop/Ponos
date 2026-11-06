# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Figura Plotly del PMC: funzione pura da `PmcSeries` e attivita a `go.Figure`.

Due pannelli con lo stesso asse delle date: in alto CTL, ATL e carico giornaliero (barre da
zero), in basso la forma (TSB), che puo essere negativa. La legenda segue l'ordine delle serie.
CTL e ATL si distinguono anche senza il colore: ATL e tratteggiata e ogni linea porta il suo nome
accanto all'ultimo punto.
Il payload per la pagina e un JSON prodotto da `json.dumps` (`render_script`).
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

LOAD_UNIT = "hrTSS per day"  # unita del carico giornaliero e delle sue medie (CTL, ATL, TSB)
MAX_NAMES_PER_DAY = 5  # nomi di attivita mostrati nel suggerimento di una barra
_LINE_WIDTH = 2
_ATL_DASH = "dash"  # ATL e tratteggiata: CTL e ATL differiscono anche senza il colore
_END_LABEL_SHIFT = 4  # pixel tra l'ultimo punto e il nome della linea
_TOP_ROW_SHARE = 0.68
EXPORT_FILE_STEM = "ponos-coach-pmc"


@dataclass(frozen=True, slots=True)
class ChartActivity:
    """Ciò che il grafico sa di un'attività: giorno locale, carico e nome da mostrare."""

    local_day: date
    load: float
    name: str


@dataclass(frozen=True, slots=True)
class PmcChartLabels:
    """Testi del grafico, già tradotti dalla vista."""

    x_axis: str
    load_axis: str
    form_axis: str
    ctl: str
    atl: str
    tsb: str
    daily_load: str
    chrome: ChartChrome
    load_unit: str = LOAD_UNIT  # "hrTSS per day", tradotto come frase intera


def daily_load_of(activities: Sequence[ChartActivity], days: Sequence[date]) -> list[float]:
    """Carico (pesato) di ogni giorno della serie: somma delle attivita di quel giorno locale."""
    totals: dict[date, float] = defaultdict(float)
    for entry in activities:
        totals[entry.local_day] += entry.load
    return [totals.get(day, 0.0) for day in days]


def _day_hover_text(activities: Sequence[ChartActivity], days: Sequence[date]) -> list[str]:
    """Nomi dei file di ogni giorno come testo: le entita HTML neutralizzano `<` e `&`."""
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
    """Figura del PMC. Serie, in quest'ordine: CTL, ATL, TSB (linee), carico giornaliero (barre)."""
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
        rangemode="tozero",  # le barre partono da zero
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
    """Il nome di CTL e di ATL accanto all'ultimo punto: la linea più alta ha il nome sopra, l'altra
    sotto, così i due nomi non si sovrappongono; il colore del testo è quello del tema."""
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
    """Costruisce figura e JSON: lavoro puro e pesante (circa 100 ms al primo grafico), adatto
    a un thread che non è quello della UI."""
    figure = build_pmc_figure(pmc, activities, labels, tokens)
    return prepare_figure(figure, labels.chrome, EXPORT_FILE_STEM, tokens)
