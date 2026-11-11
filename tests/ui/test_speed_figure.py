# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""The speed or pace figure of an activity: units, inverted pace axis, holes, themes."""

from __future__ import annotations

import base64
import json

import numpy as np
import plotly.graph_objects as go
import pytest

from ponos_coach.activities.domain import SpeedSeries, Sport
from ponos_coach.ui.activity_detail import PACE_SPORTS
from ponos_coach.ui.speed_figure import (
    SpeedLabels,
    build_speed_figure,
    pace_s_per_km,
    prepare_speed_chart,
    uses_pace,
)
from ponos_coach.ui.tokens import Theme, tokens_for
from tests.ui.helpers import CHROME

LABELS = SpeedLabels("Time", "Speed", "Pace", CHROME)
SECONDS = np.arange(0.0, 601.0, 10.0)


def series_at(speed_m_s: float, *, hole_at: int | None = None) -> SpeedSeries:
    speed = np.full(SECONDS.size, speed_m_s)
    if hole_at is not None:
        speed[hole_at] = np.nan
    return SpeedSeries(SECONDS, speed)


def figure_of(sport: Sport, series: SpeedSeries, theme: Theme = Theme.LIGHT) -> go.Figure:
    return build_speed_figure(series, sport, LABELS, tokens_for(theme))


@pytest.mark.parametrize("sport", sorted(PACE_SPORTS, key=str))
def test_the_sports_measured_by_pace_get_a_pace_in_min_per_km(sport: Sport) -> None:
    figure = figure_of(sport, series_at(1000.0 / 300.0))  # 5:00 per km

    assert uses_pace(sport)
    assert figure.layout.yaxis.title.text == "Pace (min/km)"
    assert figure.layout.xaxis.title.text == "Time (min)"
    np.testing.assert_allclose(figure.data[0].y, 300.0)  # seconds per km
    np.testing.assert_allclose(figure.data[0].x, SECONDS / 60)


def test_the_pace_axis_is_upside_down_so_faster_is_higher_and_ticks_are_written_m_ss() -> None:
    low = 1000.0 / 360.0
    high = 1000.0 / 300.0
    speed = np.linspace(low, high, SECONDS.size)  # from 6:00 to 5:00 per km
    figure = figure_of(Sport.RUNNING, SpeedSeries(SECONDS, speed))

    start, end = figure.layout.yaxis.range
    assert start > end  # the slowest pace at the bottom, the fastest at the top
    assert start >= 360.0
    assert end <= 300.0
    assert all(isinstance(text, str) and ":" in text for text in figure.layout.yaxis.ticktext)
    assert "5:00" in figure.layout.yaxis.ticktext


def test_a_cycling_activity_gets_a_speed_in_km_per_h_from_zero() -> None:
    figure = figure_of(Sport.CYCLING, series_at(10.0))  # 36 km/h

    assert not uses_pace(Sport.CYCLING)
    assert figure.layout.yaxis.title.text == "Speed (km/h)"
    np.testing.assert_allclose(figure.data[0].y, 36.0)
    assert figure.layout.yaxis.rangemode == "tozero"
    assert figure.layout.yaxis.range is None  # not reversed, not fixed


@pytest.mark.parametrize("sport", [Sport.WALKING, Sport.OTHER, Sport.STRENGTH])
def test_the_other_sports_are_shown_as_a_speed(sport: Sport) -> None:
    assert figure_of(sport, series_at(2.0)).layout.yaxis.title.text == "Speed (km/h)"


@pytest.mark.parametrize("sport", [Sport.RUNNING, Sport.CYCLING])
def test_a_hole_stays_a_hole_a_gap_in_the_line_not_a_zero_or_a_peak(sport: Sport) -> None:
    figure = figure_of(sport, series_at(3.0, hole_at=20))

    y = np.asarray(figure.data[0].y, dtype=float)
    assert np.isnan(y[20])
    assert np.isfinite(np.delete(y, 20)).all()
    assert not figure.data[0].connectgaps
    payload = json.loads(
        prepare_speed_chart(
            series_at(3.0, hole_at=20), sport, LABELS, tokens_for(Theme.LIGHT)
        ).script[len("ponosRender(") : -2]
    )
    assert None in _decoded(payload["figure"]["data"][0]["y"])  # a null in the JSON, not NaN


def _decoded(column: object) -> list[float | None]:
    """The values of a column: a list, or Plotly's base64 typed array decoded."""
    if isinstance(column, list):
        return column
    assert isinstance(column, dict)
    raw = np.frombuffer(base64.b64decode(column["bdata"]), dtype=column["dtype"])
    return [None if np.isnan(v) else float(v) for v in raw]


