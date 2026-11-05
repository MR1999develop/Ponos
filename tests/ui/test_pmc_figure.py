# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""T-UI-04 (parte pura): la figura del PMC ha le serie, gli assi e i temi attesi."""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

import numpy as np
import pytest

from ponos_coach.ui.pmc_figure import (
    PLOTLY_LOCALE_NAME,
    PLOTLY_MODEBAR_KEYS,
    build_pmc_figure,
    daily_load_of,
    render_script,
)
from ponos_coach.ui.tokens import Theme, tokens_for
from tests.ui.helpers import FIRST_DAY, LABELS, entry, small_history


def test_series_are_ctl_atl_tsb_lines_then_daily_load_bars_in_legend_order() -> None:
    pmc, entries = small_history()

    figure = build_pmc_figure(pmc, entries, LABELS, tokens_for(Theme.LIGHT))

    assert [(t.name, t.type) for t in figure.data] == [
        ("Fitness (CTL)", "scatter"),
        ("Fatigue (ATL)", "scatter"),
        ("Form (TSB)", "scatter"),
        ("Daily load", "bar"),
    ]
    assert figure.layout.legend.traceorder == "normal"


def test_line_values_are_the_pmc_series_and_bars_the_daily_loads() -> None:
    pmc, entries = small_history()

    figure = build_pmc_figure(pmc, entries, LABELS, tokens_for(Theme.LIGHT))

    ctl, atl, tsb, bars = figure.data
    np.testing.assert_allclose(ctl.y, pmc.ctl)
    np.testing.assert_allclose(atl.y, pmc.atl)
    np.testing.assert_allclose(tsb.y, pmc.tsb)
    assert list(bars.y) == [80.0, 0.0, 0.0, 80.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
    assert list(ctl.x) == list(pmc.days)


def test_axes_have_names_with_units_and_bars_start_at_zero() -> None:
    pmc, entries = small_history()

    layout = build_pmc_figure(pmc, entries, LABELS, tokens_for(Theme.LIGHT)).layout

    assert layout.xaxis2.title.text == "Date"
    assert layout.yaxis.title.text == "Load (hrTSS/day)"
    assert layout.yaxis2.title.text == "Form (hrTSS/day)"
    assert layout.yaxis.rangemode == "tozero"


def test_series_use_okabe_ito_colors_and_themes_change_the_surface() -> None:
    pmc, entries = small_history()
    light = build_pmc_figure(pmc, entries, LABELS, tokens_for(Theme.LIGHT))
    dark = build_pmc_figure(pmc, entries, LABELS, tokens_for(Theme.DARK))

    assert light.layout.paper_bgcolor == "#FFFFFF"
    assert dark.layout.paper_bgcolor == "#1E1F22"
    assert light.layout.font.color != dark.layout.font.color
    okabe_ito = {"#0072B2", "#D55E00", "#009E73", "#56B4E9", "#E69F00", "#F0E442", "#CC79A7"}
    for figure in (light, dark):
        line_colors = {trace.line.color for trace in figure.data[:3]}
        assert line_colors <= okabe_ito
        assert len(line_colors) == 3
        assert figure.data[3].marker.color not in line_colors  # le barre sono neutre


def test_daily_load_sums_activities_of_the_same_day() -> None:
    entries = [entry(FIRST_DAY, 10.0), entry(FIRST_DAY, 5.5)]

    assert daily_load_of(entries, [FIRST_DAY, FIRST_DAY.replace(day=3)]) == [15.5, 0.0]


def test_activity_names_are_escaped_so_they_are_text_not_html() -> None:
    pmc, _ = small_history()
    hostile = [entry(FIRST_DAY, 10.0, '<script>alert(1)</script>&"x".fit')]

    figure = build_pmc_figure(pmc, hostile, LABELS, tokens_for(Theme.LIGHT))

    hover = figure.data[3].customdata[0]
    assert "<script" not in hover
    assert "&lt;script&gt;" in hover
    assert "&amp;" in hover


def test_render_script_passes_data_as_a_single_json_argument() -> None:
    pmc, _ = small_history()
    hostile = [entry(FIRST_DAY, 10.0, "');alert(1);//.fit")]
    figure = build_pmc_figure(pmc, hostile, LABELS, tokens_for(Theme.LIGHT))

    script = render_script(figure, LABELS)

    prefix, suffix = "ponosRender(", ");"
    assert script.startswith(prefix)
    assert script.endswith(suffix)
    payload = json.loads(script[len(prefix) : -len(suffix)])
    assert set(payload) == {"figure", "labels", "locale", "filename"}
    assert payload["figure"]["data"][0]["name"] == "Fitness (CTL)"
    assert payload["labels"] == {"png": "Save as PNG", "svg": "Save as SVG"}
    assert payload["locale"]["name"] == PLOTLY_LOCALE_NAME
    assert script.isascii()


def test_modebar_titles_travel_as_the_plotly_language_dictionary() -> None:
    pmc, entries = small_history()
    figure = build_pmc_figure(pmc, entries, LABELS, tokens_for(Theme.LIGHT))

    payload = _payload(render_script(figure, LABELS))

    assert payload["locale"]["dictionary"] == {key: f"{key} (test)" for key in PLOTLY_MODEBAR_KEYS}
    assert "Zoom in" in payload["locale"]["dictionary"]


def test_modebar_labels_must_cover_exactly_the_plotly_titles() -> None:
    incomplete = {key: key for key in PLOTLY_MODEBAR_KEYS[1:]}

    with pytest.raises(ValueError, match="modebar"):
        replace(LABELS, modebar=incomplete)
    with pytest.raises(ValueError, match="modebar"):
        replace(LABELS, modebar={**LABELS.modebar, "Unknown": "x"})


def _payload(script: str) -> dict[str, Any]:
    prefix, suffix = "ponosRender(", ");"
    loaded: dict[str, Any] = json.loads(script[len(prefix) : -len(suffix)])
    return loaded


@pytest.mark.parametrize("theme", list(Theme))
def test_series_colors_have_at_least_3_to_1_contrast_on_the_theme_background(theme: Theme) -> None:
    tokens = tokens_for(theme)

    for color in (tokens.ctl, tokens.atl, tokens.tsb, tokens.daily_load):
        assert _contrast(color, tokens.background) >= 3.0


def _luminance(color: str) -> float:
    channels = [int(color[i : i + 2], 16) / 255 for i in (1, 3, 5)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _contrast(a: str, b: str) -> float:
    high, low = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)
