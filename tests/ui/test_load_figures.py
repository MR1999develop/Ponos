# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""T-UI-04 (pure part): the weekly load and time-in-zones figures."""

from __future__ import annotations

import colorsys
import json
from datetime import date, timedelta

import numpy as np
import pytest

from ponos_coach.activities.domain import Sport
from ponos_coach.load.domain import (
    ZONE_COUNT,
    ZONE_STARTS_PERCENT,
    DayLoad,
    ZoneSeconds,
    weekly_load_by_sport,
    weekly_zone_seconds,
)
from ponos_coach.ui.load_figures import (
    CHART_WEEKS,
    SPORT_COLOR_SLOT,
    WeeklyLoadLabels,
    WeeklyZonesLabels,
    build_weekly_load_figure,
    build_weekly_zones_figure,
    prepare_weekly_load_chart,
    prepare_weekly_zones_chart,
    sport_color,
    zone_label,
)
from ponos_coach.ui.tokens import Theme, tokens_for
from tests.ui.helpers import CHROME

MONDAY = date(2025, 9, 1)  # a Monday
RED_ORANGE_END = 50.0  # degrees of hue: from 0 to here are reds, oranges and ambers
RED_START = 335.0  # from here to 360 the red
SPORT_NAMES = {sport: f"Sport {sport.value}" for sport in Sport}
LOAD_LABELS = WeeklyLoadLabels("Week", "Load", SPORT_NAMES, CHROME)
ZONES_LABELS = WeeklyZonesLabels("Week", "Time in zone", "Zone", CHROME)


def long_history(weeks: int = 40) -> list[DayLoad]:
    """A run and (in alternate weeks) one ride per week, for `weeks` weeks."""
    items: list[DayLoad] = []
    for index in range(weeks):
        monday = MONDAY + timedelta(weeks=index)
        zones = ZoneSeconds((600.0, 1200.0, 0.0, 0.0, 0.0))
        items.append(DayLoad(monday, Sport.RUNNING, 50.0 + index, zones))
        if index % 2 == 0:
            items.append(DayLoad(monday + timedelta(days=2), Sport.CYCLING, 30.0, zones))
    return items


def through_of(weeks: int = 40) -> date:
    return MONDAY + timedelta(weeks=weeks - 1, days=3)


def test_weekly_load_has_one_stacked_bar_series_per_sport_in_sport_order() -> None:
    weekly = weekly_load_by_sport(long_history(), through_of())

    figure = build_weekly_load_figure(weekly, LOAD_LABELS, tokens_for(Theme.LIGHT))

    assert [(t.name, t.type) for t in figure.data] == [
        ("Sport running", "bar"),
        ("Sport cycling", "bar"),
    ]
    assert figure.layout.barmode == "stack"


def test_weekly_load_shows_only_the_last_26_weeks() -> None:
    weekly = weekly_load_by_sport(long_history(40), through_of(40))

    figure = build_weekly_load_figure(weekly, LOAD_LABELS, tokens_for(Theme.LIGHT))

    running = figure.data[0]
    assert len(running.x) == CHART_WEEKS == 26
    assert running.x[-1] == through_of(40) - timedelta(days=3)  # the Monday of this week
    assert list(running.y) == [50.0 + i for i in range(14, 40)]


def test_weekly_load_values_are_the_weekly_sums_per_sport() -> None:
    items = [
        DayLoad(MONDAY, Sport.RUNNING, 40.0),
        DayLoad(MONDAY + timedelta(days=3), Sport.RUNNING, 25.0),
        DayLoad(MONDAY + timedelta(days=4), Sport.TRAIL, 10.0),
    ]
    weekly = weekly_load_by_sport(items, MONDAY + timedelta(days=6))

    figure = build_weekly_load_figure(weekly, LOAD_LABELS, tokens_for(Theme.LIGHT))

    by_name = {trace.name: list(trace.y) for trace in figure.data}
    assert by_name == {"Sport running": [65.0], "Sport trail": [10.0]}