def test_pace_of_a_hole_is_nan_and_never_infinite() -> None:
    pace = pace_s_per_km(np.array([2.0, np.nan, 0.0]))

    assert pace[0] == 500.0
    assert np.isnan(pace[1])
    assert np.isinf(pace[2])  # the domain never hands over a zero speed: holes are NaN


def test_the_hover_shows_the_pace_as_m_ss_and_the_time() -> None:
    figure = figure_of(Sport.RUNNING, series_at(1000.0 / 330.0))

    assert figure.data[0].text[0] == "5:30"
    assert "%{text} min/km" in figure.data[0].hovertemplate
    assert "min<br>" in figure.data[0].hovertemplate


@pytest.mark.parametrize("theme", list(Theme))
@pytest.mark.parametrize("sport", [Sport.RUNNING, Sport.CYCLING])
def test_the_colour_comes_from_the_theme_and_is_neither_red_nor_orange(
    theme: Theme, sport: Sport
) -> None:
    tokens = tokens_for(theme)
    figure = figure_of(sport, series_at(3.0), theme)

    colour = figure.data[0].line.color
    assert colour in tokens.categories
    assert colour not in (tokens.atl, tokens.error)
    red, green, blue = (int(colour[i : i + 2], 16) for i in (1, 3, 5))
    assert not (red > green and red > blue)  # not a red or an orange
    assert figure.layout.paper_bgcolor == tokens.background
    assert figure.layout.font.color == tokens.text
    assert figure.layout.yaxis.gridcolor == tokens.grid


def test_the_light_and_dark_figures_differ_only_by_tokens() -> None:
    light = figure_of(Sport.CYCLING, series_at(3.0), Theme.LIGHT)
    dark = figure_of(Sport.CYCLING, series_at(3.0), Theme.DARK)

    assert light.layout.paper_bgcolor != dark.layout.paper_bgcolor
    np.testing.assert_array_equal(light.data[0].y, dark.data[0].y)


def test_the_plot_area_lines_up_with_the_hr_chart() -> None:
    figure = figure_of(Sport.RUNNING, series_at(3.0))

    assert (figure.layout.margin.l, figure.layout.margin.r) == (64, 16)  # as the HR figure


@pytest.mark.parametrize(
    ("sport", "fixed_y", "stem"),
    [
        (Sport.RUNNING, True, "ponos-coach-pace"),
        (Sport.CYCLING, False, "ponos-coach-speed"),
    ],
)
def test_the_page_gets_a_linked_time_axis_and_a_fixed_pace_axis(
    sport: Sport, fixed_y: bool, stem: str
) -> None:
    prepared = prepare_speed_chart(series_at(3.0), sport, LABELS, tokens_for(Theme.LIGHT))

    payload = json.loads(prepared.script[len("ponosRender(") : -2])
    assert payload["linkedX"] is True
    assert payload["zoom"] is True
    assert payload.get("fixedY", False) is fixed_y
    assert payload["linkedHover"] is True
    assert payload["filename"] == stem
    assert payload["labels"] == {"png": "Save as PNG", "svg": "Save as SVG"}
    assert prepared.background == tokens_for(Theme.LIGHT).background


def test_the_pace_axis_shows_the_whole_curve_even_a_rare_slow_point_with_a_margin() -> None:
    # The user's request of 2026-11-11: the whole curve always visible, a slight margin around it.
    speed = np.full(SECONDS.size, 1000.0 / 300.0)  # 5:00 per km
    speed[10] = 1000.0 / 720.0  # one point at 12:00 per km (a stop at a crossing)
    speed[20] = 1000.0 / 240.0  # one at 4:00 per km
    figure = figure_of(Sport.RUNNING, SpeedSeries(SECONDS, speed))

    slowest, fastest = figure.layout.yaxis.range
    span = 720.0 - 240.0
    assert 0 < slowest - 720.0 <= span * 0.1
    assert 0 < 240.0 - fastest <= span * 0.1
    assert all(fastest <= tick <= slowest for tick in figure.layout.yaxis.tickvals)


def test_a_pace_curve_without_any_finite_value_gets_no_fixed_axis() -> None:
    figure = figure_of(Sport.RUNNING, SpeedSeries(SECONDS, np.zeros(SECONDS.size)))  # standing

    assert figure.layout.yaxis.range is None
    assert not figure.layout.yaxis.tickvals
