# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Phase 9 acceptance campaign, the y axis of the charts: the whole curve with a slight margin
above and below (6% of the span), the pace axis from the fastest to the slowest point, the
elevation profile with its own range. Hostile series: flat, one finite point among holes, never
moving, extreme paces, altitude with gaps or none at all.

The figures are pure functions; the oracle is arithmetic written here.
"""

from __future__ import annotations

import math

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from ponos_coach.activities.domain import SpeedSeries, Sport
from ponos_coach.load.activity_profile import ActivityProfileData, ZoneAbsence
from ponos_coach.ui.chart_payload import Y_MARGIN_FRACTION, padded_range
from ponos_coach.ui.elevation_figure import ElevationLabels, build_elevation_figure
from ponos_coach.ui.speed_figure import SpeedLabels, build_speed_figure
from ponos_coach.ui.tokens import Theme, tokens_for
from tests.builders import fit_identity
from tests.collaudo.fase5_support import TODAY
from tests.ui.helpers import CHROME

LABELS = SpeedLabels("Time", "Speed", "Pace", CHROME)
TOKENS = tokens_for(Theme.LIGHT)
MARGIN = 0.06


def test_the_margin_is_one_value_of_six_percent() -> None:
    assert Y_MARGIN_FRACTION == MARGIN


@pytest.mark.collaudo
@settings(max_examples=300, deadline=None)
@given(st.floats(-1e5, 1e5), st.floats(0, 1e5))
def test_t_ui_11_the_padded_range_holds_every_value_with_six_percent_of_the_span_on_each_side(
    low: float, span: float
) -> None:
    high = low + span
    span = high - low  # what the float sum really gave (a tiny span can vanish)

    first, last = padded_range(low, high)

    assert first < low and last > high  # always a margin, even for a flat series
    wanted = span if span > 0 else (abs(high) or 1.0)
    assert low - first == pytest.approx(wanted * MARGIN, rel=1e-6, abs=1e-9)
    assert last - high == pytest.approx(wanted * MARGIN, rel=1e-6, abs=1e-9)


def speed_series(values: list[float]) -> SpeedSeries:
    return SpeedSeries(np.arange(len(values), dtype=float) * 10.0, np.array(values))


@pytest.mark.parametrize(
    "values",
    [
        [3.0] * 30,  # flat
        [math.nan, 2.5, math.nan, math.nan],  # one finite point among holes
        [0.5, 12.0, 0.5, 12.0],  # the slowest moving pace and a sprint
        [3.3, math.nan, 2.0, 4.0, math.nan, 3.0],
    ],
    ids=["flat", "one-point", "extremes", "holes"],
)
def test_t_ui_11_the_pace_axis_shows_the_whole_curve_from_the_fastest_to_the_slowest_with_margin(
    values: list[float],
) -> None:
    series = speed_series(values)
    finite = [1000.0 / v for v in values if math.isfinite(v)]

    figure = build_speed_figure(series, Sport.RUNNING, LABELS, TOKENS)

    slowest, fastest = figure.layout.yaxis.range  # upside down: the slowest at the bottom
    assert slowest > max(finite) and fastest < min(finite)
    assert fastest >= 0.0
    span = max(finite) - min(finite)
    if span > 0:
        assert slowest - max(finite) == pytest.approx(span * MARGIN, rel=1e-6)
        assert min(finite) - fastest == pytest.approx(span * MARGIN, rel=1e-6) or fastest == 0.0
    ticks = list(figure.layout.yaxis.tickvals)
    assert ticks == sorted(ticks) and len(ticks) <= 7
    assert all(fastest <= t <= slowest for t in ticks)
    assert len(figure.layout.yaxis.ticktext) == len(ticks)


@pytest.mark.collaudo
@settings(max_examples=150, deadline=None)
@given(
    st.lists(st.one_of(st.just(math.nan), st.floats(0.5, 15.0)), min_size=2, max_size=60).filter(
        lambda v: any(math.isfinite(x) for x in v)
    )
)
def test_t_ui_11_the_pace_range_always_holds_the_curve_and_the_ticks_for_any_series(
    values: list[float],
) -> None:
    figure = build_speed_figure(speed_series(values), Sport.TRAIL, LABELS, TOKENS)

    slowest, fastest = figure.layout.yaxis.range
    paces = [1000.0 / v for v in values if math.isfinite(v)]
    assert fastest <= min(paces) and slowest >= max(paces) and fastest >= 0
    assert all(fastest <= t <= slowest for t in figure.layout.yaxis.tickvals)


def test_a_speed_chart_for_a_bike_ride_starts_from_zero_and_keeps_holes_as_breaks() -> None:
    values = [8.0, 9.0, math.nan, 7.0]

    figure = build_speed_figure(speed_series(values), Sport.CYCLING, LABELS, TOKENS)

    y = list(figure.data[0].y)
    assert [round(v, 6) for v in y[:2]] == [28.8, 32.4] and math.isnan(y[2])
    assert figure.layout.yaxis.rangemode == "tozero"


# --- The elevation profile ---------------------------------------------------------------------

ELEVATION = ElevationLabels(
    "Distance", "Altitude", "Altitude", ("Z1", "Z2", "Z3", "Z4", "Z5"), "No zone", CHROME
)


def profile_with(
    altitude: np.ndarray | None, zones: np.ndarray | None = None
) -> ActivityProfileData:
    count = 6
    return ActivityProfileData(
        identity=fit_identity(3),
        sport=Sport.RUNNING,
        local_day=TODAY,
        seconds=np.arange(count, dtype=float),
        segment=np.zeros(count, dtype=np.intp),
        distance_m=np.arange(count, dtype=float) * 100.0,
        lat_deg=np.zeros(count),
        lon_deg=np.zeros(count),
        altitude_m=altitude,
        zone=zones,
        zone_absence=None if zones is not None else ZoneAbsence.NO_HEART_RATE,
        threshold_bpm=None if zones is None else 170,
        zone_starts_bpm=None if zones is None else (120.0, 145.0, 160.0, 175.0),
    )


@pytest.mark.parametrize(
    "altitude",
    [
        np.array([100.0, 130.0, 90.0, 140.0, 120.0, 100.0]),
        np.full(6, 250.0),  # flat
        np.array([-12.0, -10.0, -30.0, -5.0, -8.0, -12.0]),  # below the sea level
        np.array([100.0, np.nan, np.nan, 130.0, np.nan, 90.0]),  # gaps
    ],
    ids=["hilly", "flat", "below-sea", "gaps"],
)
def test_t_ui_11_the_elevation_profile_range_holds_the_whole_profile_with_the_margin(
    altitude: np.ndarray,
) -> None:
    figure = build_elevation_figure(profile_with(altitude), ELEVATION, TOKENS, by_zone=False)

    first, last = figure.layout.yaxis.range
    finite = altitude[np.isfinite(altitude)]
    assert first < finite.min() and last > finite.max()
    span = finite.max() - finite.min() or abs(finite.max()) or 1.0
    assert last - finite.max() == pytest.approx(span * MARGIN, rel=1e-6)


def test_the_elevation_profile_without_altitude_is_refused_with_a_value_error_not_drawn() -> None:
    with pytest.raises(ValueError, match="no altitude"):
        build_elevation_figure(profile_with(None), ELEVATION, TOKENS, by_zone=True)


def test_the_elevation_profile_with_an_altitude_of_only_gaps_has_no_fixed_range() -> None:
    figure = build_elevation_figure(
        profile_with(np.full(6, np.nan)), ELEVATION, TOKENS, by_zone=False
    )

    assert figure.layout.yaxis.range is None


def test_the_zone_of_every_stretch_is_named_in_the_legend_and_colour_is_never_the_only_sign() -> (
    None
):
    zones = np.array([0, 0, 2, 4, -1, 1], dtype=np.int8)
    altitude = np.array([100.0, 110.0, 120.0, 115.0, 105.0, 100.0])

    figure = build_elevation_figure(profile_with(altitude, zones), ELEVATION, TOKENS, by_zone=True)

    names = [t.name for t in figure.data]
    assert names[0] == "Altitude"
    assert {"Z1", "Z3", "Z5", "No zone"} <= set(names)
    assert figure.layout.showlegend is True
    no_zone = next(t for t in figure.data if t.name == "No zone")
    assert no_zone.line.dash == "dash"  # the stretch without a usable HR is dashed as well as grey
