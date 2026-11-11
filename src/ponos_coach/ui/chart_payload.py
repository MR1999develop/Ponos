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
from enum import Enum

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
# Space left above and below a curve, as a fraction of the values it spans (the user's request
# of 2026-11-11: the whole curve always visible, with a slight margin). The page uses the same
# value when it fits the y axis to the part in view.
Y_MARGIN_FRACTION = 0.06


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


def padded_range(low: float, high: float) -> tuple[float, float]:
    """The y range that shows every value from `low` to `high` with `Y_MARGIN_FRACTION` above
    and below (a flat series gets a margin from its own size)."""
    span = high - low or abs(high) or 1.0
    return low - span * Y_MARGIN_FRACTION, high + span * Y_MARGIN_FRACTION


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


HOVER_FONT_SIZE = 11  # px: the box under the pointer stays small beside the chart's text
HOVER_OPACITY = 0.82  # the chart shows through the box under the pointer


def _translucent(hex_color: str, opacity: float) -> str:
    """`#RRGGBB` as `rgba(r, g, b, opacity)`."""
    red, green, blue = (int(hex_color[i : i + 2], 16) for i in (1, 3, 5))
    return f"rgba({red}, {green}, {blue}, {opacity})"


def base_layout(tokens: ThemeTokens) -> dict[str, object]:
    """Background and font from the tokens, the same in every figure. The box shown under the
    pointer is small, see-through and with a thin border (user's request of 2026-11-10: it was
    too intrusive)."""
    return {
        "paper_bgcolor": tokens.background,
        "plot_bgcolor": tokens.background,
        "font": {"color": tokens.text},
        "hoverlabel": {
            "bgcolor": _translucent(tokens.background, HOVER_OPACITY),
            "bordercolor": tokens.grid,
            "font": {"size": HOVER_FONT_SIZE, "color": tokens.text},
            "align": "left",
        },
    }


class XNavigation(Enum):
    """How the user moves along a chart's x axis."""

    FIXED = "fixed"  # only the toolbar
    WHEEL = "wheel"  # the wheel zooms and a left drag pans
    LINKED = "linked"  # as WHEEL, and the page reports the range it shows (charts kept in step)
    LINKED_FIXED_Y = "linked-fixed-y"  # as LINKED, and the y range stays as the figure sets it


class PointerReport(Enum):
    """What the page tells about the user's pointer."""

    NONE = "none"
    INDEX = "index"  # the index of the point under it (see `ChartView.hovered`)
    # Linked charts: the x under it (see `ChartView.hovered_x`), and the hover shown at an x
    # it is given (see `ChartView.show_hover_at`).
    LINKED_X = "linked-x"


def enable_wheel_navigation(figure: go.Figure) -> None:
    """The wheel zooms and a left drag pans the x axis; the y axes stay as drawn."""
    figure.update_layout(dragmode="pan")
    figure.update_yaxes(fixedrange=True)


def render_script(
    figure: go.Figure,
    chrome: ChartChrome,
    filename: str,
    *,
    pointer: PointerReport = PointerReport.NONE,
    navigation: XNavigation = XNavigation.FIXED,
) -> str:
    """JavaScript call that draws the figure: the argument is a single JSON (`json.dumps`).

    `pointer` says what the page reports about the pointer (`PointerReport`); with
    `navigation` WHEEL the wheel zooms and a drag pans the x axis; with LINKED the page also
    reports the range it shows (see `ChartView.x_range_changed`); with LINKED_FIXED_Y the y range
    does not follow the part in view and the right drag does not stretch it (an axis with its own
    ticks, such as a pace written `m:ss`).
    """
    payload: dict[str, object] = {
        "figure": json.loads(pio.to_json(figure)),
        "labels": {"png": chrome.export_png, "svg": chrome.export_svg},
        # Plotly language dictionary: the page registers it, no text is composed in JS.
        "locale": {"name": PLOTLY_LOCALE_NAME, "dictionary": dict(chrome.modebar)},
        "filename": filename,
        "yMargin": Y_MARGIN_FRACTION,
    }
    if pointer is PointerReport.INDEX:
        payload["hover"] = True
    if pointer is PointerReport.LINKED_X:
        payload["linkedHover"] = True
    if navigation is not XNavigation.FIXED:
        payload["zoom"] = True
    if navigation in (XNavigation.LINKED, XNavigation.LINKED_FIXED_Y):
        payload["linkedX"] = True
    if navigation is XNavigation.LINKED_FIXED_Y:
        payload["fixedY"] = True
    return f"ponosRender({json.dumps(payload, ensure_ascii=True, allow_nan=False)});"


def prepare_figure(
    figure: go.Figure,
    chrome: ChartChrome,
    filename: str,
    tokens: ThemeTokens,
    *,
    pointer: PointerReport = PointerReport.NONE,
    navigation: XNavigation = XNavigation.FIXED,
) -> PreparedChart:
    """Figure, JSON and background together: pure, heavy work, suited to a non-UI thread.

    Unless `navigation` is FIXED, the x axis moves with the wheel and a drag
    (`enable_wheel_navigation`).
    """
    if navigation is not XNavigation.FIXED:
        enable_wheel_navigation(figure)
    return PreparedChart(
        figure,
        render_script(figure, chrome, filename, pointer=pointer, navigation=navigation),
        tokens.background,
    )
