# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""T-UI-04 (pure part): the HR figure of an activity with the zone bands."""

from __future__ import annotations

import json

import numpy as np
import plotly.graph_objects as go
import pytest
from hypothesis import given
from hypothesis import strategies as st

from ponos_coach.activities.domain import HeartRateSeries
from ponos_coach.load.activity_detail import HeartRateTrace
from ponos_coach.load.domain import HeartRateLimits, zone_starts_bpm
from ponos_coach.ui.hr_figure import (
    MAX_TRACE_POINTS,
    HeartRateLabels,
    build_heart_rate_figure,
    prepare_heart_rate_chart,
    thinned_indices,
    zone_bands,
)
from ponos_coach.ui.tokens import Theme, tokens_for
from tests.ui.helpers import CHROME

LABELS = HeartRateLabels("Time", "Heart rate", "Heart rate", "Samples outside your limits", CHROME)
LIMITS = HeartRateLimits(50, 190)
THRESHOLD = 171
STARTS = zone_starts_bpm(THRESHOLD)


def trace_of(bpm: list[float], *, limits: HeartRateLimits = LIMITS) -> HeartRateTrace:
    seconds = np.arange(len(bpm), dtype=np.float64) * 10.0
    series = HeartRateSeries(seconds, np.asarray(bpm, dtype=np.float64))
    return HeartRateTrace.of(series, limits)


def figure_of(trace: HeartRateTrace, theme: Theme = Theme.LIGHT) -> go.Figure:
    return build_heart_rate_figure(trace, LIMITS, STARTS, LABELS, tokens_for(theme))


def test_the_curve_is_in_minutes_and_bpm() -> None:
    trace = trace_of([120.0, 130.0, 140.0, 150.0, 160.0, 165.0])

    figure = figure_of(trace)

    curve = figure.data[0]
    assert curve.name == "Heart rate"
    assert curve.mode == "lines"
    np.testing.assert_allclose(curve.x, trace.seconds / 60)
    np.testing.assert_allclose(curve.y, trace.bpm)
    assert figure.layout.xaxis.title.text == "Time (min)"
    assert figure.layout.yaxis.title.text == "Heart rate (bpm)"
    assert len(figure.data) == 1  # no discards: no second series


def test_discarded_samples_are_marked_with_a_cross_in_a_second_series() -> None:
    trace = trace_of([120.0, 255.0, 140.0, 10.0, 160.0])

    figure = figure_of(trace)

    marks = figure.data[1]
    assert marks.name == "Samples outside your limits"
    assert marks.mode == "markers"
    assert marks.marker.symbol == "x"  # not just the colour
    assert list(marks.y) == [255.0, 10.0]
    assert marks.marker.color == tokens_for(Theme.LIGHT).error


def test_one_band_per_zone_behind_the_curve_with_the_zone_name_on_the_edge() -> None:
    trace = trace_of([120.0, 150.0, 175.0])

    figure = figure_of(trace)

    assert len(figure.layout.shapes) == 5
    assert all(shape.layer == "below" for shape in figure.layout.shapes)
    tokens = tokens_for(Theme.LIGHT)
    assert [shape.fillcolor for shape in figure.layout.shapes] == list(tokens.zones)
    assert [a.text for a in figure.layout.annotations] == [
        "Z1 <78%",
        "Z2 78\u201386%",
        "Z3 87\u201393%",
        "Z4 94\u201399%",
        "Z5 \u2265100%",
    ]


def test_bands_are_contiguous_and_use_the_zone_boundaries_of_the_threshold() -> None:
    trace = trace_of([120.0, 175.0])

    shapes = figure_of(trace).layout.shapes

    edges = [(s.y0, s.y1) for s in shapes]
    assert [low for low, _ in edges][1:] == pytest.approx(list(STARTS))
    assert [high for _, high in edges][:-1] == pytest.approx(list(STARTS))
    assert edges[0][0] == pytest.approx(LIMITS.min_bpm - 5)
    assert edges[-1][1] == pytest.approx(LIMITS.max_bpm + 5)


def test_the_axis_covers_the_limits_and_the_discarded_samples() -> None:
    trace = trace_of([120.0, 255.0, 140.0])

    low, high = figure_of(trace).layout.yaxis.range

    assert low <= LIMITS.min_bpm
    assert high >= 255.0


def test_zones_outside_the_axis_range_have_no_band() -> None:
    bands = zone_bands((144.0, 153.0, 162.0, 171.0), low=100.0, high=150.0)

    assert [zone for zone, _, _ in bands] == [0, 1]
    assert bands[-1][2] == 150.0  # the band is clipped at the axis edge