def test_a_sport_without_load_in_the_window_has_no_legend_entry() -> None:
    old = [DayLoad(MONDAY, Sport.HIKING, 20.0)]
    recent = [DayLoad(MONDAY + timedelta(weeks=40 + i), Sport.RUNNING, 30.0) for i in range(3)]
    weekly = weekly_load_by_sport(old + recent, MONDAY + timedelta(weeks=42))

    figure = build_weekly_load_figure(weekly, LOAD_LABELS, tokens_for(Theme.LIGHT))

    assert [trace.name for trace in figure.data] == ["Sport running"]


def test_weekly_load_axes_have_names_and_units_and_bars_start_at_zero() -> None:
    weekly = weekly_load_by_sport(long_history(), through_of())

    layout = build_weekly_load_figure(weekly, LOAD_LABELS, tokens_for(Theme.LIGHT)).layout

    assert layout.xaxis.title.text == "Week"
    assert layout.xaxis.type == "date"
    assert layout.yaxis.title.text == "Load (hrTSS per week)"
    assert layout.yaxis.rangemode == "tozero"


def test_every_sport_has_its_own_color_slot_and_keeps_it() -> None:
    assert set(SPORT_COLOR_SLOT) == set(Sport)
    assert sorted(SPORT_COLOR_SLOT.values()) == list(range(len(Sport)))
    for theme in Theme:
        tokens = tokens_for(theme)
        colors = [sport_color(sport, tokens) for sport in Sport]
        assert len(set(colors)) == len(Sport)


def test_sport_colors_in_the_figure_come_from_the_theme_tokens() -> None:
    weekly = weekly_load_by_sport(long_history(), through_of())
    light = build_weekly_load_figure(weekly, LOAD_LABELS, tokens_for(Theme.LIGHT))
    dark = build_weekly_load_figure(weekly, LOAD_LABELS, tokens_for(Theme.DARK))

    assert light.data[0].marker.color == tokens_for(Theme.LIGHT).categories[0]
    assert dark.data[0].marker.color == tokens_for(Theme.DARK).categories[0]
    assert light.layout.paper_bgcolor == "#FFFFFF"
    assert dark.layout.paper_bgcolor == "#1E1F22"


def test_zones_figure_has_five_stacked_series_z1_to_z5_with_their_boundaries() -> None:
    weekly = weekly_zone_seconds(long_history(), through_of())

    figure = build_weekly_zones_figure(weekly, ZONES_LABELS, tokens_for(Theme.LIGHT))

    assert [t.name for t in figure.data] == [
        "Z1 <78%",
        "Z2 78\u201386%",
        "Z3 87\u201393%",
        "Z4 94\u201399%",
        "Z5 \u2265100%",
    ]
    assert figure.layout.barmode == "stack"
    assert figure.layout.legend.title.text == "Zone"


def test_zone_labels_follow_the_single_source_of_the_boundaries() -> None:
    assert ZONE_STARTS_PERCENT == (78, 87, 94, 100)
    assert zone_label(0).endswith("<78%")
    assert zone_label(ZONE_COUNT - 1).endswith("\u2265100%")
    with_boundaries = [zone_label(i) for i in range(1, ZONE_COUNT - 1)]
    for label, start, end in zip(
        with_boundaries, ZONE_STARTS_PERCENT[:-1], ZONE_STARTS_PERCENT[1:], strict=True
    ):
        assert label.endswith(f"{start}\u2013{end - 1}%")


def test_zones_are_in_hours_per_week_and_the_axes_say_so() -> None:
    weekly = weekly_zone_seconds(long_history(4), through_of(4))

    figure = build_weekly_zones_figure(weekly, ZONES_LABELS, tokens_for(Theme.LIGHT))

    # the first week has two activities (run and ride), each with 600 s in Z1 and 1200 s in Z2
    first_week_hours = [float(trace.y[0]) for trace in figure.data]
    assert first_week_hours == pytest.approx([1200 / 3600, 2400 / 3600, 0, 0, 0])
    assert figure.layout.yaxis.title.text == "Time in zone (hours per week)"
    assert figure.layout.yaxis.rangemode == "tozero"
    assert figure.layout.xaxis.title.text == "Week"


