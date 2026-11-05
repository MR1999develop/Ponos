# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Figura Plotly del PMC: funzione pura da `PmcSeries` e attivita a `go.Figure`.

Due pannelli con lo stesso asse delle date: in alto CTL, ATL e carico giornaliero (barre da
zero), in basso la forma (TSB), che puo essere negativa. La legenda segue l'ordine delle serie.
Il payload per la pagina e un JSON prodotto da `json.dumps` (`render_script`).
"""

from __future__ import annotations

import html
import json
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date

import plotly.graph_objects as go
import plotly.io as pio
from plotly.subplots import make_subplots

from ponos_coach.load.domain import PmcSeries
from ponos_coach.load.use_cases import ActivityEntry
from ponos_coach.ui.tokens import ThemeTokens

LOAD_UNIT = "hrTSS/day"  # unita del carico giornaliero e delle sue medie (CTL, ATL, TSB)
MAX_NAMES_PER_DAY = 5  # nomi di attivita mostrati nel suggerimento di una barra
_LINE_WIDTH = 2
_TOP_ROW_SHARE = 0.68
_EXPORT_FILE_STEM = "ponos-coach-pmc"

# Titoli dei bottoni della barra di Plotly: sono le chiavi del suo dizionario di lingua (il
# testo inglese che Plotly cerca), non testi mostrati. La vista le traduce con `tr()`.
PLOTLY_MODEBAR_KEYS: tuple[str, ...] = (
    "Zoom",
    "Pan",
    "Zoom in",
    "Zoom out",
    "Autoscale",
    "Reset axes",
    "Toggle Spike Lines",
    "Show closest data on hover",
    "Compare data on hover",
)
PLOTLY_LOCALE_NAME = "ponos"

type PmcFigure = go.Figure


@dataclass(frozen=True, slots=True)
class PmcChartLabels:
    """Testi del grafico, gia tradotti dalla vista.

    `modebar` traduce i bottoni della barra di Plotly: una voce per ogni `PLOTLY_MODEBAR_KEYS`.
    """

    x_axis: str
    load_axis: str
    form_axis: str
    ctl: str
    atl: str
    tsb: str
    daily_load: str
    export_png: str
    export_svg: str
    modebar: Mapping[str, str]

    def __post_init__(self) -> None:
        if set(self.modebar) != set(PLOTLY_MODEBAR_KEYS):
            raise ValueError("modebar must translate exactly the Plotly modebar titles")


def daily_load_of(activities: Sequence[ActivityEntry], days: Sequence[date]) -> list[float]:
    """Carico (pesato) di ogni giorno della serie: somma delle attivita di quel giorno locale."""
    totals: dict[date, float] = defaultdict(float)
    for entry in activities:
        totals[entry.local_day] += entry.load.load
    return [totals.get(day, 0.0) for day in days]


def _day_hover_text(activities: Sequence[ActivityEntry], days: Sequence[date]) -> list[str]:
    """Nomi dei file di ogni giorno come testo: le entita HTML neutralizzano `<` e `&`."""
    names: dict[date, list[str]] = defaultdict(list)
    for entry in activities:
        names[entry.local_day].append(html.escape(entry.file_name, quote=True))
    texts: list[str] = []
    for day in days:
        shown = names.get(day, [])
        extra = len(shown) - MAX_NAMES_PER_DAY
        lines = shown[:MAX_NAMES_PER_DAY] + ([f"+{extra}"] if extra > 0 else [])
        texts.append("<br>".join(lines))
    return texts


def _axis_style(tokens: ThemeTokens, title: str) -> dict[str, object]:
    return {
        "title": {"text": title},
        "gridcolor": tokens.grid,
        "linecolor": tokens.axis_line,
        "zerolinecolor": tokens.axis_line,
        "tickfont": {"color": tokens.text_muted},
        "title_font": {"color": tokens.text},
    }


def build_pmc_figure(
    pmc: PmcSeries,
    activities: Sequence[ActivityEntry],
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
    hover = f"%{{x|%Y-%m-%d}}<br>%{{y:.1f}} {LOAD_UNIT}<extra>%{{fullData.name}}</extra>"

    def line(name: str, values: object, color: str) -> go.Scatter:
        return go.Scatter(
            x=days,
            y=values,
            name=name,
            mode="lines",
            line={"color": color, "width": _LINE_WIDTH},
            hovertemplate=hover,
        )

    figure.add_trace(line(labels.ctl, pmc.ctl, tokens.ctl), row=1, col=1)
    figure.add_trace(line(labels.atl, pmc.atl, tokens.atl), row=1, col=1)
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
    figure.update_xaxes(_axis_style(tokens, ""))
    figure.update_xaxes(_axis_style(tokens, labels.x_axis), type="date", row=2, col=1)
    figure.update_yaxes(
        _axis_style(tokens, f"{labels.load_axis} ({LOAD_UNIT})"),
        rangemode="tozero",  # le barre partono da zero
        row=1,
        col=1,
    )
    figure.update_yaxes(
        _axis_style(tokens, f"{labels.form_axis} ({LOAD_UNIT})"), zeroline=True, row=2, col=1
    )
    figure.update_layout(
        paper_bgcolor=tokens.background,
        plot_bgcolor=tokens.background,
        font={"color": tokens.text},
        legend={"orientation": "h", "traceorder": "normal", "y": 1.08, "x": 0},
        margin={"l": 64, "r": 16, "t": 40, "b": 48},
        bargap=0.1,
        hovermode="x unified",
    )
    return figure


def render_script(figure: go.Figure, labels: PmcChartLabels) -> str:
    """Chiamata JavaScript che disegna la figura: l'argomento e un solo JSON (`json.dumps`)."""
    payload = {
        "figure": json.loads(pio.to_json(figure)),
        "labels": {"png": labels.export_png, "svg": labels.export_svg},
        # Dizionario di lingua di Plotly: la pagina lo registra, nessun testo si compone in JS.
        "locale": {"name": PLOTLY_LOCALE_NAME, "dictionary": dict(labels.modebar)},
        "filename": _EXPORT_FILE_STEM,
    }
    return f"ponosRender({json.dumps(payload, ensure_ascii=True, allow_nan=False)});"
