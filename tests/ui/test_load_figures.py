# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""T-UI-04 (parte pura): le figure del carico settimanale e del tempo nelle zone."""

from __future__ import annotations

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

MONDAY = date(2025, 9, 1)  # un lunedì
SPORT_NAMES = {sport: f"Sport {sport.value}" for sport in Sport}
LOAD_LABELS = WeeklyLoadLabels("Week", "Load", SPORT_NAMES, CHROME)
ZONES_LABELS = WeeklyZonesLabels("Week", "Time in zone", "Zone", CHROME)


def long_history(weeks: int = 40) -> list[DayLoad]:
    """Una corsa e (a settimane alterne) una pedalata a settimana, per `weeks` settimane."""
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
    assert running.x[-1] == through_of(40) - timedelta(days=3)  # il lunedì di questa settimana
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
    assert layout.yaxis.title.text == "Load (hrTSS/week)"
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
        "Z1 <85%",
        "Z2 85\u201389%",
        "Z3 90\u201394%",
        "Z4 95\u201399%",
        "Z5 \u2265100%",
    ]
    assert figure.layout.barmode == "stack"
    assert figure.layout.legend.title.text == "Zone"


def test_zone_labels_follow_the_single_source_of_the_boundaries() -> None:
    assert ZONE_STARTS_PERCENT == (85, 90, 95, 100)
    assert zone_label(0).endswith("<85%")
    assert zone_label(ZONE_COUNT - 1).endswith("\u2265100%")
    with_boundaries = [zone_label(i) for i in range(1, ZONE_COUNT - 1)]
    for label, start, end in zip(
        with_boundaries, ZONE_STARTS_PERCENT[:-1], ZONE_STARTS_PERCENT[1:], strict=True
    ):
        assert label.endswith(f"{start}\u2013{end - 1}%")


def test_zones_are_in_hours_per_week_and_the_axes_say_so() -> None:
    weekly = weekly_zone_seconds(long_history(4), through_of(4))

    figure = build_weekly_zones_figure(weekly, ZONES_LABELS, tokens_for(Theme.LIGHT))

    # la prima settimana ha due attività (corsa e pedalata), ciascuna con 600 s in Z1 e 1200 s in Z2
    first_week_hours = [float(trace.y[0]) for trace in figure.data]
    assert first_week_hours == pytest.approx([1200 / 3600, 2400 / 3600, 0, 0, 0])
    assert figure.layout.yaxis.title.text == "Time in zone (h/week)"
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
    assert prepared.script.isascii()  # le virgolette e i simboli passano come escape JSON
    payload = json.loads(prepared.script[len(prefix) : -len(suffix)])
    assert payload["filename"] == "ponos-coach-zones"
    assert payload["figure"]["data"][0]["name"] == "Z1 <85%"
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
