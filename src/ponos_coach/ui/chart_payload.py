# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Parts common to all Plotly charts: axis style, toolbar texts, payload.

Each figure is a pure function from data, already translated texts and tokens to `go.Figure`. To
the web page
the figure arrives as a single JSON produced by `json.dumps` (`render_script`): no data enters
as HTML and no text is composed in JavaScript.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass

import plotly.graph_objects as go
import plotly.io as pio

from ponos_coach.ui.tokens import ThemeTokens

# Titles of the Plotly toolbar buttons: they are the keys of its language dictionary (the English
# text Plotly looks up), not displayed texts. The view translates them with `tr()`.
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
LEGEND_Y = 1.08  # the horizontal legend sits above the chart area


@dataclass(frozen=True, slots=True)
class ChartChrome:
    """Toolbar texts of every chart, already translated by the view.

    `modebar` translates the Plotly toolbar buttons: one entry for each `PLOTLY_MODEBAR_KEYS`.
    """

    export_png: str
    export_svg: str
    modebar: Mapping[str, str]

    def __post_init__(self) -> None:
        if set(self.modebar) != set(PLOTLY_MODEBAR_KEYS):
            raise ValueError("modebar must translate exactly the Plotly modebar titles")


@dataclass(frozen=True, slots=True)
class PreparedChart:
    """A chart ready to show: figure, JavaScript call and background colour."""

    figure: go.Figure
    script: str
    background: str


def axis_style(tokens: ThemeTokens, title: str) -> dict[str, object]:
    """Style of an axis from the theme tokens: title, grid, line and ticks."""
    return {
        "title": {"text": title},
        "gridcolor": tokens.grid,
        "linecolor": tokens.axis_line,
        "zerolinecolor": tokens.axis_line,
        "tickfont": {"color": tokens.text_muted},
        "title_font": {"color": tokens.text},
    }


def base_layout(tokens: ThemeTokens) -> dict[str, object]:
    """Background and font from the tokens, the same in every figure."""
    return {
        "paper_bgcolor": tokens.background,
        "plot_bgcolor": tokens.background,
        "font": {"color": tokens.text},
    }


def enable_wheel_navigation(figure: go.Figure) -> None:
    """The wheel zooms and a left drag pans the x axis; the y axes stay as drawn."""
    figure.update_layout(dragmode="pan")
    figure.update_yaxes(fixedrange=True)


def render_script(
    figure: go.Figure,
    chrome: ChartChrome,
    filename: str,
    *,
    hover: bool = False,
    zoomable: bool = False,
    linked_x: bool = False,
) -> str:
    """JavaScript call that draws the figure: the argument is a single JSON (`json.dumps`).

    With `hover` the page reports the point under the pointer (see `ChartView.hovered`); with
    `zoomable` the wheel zooms and a drag pans the x axis; with `linked_x` (zoomable too) the page
    also reports the range of dates it shows (see `ChartView.x_range_changed`).
    """
    payload: dict[str, object] = {
        "figure": json.loads(pio.to_json(figure)),
        "labels": {"png": chrome.export_png, "svg": chrome.export_svg},
        # Plotly language dictionary: the page registers it, no text is composed in JS.
        "locale": {"name": PLOTLY_LOCALE_NAME, "dictionary": dict(chrome.modebar)},
        "filename": filename,
    }
    if hover:
        payload["hover"] = True
    if zoomable or linked_x:
        payload["zoom"] = True
    if linked_x:
        payload["linkedX"] = True
    return f"ponosRender({json.dumps(payload, ensure_ascii=True, allow_nan=False)});"


def prepare_figure(
    figure: go.Figure,
    chrome: ChartChrome,
    filename: str,
    tokens: ThemeTokens,
    *,
    hover: bool = False,
    zoomable: bool = False,
    linked_x: bool = False,
) -> PreparedChart:
    """Figure, JSON and background together: pure, heavy work, suited to a non-UI thread.

    With `zoomable` (or `linked_x`) the x axis moves with the wheel and a drag
    (`enable_wheel_navigation`).
    """
    if zoomable or linked_x:
        enable_wheel_navigation(figure)
    return PreparedChart(
        figure,
        render_script(figure, chrome, filename, hover=hover, zoomable=zoomable, linked_x=linked_x),
        tokens.background,
    )
