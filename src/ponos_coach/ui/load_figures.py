# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Figure Plotly del carico settimanale per sport e del tempo nelle zone di FC.

Funzioni pure da `WeeklyLoad` e `WeeklyZones` (già aggregati dal dominio) a `go.Figure`. Barre
impilate che partono da zero, ultime 26 settimane (si ingrandisce con la barra del grafico). Il
colore di uno sport è sempre lo stesso; le zone usano una scala sequenziale a luminosità
crescente, quindi l'ordine si legge anche senza i colori (legenda e suggerimenti dicono il nome).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

import numpy as np
import plotly.graph_objects as go

from ponos_coach.activities.domain import Sport
from ponos_coach.load.domain import ZONE_COUNT, ZONE_STARTS_PERCENT, WeeklyLoad, WeeklyZones
from ponos_coach.ui.chart_payload import (
    LEGEND_Y,
    ChartChrome,
    PreparedChart,
    axis_style,
    base_layout,
    prepare_figure,
)
from ponos_coach.ui.tokens import ThemeTokens

CHART_WEEKS = 26  # settimane mostrate: circa sei mesi
WEEKLY_LOAD_UNIT = "hrTSS/week"
ZONE_TIME_UNIT = "h"
LOAD_EXPORT_STEM = "ponos-coach-weekly-load"
ZONES_EXPORT_STEM = "ponos-coach-zones"
_SECONDS_PER_HOUR = 3600.0
_BAR_GAP = 0.15
_SEPARATOR_WIDTH = 1  # linea del colore di fondo tra segmenti: separa anche senza colore
_LAST_ZONE_INDEX = ZONE_COUNT - 1

# Posto di ogni sport nei sette colori di categoria dei token: lo sport tiene sempre il suo.
SPORT_COLOR_SLOT: Mapping[Sport, int] = {
    Sport.RUNNING: 0,
    Sport.TRAIL: 1,
    Sport.CYCLING: 2,
    Sport.HIKING: 3,
    Sport.WALKING: 4,
    Sport.STRENGTH: 5,
    Sport.OTHER: 6,
}


@dataclass(frozen=True, slots=True)
class WeeklyLoadLabels:
    """Testi del grafico del carico settimanale, già tradotti dalla vista."""

    x_axis: str
    y_axis: str
    sports: Mapping[Sport, str]
    chrome: ChartChrome


@dataclass(frozen=True, slots=True)
class WeeklyZonesLabels:
    """Testi del grafico del tempo nelle zone, già tradotti dalla vista."""

    x_axis: str
    y_axis: str
    legend_title: str
    chrome: ChartChrome


def sport_color(sport: Sport, tokens: ThemeTokens) -> str:
    """Colore di categoria di uno sport nel tema."""
    return tokens.categories[SPORT_COLOR_SLOT[sport]]


def zone_label(index: int) -> str:
    """Nome breve di una zona con i suoi confini in percentuale della soglia (indice da 0).

    `Z1 <85%`, `Z2 85-89%`, ..., `Z5 ≥100%`. I confini vengono da `ZONE_STARTS_PERCENT`, la sola
    fonte; sigle e simboli sono uguali in ogni lingua.
    """
    name = f"Z{index + 1}"
    if index == 0:
        return f"{name} <{ZONE_STARTS_PERCENT[0]}%"
    if index == _LAST_ZONE_INDEX:
        return f"{name} ≥{ZONE_STARTS_PERCENT[-1]}%"
    return f"{name} {ZONE_STARTS_PERCENT[index - 1]}\u2013{ZONE_STARTS_PERCENT[index] - 1}%"


def _style_weekly_layout(
    figure: go.Figure, tokens: ThemeTokens, x_title: str, y_title: str, legend_title: str = ""
) -> None:
    figure.update_xaxes(axis_style(tokens, x_title), type="date", hoverformat="%Y-%m-%d")
    figure.update_yaxes(axis_style(tokens, y_title), rangemode="tozero")  # barre da zero
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


def build_weekly_load_figure(
    weekly: WeeklyLoad, labels: WeeklyLoadLabels, tokens: ThemeTokens
) -> go.Figure:
    """Carico per settimana, un segmento per sport (solo gli sport presenti nelle ultime
    `CHART_WEEKS` settimane), nell'ordine di `Sport`."""
    weeks = weekly.weeks[-CHART_WEEKS:]
    load = weekly.load[-CHART_WEEKS:, :]
    figure = go.Figure()
    hover = f"%{{y:.0f}} {WEEKLY_LOAD_UNIT}<extra>%{{fullData.name}}</extra>"
    for column, sport in enumerate(weekly.sports):
        values = load[:, column]
        if not np.any(values > 0):
            continue
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
    _style_weekly_layout(figure, tokens, labels.x_axis, f"{labels.y_axis} ({WEEKLY_LOAD_UNIT})")
    return figure


def build_weekly_zones_figure(
    weekly: WeeklyZones, labels: WeeklyZonesLabels, tokens: ThemeTokens
) -> go.Figure:
    """Ore per zona e settimana (Z1 in basso, Z5 in alto), ultime `CHART_WEEKS` settimane."""
    weeks = weekly.weeks[-CHART_WEEKS:]
    hours = weekly.seconds[-CHART_WEEKS:, :] / _SECONDS_PER_HOUR
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
        f"{labels.y_axis} ({ZONE_TIME_UNIT}/week)",
        labels.legend_title,
    )
    return figure


def prepare_weekly_load_chart(
    weekly: WeeklyLoad, labels: WeeklyLoadLabels, tokens: ThemeTokens
) -> PreparedChart:
    """Figura e JSON del carico settimanale (lavoro puro, adatto a un thread che non è la UI)."""
    figure = build_weekly_load_figure(weekly, labels, tokens)
    return prepare_figure(figure, labels.chrome, LOAD_EXPORT_STEM, tokens)


def prepare_weekly_zones_chart(
    weekly: WeeklyZones, labels: WeeklyZonesLabels, tokens: ThemeTokens
) -> PreparedChart:
    """Figura e JSON del tempo nelle zone (lavoro puro, adatto a un thread che non è la UI)."""
    figure = build_weekly_zones_figure(weekly, labels, tokens)
    return prepare_figure(figure, labels.chrome, ZONES_EXPORT_STEM, tokens)
