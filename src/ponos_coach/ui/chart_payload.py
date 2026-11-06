# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Parti comuni a tutti i grafici Plotly: stile degli assi, testi della barra, payload.

Ogni figura è una funzione pura da dati, testi già tradotti e token a `go.Figure`. Alla pagina web
la figura arriva come un solo JSON prodotto da `json.dumps` (`render_script`): nessun dato entra
come HTML e nessun testo si compone in JavaScript.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass

import plotly.graph_objects as go
import plotly.io as pio

from ponos_coach.ui.tokens import ThemeTokens

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
LEGEND_Y = 1.08  # la legenda orizzontale sta sopra l'area del grafico


@dataclass(frozen=True, slots=True)
class ChartChrome:
    """Testi della barra di ogni grafico, già tradotti dalla vista.

    `modebar` traduce i bottoni della barra di Plotly: una voce per ogni `PLOTLY_MODEBAR_KEYS`.
    """

    export_png: str
    export_svg: str
    modebar: Mapping[str, str]

    def __post_init__(self) -> None:
        if set(self.modebar) != set(PLOTLY_MODEBAR_KEYS):
            raise ValueError("modebar must translate exactly the Plotly modebar titles")


@dataclass(frozen=True, slots=True)
class PreparedChart:
    """Un grafico pronto da mostrare: figura, chiamata JavaScript e colore di fondo."""

    figure: go.Figure
    script: str
    background: str


def axis_style(tokens: ThemeTokens, title: str) -> dict[str, object]:
    """Stile di un asse dai token del tema: titolo, griglia, linea e tacche."""
    return {
        "title": {"text": title},
        "gridcolor": tokens.grid,
        "linecolor": tokens.axis_line,
        "zerolinecolor": tokens.axis_line,
        "tickfont": {"color": tokens.text_muted},
        "title_font": {"color": tokens.text},
    }


def base_layout(tokens: ThemeTokens) -> dict[str, object]:
    """Fondo e carattere dei token, uguali in ogni figura."""
    return {
        "paper_bgcolor": tokens.background,
        "plot_bgcolor": tokens.background,
        "font": {"color": tokens.text},
    }


def render_script(figure: go.Figure, chrome: ChartChrome, filename: str) -> str:
    """Chiamata JavaScript che disegna la figura: l'argomento è un solo JSON (`json.dumps`)."""
    payload = {
        "figure": json.loads(pio.to_json(figure)),
        "labels": {"png": chrome.export_png, "svg": chrome.export_svg},
        # Dizionario di lingua di Plotly: la pagina lo registra, nessun testo si compone in JS.
        "locale": {"name": PLOTLY_LOCALE_NAME, "dictionary": dict(chrome.modebar)},
        "filename": filename,
    }
    return f"ponosRender({json.dumps(payload, ensure_ascii=True, allow_nan=False)});"


def prepare_figure(
    figure: go.Figure, chrome: ChartChrome, filename: str, tokens: ThemeTokens
) -> PreparedChart:
    """Figura, JSON e fondo insieme: lavoro puro e pesante, adatto a un thread che non è la UI."""
    return PreparedChart(figure, render_script(figure, chrome, filename), tokens.background)
