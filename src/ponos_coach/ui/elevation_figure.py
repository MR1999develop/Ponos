# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Plotly figure of the elevation profile: altitude over distance, optionally coloured by zone.

Pure function from the profile columns, already translated texts and tokens to `go.Figure`. The
first trace is the whole altitude line and the only one that answers hover: the page reports its
point index, which is the index of the profile columns, so a point of the chart and a point of the
map are the same number. The zone traces are drawn over it (thicker), each named in the legend with
its heart-rate range; the stretches without a usable heart rate are grey and dashed. Colour is
never the only sign: the legend names every zone.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import plotly.graph_objects as go
from numpy.typing import NDArray

from ponos_coach.load.activity_profile import NO_ZONE, ActivityProfileData
from ponos_coach.ui.chart_payload import LEGEND_Y, ChartChrome, axis_style, base_layout
from ponos_coach.ui.tokens import ThemeTokens

EXPORT_FILE_STEM = "ponos-coach-elevation"
_METERS_PER_KM = 1000.0
_BASE_WIDTH = 1.5
_ZONE_WIDTH = 4.0


@dataclass(frozen=True, slots=True)
class ElevationLabels:
    """Texts of the profile, already translated by the view.

    `zone_names` are the five legend entries (Z1 ... Z5, with their ranges); `no_zone` names the
    grey stretches.
    """

    x_axis: str
    y_axis: str
    altitude: str
    zone_names: tuple[str, str, str, str, str]
    no_zone: str
    chrome: ChartChrome


def _stretches(
    zone: NDArray[np.int8], altitude: NDArray[np.float64], segment: NDArray[np.intp], wanted: int
) -> NDArray[np.float64]:
    """Altitude at the points of the stretches in `wanted` zone, NaN elsewhere.

    The stretch that starts at point i has the zone of point i and ends at point i+1 (if they
    are in the same continuous segment), so both ends are kept.
    """
    out = np.full(altitude.shape, np.nan)
    starts = np.flatnonzero((zone[:-1] == wanted) & (segment[:-1] == segment[1:]))
    out[starts] = altitude[starts]
    out[starts + 1] = altitude[starts + 1]
    return out


def _zone_traces(
    profile: ActivityProfileData,
    labels: ElevationLabels,
    tokens: ThemeTokens,
    distance_km: NDArray[np.float64],
    altitude: NDArray[np.float64],
) -> list[go.Scatter]:
    if profile.zone is None or altitude.size < 2:
        return []
    entries: Sequence[tuple[int, str, str, str | None]] = [
        *((z, labels.zone_names[z], tokens.zones[z], None) for z in range(len(labels.zone_names))),
        (NO_ZONE, labels.no_zone, tokens.no_zone, "dash"),
    ]
    traces: list[go.Scatter] = []
    for zone, name, color, dash in entries:
        y = _stretches(profile.zone, altitude, profile.segment, zone)
        if np.isnan(y).all():
            continue
        traces.append(
            go.Scatter(
                x=distance_km,
                y=y,
                mode="lines",
                name=name,
                line={"color": color, "width": _ZONE_WIDTH, "dash": dash or "solid"},
                connectgaps=False,
                hoverinfo="skip",
            )
        )
    return traces


def build_elevation_figure(
    profile: ActivityProfileData, labels: ElevationLabels, tokens: ThemeTokens, *, by_zone: bool
) -> go.Figure:
    """Altitude over distance (km) of the profile; `by_zone` adds the zone traces if there are any.

    Raises:
        ValueError: the profile has no altitude (the caller shows the reason instead).
    """
    if profile.altitude_m is None:
        raise ValueError("the profile has no altitude")
    distance_km = profile.distance_m / _METERS_PER_KM
    altitude = profile.altitude_m
    zoned = by_zone and profile.zone is not None
    base_color = tokens.text_muted if zoned else tokens.categories[0]
    figure = go.Figure(
        go.Scatter(
            x=distance_km,
            y=altitude,
            mode="lines",
            name=labels.altitude,
            line={"color": base_color, "width": _BASE_WIDTH},
            connectgaps=False,
            hovertemplate="%{y:.0f} m<extra></extra>",
        )
    )
    if zoned:
        figure.add_traces(_zone_traces(profile, labels, tokens, distance_km, altitude))
    figure.update_xaxes(axis_style(tokens, labels.x_axis), rangemode="tozero")
    figure.update_yaxes(axis_style(tokens, labels.y_axis))
    figure.update_layout(
        **base_layout(tokens),
        hovermode="x",
        showlegend=zoned,
        legend={"orientation": "h", "y": LEGEND_Y, "x": 0},
        margin={"l": 64, "r": 16, "t": 48, "b": 48},
    )
    return figure