def test_zone_colors_are_the_sequential_scale_of_the_theme() -> None:
    weekly = weekly_zone_seconds(long_history(), through_of())
    for theme in Theme:
        tokens = tokens_for(theme)

        figure = build_weekly_zones_figure(weekly, ZONES_LABELS, tokens)

        assert tuple(trace.marker.color for trace in figure.data) == tokens.zones


def test_the_prepared_chart_carries_one_json_argument_and_an_ascii_script() -> None:
    weekly = weekly_zone_seconds(long_history(), through_of())
    tokens = tokens_for(Theme.DARK)

    prepared = prepare_weekly_zones_chart(weekly, ZONES_LABELS, tokens)

    prefix, suffix = "ponosRender(", ");"
    assert prepared.script.startswith(prefix)
    assert prepared.script.isascii()  # quotes and symbols pass as JSON escapes
    payload = json.loads(prepared.script[len(prefix) : -len(suffix)])
    assert payload["filename"] == "ponos-coach-zones"
    assert payload["figure"]["data"][0]["name"] == "Z1 <78%"
    assert prepared.background == tokens.background


def test_the_weekly_load_chart_exports_with_its_own_file_name() -> None:
    weekly = weekly_load_by_sport(long_history(), through_of())

    prepared = prepare_weekly_load_chart(weekly, LOAD_LABELS, tokens_for(Theme.LIGHT))

    assert "ponos-coach-weekly-load" in prepared.script
    assert len(prepared.figure.data) == 2


def test_an_empty_history_gives_an_empty_figure_not_an_error() -> None:
    weekly = weekly_load_by_sport([], date(2026, 1, 1))

    figure = build_weekly_load_figure(weekly, LOAD_LABELS, tokens_for(Theme.LIGHT))

    assert figure.data == ()


def test_no_value_is_negative() -> None:
    weekly = weekly_zone_seconds(long_history(), through_of())

    figure = build_weekly_zones_figure(weekly, ZONES_LABELS, tokens_for(Theme.LIGHT))

    assert all(np.all(np.asarray(trace.y) >= 0) for trace in figure.data)


# --- Sport series, colours, units and zone labels -----------------------------------


def every_sport_week(loads: dict[Sport, float]) -> list[DayLoad]:
    """One activity per sport in the same week, with the given load."""
    return [DayLoad(MONDAY, sport, load, ZoneSeconds.ZERO) for sport, load in loads.items()]


def traces_of(items: list[DayLoad]) -> dict[str, float]:
    weekly = weekly_load_by_sport(items, MONDAY + timedelta(days=3))
    figure = build_weekly_load_figure(weekly, LOAD_LABELS, tokens_for(Theme.LIGHT))
    return {str(trace.name): float(np.sum(trace.y)) for trace in figure.data}


def test_up_to_five_sports_each_get_their_own_series() -> None:
    loads = {
        Sport.RUNNING: 50.0,
        Sport.CYCLING: 40.0,
        Sport.STRENGTH: 30.0,
        Sport.HIKING: 20.0,
        Sport.OTHER: 10.0,
    }

    assert traces_of(every_sport_week(loads)) == {SPORT_NAMES[s]: v for s, v in loads.items()}


def test_with_more_than_five_sports_the_least_loaded_are_grouped_in_other() -> None:
    loads = {
        Sport.RUNNING: 70.0,
        Sport.TRAIL: 5.0,
        Sport.CYCLING: 60.0,
        Sport.STRENGTH: 50.0,
        Sport.HIKING: 40.0,
        Sport.WALKING: 8.0,
        Sport.OTHER: 2.0,
    }

    series = traces_of(every_sport_week(loads))

    assert len(series) == 5
    assert series == {
        SPORT_NAMES[Sport.RUNNING]: 70.0,
        SPORT_NAMES[Sport.CYCLING]: 60.0,
        SPORT_NAMES[Sport.STRENGTH]: 50.0,
        SPORT_NAMES[Sport.HIKING]: 40.0,
        SPORT_NAMES[Sport.OTHER]: 15.0,  # trail + walking + other
    }
    assert sum(series.values()) == sum(loads.values())  # total load does not change