def test_a_long_curve_is_thinned_but_every_discarded_sample_is_kept() -> None:
    count = 20_000
    bpm = np.full(count, 140.0)
    bpm[[7, 9_999, 19_999]] = 255.0  # three discards
    series = HeartRateSeries(np.arange(count, dtype=np.float64), bpm)
    trace = HeartRateTrace.of(series, LIMITS)

    figure = figure_of(trace)

    assert len(figure.data[0].x) <= MAX_TRACE_POINTS + 3
    assert len(figure.data[0].x) >= MAX_TRACE_POINTS // 4
    assert list(figure.data[1].y) == [255.0, 255.0, 255.0]


def test_thinning_a_short_series_changes_nothing_and_caps_the_flags() -> None:
    keep = np.zeros(10, dtype=np.bool_)
    assert list(thinned_indices(np.arange(10.0), keep)) == list(range(10))
    limit = np.zeros(MAX_TRACE_POINTS)
    assert list(thinned_indices(limit, np.zeros(limit.size, dtype=np.bool_))) == list(
        range(MAX_TRACE_POINTS)
    )

    count = MAX_TRACE_POINTS * 4
    everything = np.ones(count, dtype=np.bool_)
    chosen = thinned_indices(np.zeros(count), everything)
    assert len(chosen) <= 3 * MAX_TRACE_POINTS  # even with all discards the cap holds
    assert list(chosen) == sorted(set(chosen))


def test_thinning_keeps_the_borders_the_minimum_and_the_maximum() -> None:
    count = 50_001  # not a multiple of the stretches
    seconds = np.arange(count, dtype=np.float64)
    bpm = 140.0 + 20.0 * np.sin(seconds / 700.0) + (seconds % 7)
    bpm[12_345] = 199.0  # a lone peak
    bpm[40_001] = 61.0  # a lone valley
    keep = np.zeros(count, dtype=np.bool_)

    chosen = thinned_indices(bpm, keep)

    assert len(chosen) <= MAX_TRACE_POINTS + 2
    assert chosen[0] == 0
    assert chosen[-1] == count - 1
    assert bpm[chosen].max() == bpm.max() == 199.0
    assert bpm[chosen].min() == bpm.min() == 61.0
    assert list(chosen) == sorted(set(chosen))


@given(
    values=st.lists(st.floats(30, 230), min_size=1, max_size=9_000),
)
def test_thinning_never_loses_an_extreme_and_stays_sorted(values: list[float]) -> None:
    bpm = np.array(values)

    chosen = thinned_indices(bpm, np.zeros(bpm.size, dtype=np.bool_))

    assert list(chosen) == sorted(set(chosen))
    assert chosen[0] == 0
    assert chosen[-1] == bpm.size - 1
    assert bpm[chosen].max() == bpm.max()
    assert bpm[chosen].min() == bpm.min()


def test_the_dark_theme_changes_surface_and_band_opacity_not_the_data() -> None:
    trace = trace_of([120.0, 150.0, 175.0])

    light = figure_of(trace, Theme.LIGHT)
    dark = figure_of(trace, Theme.DARK)

    assert light.layout.paper_bgcolor == "#FFFFFF"
    assert dark.layout.paper_bgcolor == "#1E1F22"
    assert [s.opacity for s in light.layout.shapes] != [s.opacity for s in dark.layout.shapes]
    np.testing.assert_allclose(light.data[0].y, dark.data[0].y)


def test_the_prepared_chart_is_one_ascii_json_argument_with_its_file_name() -> None:
    trace = trace_of([120.0, 150.0, 175.0])

    prepared = prepare_heart_rate_chart(trace, LIMITS, STARTS, LABELS, tokens_for(Theme.LIGHT))

    prefix, suffix = "ponosRender(", ");"
    assert prepared.script.isascii()
    payload = json.loads(prepared.script[len(prefix) : -len(suffix)])
    assert payload["filename"] == "ponos-coach-heart-rate"
    assert payload["figure"]["data"][0]["name"] == "Heart rate"


def test_the_wheel_zooms_and_a_left_drag_pans_the_time_axis_only() -> None:
    prepared = prepare_heart_rate_chart(
        trace_of([120.0, 140.0, 160.0]), LIMITS, STARTS, LABELS, tokens_for(Theme.LIGHT)
    )

    payload = json.loads(prepared.script[len("ponosRender(") : -2])
    assert payload["zoom"] is True  # the page turns on the wheel zoom
    assert "linkedX" not in payload  # minutes of one session: nothing to keep in step
    assert prepared.figure.layout.dragmode == "pan"
    assert prepared.figure.layout.yaxis.fixedrange is True  # bpm stay as drawn
