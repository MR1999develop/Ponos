# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Parts of the map view that need no web engine: styles, legend, list model, elevation figure."""

from __future__ import annotations

from datetime import date

import numpy as np
import pytest
from pytestqt.qtbot import QtBot

from ponos_coach.activities.domain import ActivityIdentity, Sport
from ponos_coach.load.activity_profile import NO_ZONE, ActivityProfileData, ZoneAbsence
from ponos_coach.maps.ports import TileProblem
from ponos_coach.ui import map_texts
from ponos_coach.ui.chart_texts import chart_chrome
from ponos_coach.ui.elevation_figure import ElevationLabels, build_elevation_figure
from ponos_coach.ui.map_legend import LegendBar, LegendEntry, dash_pattern
from ponos_coach.ui.map_style import (
    SPORT_ORDER,
    page_colors,
    page_sports,
    sport_color,
    sport_look,
)
from ponos_coach.ui.tokens import Theme, tokens_for
from tests.builders import fit_identity

IDENTITY: ActivityIdentity = fit_identity(1)


# --- Looks: colour and dash, never colour alone ---------------------------------------------


@pytest.mark.parametrize("theme", list(Theme))
def test_every_sport_has_its_own_colour_slot_and_its_own_dash_in_both_themes(theme: Theme) -> None:
    tokens = tokens_for(theme)

    looks = [(sport_color(s, tokens), sport_look(s).dash) for s in SPORT_ORDER]

    assert len({color for color, _ in looks}) == len(SPORT_ORDER)  # distinct colours
    assert len({dash for _, dash in looks}) == len(SPORT_ORDER)  # distinct line patterns
    assert sport_look(Sport.RUNNING).dash == ""  # the commonest sport is a plain line


def test_the_page_receives_a_look_for_every_sport_in_the_order_of_the_indexes() -> None:
    tokens = tokens_for(Theme.LIGHT)

    sports = page_sports(tokens)

    assert len(sports) == len(SPORT_ORDER)
    assert sports[0] == {"color": sport_color(SPORT_ORDER[0], tokens), "dash": ""}
    assert set(page_colors(tokens)) == {"background", "text", "panel"}


@pytest.mark.parametrize("theme", list(Theme))
def test_the_map_backdrop_is_a_neutral_grey_with_no_tint(theme: Theme) -> None:
    value = tokens_for(theme).map_background.lstrip("#")
    red, green, blue = (int(value[i : i + 2], 16) for i in (0, 2, 4))

    assert max(red, green, blue) - min(red, green, blue) <= 8  # grey, not a colour


# --- Legend ---------------------------------------------------------------------------------


def test_dash_text_becomes_the_on_off_lengths_of_a_pen() -> None:
    assert dash_pattern("") == []
    assert dash_pattern("6 6") == [2.0, 2.0]
    assert dash_pattern("10 5") == [10 / 3, 5 / 3]
    assert len(dash_pattern("12 4 2")) % 2 == 0  # Qt wants pairs


def test_the_legend_shows_a_name_per_entry_and_hides_when_empty(qtbot: QtBot) -> None:
    bar = LegendBar("Legend: sports")
    qtbot.addWidget(bar)
    assert bar.isHidden()

    bar.set_entries(
        [LegendEntry("Running", "#0072B2"), LegendEntry("Trail running", "#CC79A7", "10 5")]
    )

    assert not bar.isHidden()
    assert [e.text for e in bar.entries] == ["Running", "Trail running"]
    assert bar.accessibleName() == "Legend: sports"
    bar.set_entries([])
    assert bar.isHidden()


# --- Texts ----------------------------------------------------------------------------------


@pytest.mark.parametrize("problem", list(TileProblem))
def test_every_tile_problem_has_what_it_is_and_what_to_do(problem: TileProblem) -> None:
    message = map_texts.tile_notice(problem)

    assert message.title
    assert message.what
    assert message.remedy
    assert message.title.rstrip(".") in map_texts.notice_line(message)