def test_the_grouping_keeps_the_sport_order_other_last_and_the_color_of_each_sport() -> None:
    weekly = weekly_load_by_sport(
        every_sport_week({sport: float(10 + number) for number, sport in enumerate(Sport)}),
        MONDAY + timedelta(days=3),
    )
    tokens = tokens_for(Theme.DARK)

    figure = build_weekly_load_figure(weekly, LOAD_LABELS, tokens)

    names = [str(trace.name) for trace in figure.data]
    assert names[-1] == SPORT_NAMES[Sport.OTHER]
    order = [next(s for s, n in SPORT_NAMES.items() if n == name) for name in names]
    assert order == sorted(order, key=list(Sport).index)
    assert [trace.marker.color for trace in figure.data] == [sport_color(s, tokens) for s in order]


def test_a_tie_in_the_load_is_broken_by_the_order_of_the_sports() -> None:
    loads = dict.fromkeys(Sport, 10.0)

    series = traces_of(every_sport_week(loads))

    assert set(series) == {
        SPORT_NAMES[Sport.RUNNING],
        SPORT_NAMES[Sport.TRAIL],
        SPORT_NAMES[Sport.CYCLING],
        SPORT_NAMES[Sport.STRENGTH],
        SPORT_NAMES[Sport.OTHER],
    }


@pytest.mark.parametrize("theme", list(Theme))
def test_no_sport_color_is_red_or_orange(theme: Theme) -> None:
    for sport in Sport:
        red, green, blue = (
            int(sport_color(sport, tokens_for(theme))[i : i + 2], 16) for i in (1, 3, 5)
        )
        hue, saturation, _ = colorsys.rgb_to_hsv(red / 255, green / 255, blue / 255)
        degrees = hue * 360
        warm = degrees < RED_ORANGE_END or degrees > RED_START
        assert not (warm and saturation > 0.25), (sport, degrees)  # greys and blacks do not count


def test_the_units_of_the_axes_and_tooltips_come_from_the_labels() -> None:
    weekly = weekly_load_by_sport(long_history(4), through_of(4))
    load_labels = WeeklyLoadLabels("W", "L", SPORT_NAMES, CHROME, unit="hrTSS a settimana")
    zones_labels = WeeklyZonesLabels("W", "T", "Z", CHROME, unit="ore a settimana")

    load = build_weekly_load_figure(weekly, load_labels, tokens_for(Theme.LIGHT))
    zones = build_weekly_zones_figure(
        weekly_zone_seconds(long_history(4), through_of(4)), zones_labels, tokens_for(Theme.LIGHT)
    )

    assert load.layout.yaxis.title.text == "L (hrTSS a settimana)"
    assert "hrTSS a settimana" in load.data[0].hovertemplate
    assert zones.layout.yaxis.title.text == "T (ore a settimana)"


def test_zone_labels_with_non_integer_boundaries_say_less_than_the_next_boundary() -> None:
    starts = (82.5, 88.5, 94.0, 100.0)

    labels = [zone_label(index, starts) for index in range(ZONE_COUNT)]

    assert labels == [
        "Z1 <82.5%",
        "Z2 82.5\u2013<88.5%",
        "Z3 88.5\u2013<94%",
        "Z4 94\u2013<100%",
        "Z5 \u2265100%",
    ]


def test_zone_labels_with_whole_boundaries_end_one_point_before_the_next() -> None:
    assert zone_label(1, (78.0, 87.0, 94.0, 100.0)) == "Z2 78\u201386%"
    assert zone_label(1) == "Z2 78\u201386%"
