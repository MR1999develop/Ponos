# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Figura Plotly della FC di un'attività: curva nel tempo con le bande delle zone.

Funzione pura da `HeartRateTrace` e confini di zona (già calcolati dal dominio) a `go.Figure`.
Le bande stanno dietro la curva e portano il nome della zona sul bordo destro; i campioni scartati
dal calcolo del carico (fuori da FC minima e massima) sono evidenziati con una croce, non solo con
il colore. Le curve molto lunghe si diradano a `MAX_TRACE_POINTS` punti, tenendo tutti gli scarti.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import plotly.graph_objects as go
from numpy.typing import NDArray

from ponos_coach.load.activity_detail import HeartRateTrace
from ponos_coach.load.domain import HeartRateLimits
from ponos_coach.ui.chart_payload import (
    LEGEND_Y,
    ChartChrome,
    PreparedChart,
    axis_style,
    base_layout,
    prepare_figure,
)
from ponos_coach.ui.load_figures import zone_label
from ponos_coach.ui.tokens import ThemeTokens

HEART_RATE_UNIT = "bpm"
TIME_UNIT = "min"
HR_EXPORT_STEM = "ponos-coach-heart-rate"
MAX_TRACE_POINTS = 3000  # punti della curva mandati alla pagina
_SECONDS_PER_MINUTE = 60.0
_LINE_WIDTH = 1.5
_MARKER_SIZE = 7
_AXIS_PADDING_BPM = 5
_LABEL_X = 0.995  # etichette di zona vicino al bordo destro dell'area del grafico


@dataclass(frozen=True, slots=True)
class HeartRateLabels:
    """Testi del grafico della FC, già tradotti dalla vista."""

    x_axis: str
    y_axis: str
    heart_rate: str
    discarded: str
    chrome: ChartChrome


def thinned_indices(count: int, keep: NDArray[np.bool_]) -> NDArray[np.intp]:
    """Indici dei punti da disegnare: al massimo `MAX_TRACE_POINTS` equidistanti, più gli indici
    di `keep` (campioni scartati), anch'essi diradati se da soli superano il tetto."""
    if count <= MAX_TRACE_POINTS:
        return np.arange(count, dtype=np.intp)
    even = np.linspace(0, count - 1, MAX_TRACE_POINTS).astype(np.intp)
    flagged = np.flatnonzero(keep).astype(np.intp)
    if flagged.size > MAX_TRACE_POINTS:
        flagged = flagged[:: math.ceil(flagged.size / MAX_TRACE_POINTS)]
    return np.union1d(even, flagged).astype(np.intp)


def _axis_range(trace: HeartRateTrace, limits: HeartRateLimits) -> tuple[float, float]:
    """Intervallo di FC dell'asse: i limiti del profilo, allargati ai campioni disegnati."""
    low: float = limits.min_bpm
    high: float = limits.max_bpm
    if trace.bpm.size:
        low = min(low, float(trace.bpm.min()))
        high = max(high, float(trace.bpm.max()))
    return low - _AXIS_PADDING_BPM, high + _AXIS_PADDING_BPM


def zone_bands(
    starts_bpm: tuple[float, float, float, float], low: float, high: float
) -> list[tuple[int, float, float]]:
    """Bande (indice di zona, da, a) dentro `[low, high]`; le zone fuori scala non compaiono."""
    edges = [low, *starts_bpm, high]
    bands: list[tuple[int, float, float]] = []
    for zone in range(len(edges) - 1):
        start = min(max(edges[zone], low), high)
        end = min(max(edges[zone + 1], low), high)
        if end > start:
            bands.append((zone, start, end))
    return bands


def build_heart_rate_figure(
    trace: HeartRateTrace,
    limits: HeartRateLimits,
    zone_starts_bpm: tuple[float, float, float, float],
    labels: HeartRateLabels,
    tokens: ThemeTokens,
) -> go.Figure:
    """Curva della FC (serie 0) e, se ce ne sono, i campioni scartati (serie 1)."""
    keep = trace.discarded
    chosen = thinned_indices(trace.bpm.size, keep)
    minutes = trace.seconds[chosen] / _SECONDS_PER_MINUTE
    figure = go.Figure()
    hover = (
        f"%{{x:.1f}} {TIME_UNIT}<br>%{{y:.0f}} {HEART_RATE_UNIT}<extra>%{{fullData.name}}</extra>"
    )
    figure.add_trace(
        go.Scatter(
            x=minutes,
            y=trace.bpm[chosen],
            name=labels.heart_rate,
            mode="lines",
            line={"color": tokens.ctl, "width": _LINE_WIDTH},
            hovertemplate=hover,
        )
    )
    flagged = chosen[keep[chosen]]
    if flagged.size:
        figure.add_trace(
            go.Scatter(
                x=trace.seconds[flagged] / _SECONDS_PER_MINUTE,
                y=trace.bpm[flagged],
                name=labels.discarded,
                mode="markers",
                marker={"color": tokens.error, "symbol": "x", "size": _MARKER_SIZE},
                hovertemplate=hover,
            )
        )
    low, high = _axis_range(trace, limits)
    shapes: list[dict[str, object]] = []
    annotations: list[dict[str, object]] = []
    for zone, start, end in zone_bands(zone_starts_bpm, low, high):
        shapes.append(
            {
                "type": "rect",
                "xref": "paper",
                "x0": 0,
                "x1": 1,
                "yref": "y",
                "y0": start,
                "y1": end,
                "fillcolor": tokens.zones[zone],
                "opacity": tokens.band_opacity,
                "line": {"width": 0},
                "layer": "below",
            }
        )
        annotations.append(
            {
                "xref": "paper",
                "x": _LABEL_X,
                "xanchor": "right",
                "yref": "y",
                "y": (start + end) / 2,
                "text": zone_label(zone),
                "showarrow": False,
                "font": {"color": tokens.text_muted},
            }
        )
    figure.update_xaxes(axis_style(tokens, f"{labels.x_axis} ({TIME_UNIT})"), rangemode="tozero")
    figure.update_yaxes(
        axis_style(tokens, f"{labels.y_axis} ({HEART_RATE_UNIT})"), range=[low, high]
    )
    figure.update_layout(
        **base_layout(tokens),
        shapes=shapes,
        annotations=annotations,
        legend={"orientation": "h", "traceorder": "normal", "y": LEGEND_Y, "x": 0},
        margin={"l": 64, "r": 16, "t": 48, "b": 48},
        hovermode="x",
    )
    return figure


def prepare_heart_rate_chart(
    trace: HeartRateTrace,
    limits: HeartRateLimits,
    zone_starts_bpm: tuple[float, float, float, float],
    labels: HeartRateLabels,
    tokens: ThemeTokens,
) -> PreparedChart:
    """Figura e JSON della FC (lavoro puro, adatto a un thread che non è la UI)."""
    figure = build_heart_rate_figure(trace, limits, zone_starts_bpm, labels, tokens)
    return prepare_figure(figure, labels.chrome, HR_EXPORT_STEM, tokens)