def test_the_zone_legend_gives_the_heart_rate_range_when_the_thresholds_are_known() -> None:
    starts = (133.0, 148.0, 160.0, 170.0)

    texts = [map_texts.zone_legend_text(i, starts) for i in range(5)]

    assert texts[0].startswith("Z1") and "below 133 bpm" in texts[0]
    assert "133 to 148 bpm" in texts[1]
    assert "from 170 bpm" in texts[4]
    assert map_texts.zone_legend_text(2, None).startswith("Z3")  # without thresholds: the name only


def test_the_attribution_credits_the_openstreetmap_contributors() -> None:
    assert map_texts.ATTRIBUTION == "© OpenStreetMap contributors"


# --- Elevation profile ----------------------------------------------------------------------


def profile(*, altitude: bool = True, zones: bool = True) -> ActivityProfileData:
    count = 8
    return ActivityProfileData(
        identity=IDENTITY,
        sport=Sport.RUNNING,
        local_day=date(2026, 5, 3),
        segment=np.array([0, 0, 0, 0, 1, 1, 1, 1], dtype=np.intp),
        distance_m=np.arange(count, dtype=np.float64) * 500.0,
        lat_deg=np.linspace(-30.0, -29.99, count),
        lon_deg=np.full(count, -20.0),
        altitude_m=np.array([10.0, 14, 20, 30, 25, 22, 40, 41]) if altitude else None,
        zone=np.array([0, 1, 1, 2, 2, NO_ZONE, 3, 4], dtype=np.int8) if zones else None,
        zone_absence=None if zones else ZoneAbsence.NO_HEART_RATE,
        threshold_bpm=170 if zones else None,
        zone_starts_bpm=(133.0, 148.0, 160.0, 170.0) if zones else None,
    )


def labels() -> ElevationLabels:
    return ElevationLabels(
        "Distance (km)",
        "Altitude (m)",
        "Altitude",
        ("Z1", "Z2", "Z3", "Z4", "Z5"),
        "No usable heart rate",
        chart_chrome(),
    )


def test_the_profile_is_altitude_over_distance_with_named_axes_and_units() -> None:
    figure = build_elevation_figure(profile(), labels(), tokens_for(Theme.LIGHT), by_zone=False)

    assert len(figure.data) == 1  # one line, no zone traces
    assert list(figure.data[0].x) == [0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5]  # kilometres
    assert figure.layout.xaxis.title.text == "Distance (km)"
    assert figure.layout.yaxis.title.text == "Altitude (m)"
    assert figure.layout.xaxis.rangemode == "tozero"
    assert not figure.layout.showlegend


def test_by_zone_the_profile_adds_one_named_trace_per_zone_present_and_a_dashed_grey_one() -> None:
    tokens = tokens_for(Theme.LIGHT)

    figure = build_elevation_figure(profile(), labels(), tokens, by_zone=True)

    names = [trace.name for trace in figure.data]
    assert names[0] == "Altitude"
    assert {"Z1", "Z2", "Z3", "Z4", "No usable heart rate"} <= set(names)
    assert figure.layout.showlegend  # the legend names every zone: colour is never alone
    grey = next(t for t in figure.data if t.name == "No usable heart rate")
    assert grey.line.dash == "dash"
    assert grey.line.color == tokens.no_zone
    assert figure.data[0].hoverinfo != "skip"  # only the whole line answers hover
    assert all(t.hoverinfo == "skip" for t in figure.data[1:])


def test_a_zone_trace_skips_the_gaps_between_segments() -> None:
    figure = build_elevation_figure(profile(), labels(), tokens_for(Theme.LIGHT), by_zone=True)

    z3 = next(t for t in figure.data if t.name == "Z3")  # the stretch from point 4 to point 5
    values = np.array(z3.y, dtype=float)
    assert not np.isnan(values[[4, 5]]).any()
    assert np.isnan(values[[0, 1, 2, 3, 6, 7]]).all()  # point 3 to 4 is a gap between segments


def test_without_zones_by_zone_still_gives_the_plain_profile() -> None:
    figure = build_elevation_figure(
        profile(zones=False), labels(), tokens_for(Theme.DARK), by_zone=True
    )

    assert len(figure.data) == 1


def test_a_profile_without_altitude_has_no_figure() -> None:
    with pytest.raises(ValueError, match="altitude"):
        build_elevation_figure(
            profile(altitude=False), labels(), tokens_for(Theme.LIGHT), by_zone=False
        )
