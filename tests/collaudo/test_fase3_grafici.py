# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""T-UI-04 and the contrast of T-UI-05 for the phase 3 charts, in both themes.

The figures are checked in two ways: on the structure (series, axes with units, bars from zero,
theme colours, values compared with a sum written here without the functions under test) and in the
real web engine, where they are exported to SVG and PNG and the files are checked (well-formed XML,
PNG with
CRC and dimensions, theme background colour in the pixels).
"""

from __future__ import annotations

import json
import re
import struct
import zlib
from collections import defaultdict
from collections.abc import Callable
from datetime import date, timedelta
from functools import partial
from pathlib import Path
from xml.etree import ElementTree

import numpy as np
import plotly.graph_objects as go
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from PySide6.QtGui import QImage
from PySide6.QtWebEngineCore import QWebEngineDownloadRequest
from pytestqt.qtbot import QtBot

from ponos_coach.activities.domain import HeartRateSeries, Sport
from ponos_coach.load.activity_detail import HeartRateTrace
from ponos_coach.load.domain import (
    DayLoad,
    HeartRateLimits,
    ZoneSeconds,
    compute_pmc,
    weekly_load_by_sport,
    weekly_zone_seconds,
    zone_starts_bpm,
)
from ponos_coach.ui.chart_payload import PreparedChart
from ponos_coach.ui.chart_view import ChartEngine, ChartView
from ponos_coach.ui.hr_figure import (
    HeartRateLabels,
    build_heart_rate_figure,
    prepare_heart_rate_chart,
)
from ponos_coach.ui.load_figures import (
    CHART_WEEKS,
    MAX_SPORT_SERIES,
    WeeklyLoadLabels,
    WeeklyZonesLabels,
    build_weekly_load_figure,
    build_weekly_zones_figure,
    prepare_weekly_load_chart,
    prepare_weekly_zones_chart,
    sport_color,
    zone_label,
)
from ponos_coach.ui.pmc_figure import build_pmc_figure, prepare_chart
from ponos_coach.ui.tokens import Theme, ThemeTokens, tokens_for
from tests.ui.helpers import CHROME, LABELS, small_history

WAIT_MS = 60_000
THEMES = list(Theme)
SPORT_NAMES = {sport: sport.value.capitalize() for sport in Sport}
LOAD_LABELS = WeeklyLoadLabels("Week", "Load", SPORT_NAMES, CHROME)
ZONES_LABELS = WeeklyZonesLabels("Week", "Time in zone", "Zone", CHROME)
HR_LABELS = HeartRateLabels("Time", "Heart rate", "Heart rate", "Discarded", CHROME)
LIMITS = HeartRateLimits(50, 190)
THROUGH = date(2026, 6, 28)  # a Sunday


# --- Data and builders -------------------------------------------------------------------------


def sample_day_loads(weeks: int = 40) -> list[DayLoad]:
    """Three sports over `weeks` weeks, with an empty week in the middle."""
    rng = np.random.default_rng(2026)
    items: list[DayLoad] = []
    for week in range(weeks):
        if week == weeks // 2:
            continue
        monday = THROUGH - timedelta(days=6) - timedelta(weeks=week)
        for offset, sport in ((0, Sport.RUNNING), (2, Sport.CYCLING), (4, Sport.STRENGTH)):
            zones = tuple(float(v) for v in rng.integers(0, 900, 5))
            items.append(
                DayLoad(
                    monday + timedelta(days=offset),
                    sport,
                    float(rng.integers(20, 150)),
                    ZoneSeconds(zones),  # type: ignore[arg-type]
                )
            )
    return items


def hr_trace() -> HeartRateTrace:
    """A 40-minute track with peaks and a stretch below the minimum HR (discarded samples)."""
    seconds = np.arange(0, 2400, 2.0)
    bpm = 120.0 + 60.0 * np.sin(seconds / 300.0)
    bpm[100:120] = 40.0  # below the minimum
    bpm[300:305] = 200.0  # above the maximum
    return HeartRateTrace.of(HeartRateSeries(seconds, bpm), LIMITS)


def pmc_chart(tokens: ThemeTokens) -> PreparedChart:
    pmc, entries = small_history()
    return prepare_chart(pmc, entries, LABELS, tokens)


def load_chart(tokens: ThemeTokens) -> PreparedChart:
    return prepare_weekly_load_chart(
        weekly_load_by_sport(sample_day_loads(), THROUGH), LOAD_LABELS, tokens
    )


def zones_chart(tokens: ThemeTokens) -> PreparedChart:
    return prepare_weekly_zones_chart(
        weekly_zone_seconds(sample_day_loads(), THROUGH), ZONES_LABELS, tokens
    )


def heart_rate_chart(tokens: ThemeTokens) -> PreparedChart:
    return prepare_heart_rate_chart(hr_trace(), LIMITS, zone_starts_bpm(170), HR_LABELS, tokens)


CHARTS: dict[str, Callable[[ThemeTokens], PreparedChart]] = {
    "pmc": pmc_chart,
    "load": load_chart,
    "zones": zones_chart,
    "hr": heart_rate_chart,
}
EXPORT_STEMS = {
    "pmc": "ponos-coach-pmc",
    "load": "ponos-coach-weekly-load",
    "zones": "ponos-coach-zones",
    "hr": "ponos-coach-heart-rate",
}


# --- Structure of the figures ------------------------------------------------------------------


def axes_of(figure: go.Figure) -> list[tuple[str, go.layout.XAxis | go.layout.YAxis]]:
    return [
        (name, figure.layout[name]) for name in figure.layout if re.fullmatch(r"[xy]axis\d*", name)
    ]


@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize("kind", list(CHARTS))
def test_t_ui_04_every_figure_carries_the_theme_and_serialises_to_strict_ascii_json(
    kind: str, theme: Theme
) -> None:
    tokens = tokens_for(theme)

    prepared = CHARTS[kind](tokens)

    figure = prepared.figure
    assert figure.layout.paper_bgcolor == tokens.background == prepared.background
    assert figure.layout.plot_bgcolor == tokens.background
    assert figure.layout.font.color == tokens.text
    assert prepared.script.isascii()
    match = re.fullmatch(r"ponosRender\((.*)\);", prepared.script, flags=re.DOTALL)
    assert match is not None
    payload = json.loads(match.group(1), parse_constant=_refuse_constant)
    assert payload["filename"] == EXPORT_STEMS[kind]
    assert payload["labels"] == {"png": CHROME.export_png, "svg": CHROME.export_svg}
    assert len(payload["figure"]["data"]) == len(figure.data)


def _refuse_constant(name: str) -> object:
    raise AssertionError(f"the JSON contains {name}: the page would not read it")


@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize("kind", list(CHARTS))
def test_t_ui_04_every_value_axis_names_what_it_measures_with_its_unit(
    kind: str, theme: Theme
) -> None:
    figure = CHARTS[kind](tokens_for(theme)).figure

    titled = [(name, axis.title.text) for name, axis in axes_of(figure) if axis.title.text]

    value_axes = [(n, t) for n, t in titled if n.startswith("yaxis")]
    assert value_axes, kind
    for name, title in value_axes:
        assert re.search(r"\([^()]+\)$", title), (kind, name, title)  # "Name (unit)"
    x_axes = [t for n, t in titled if n.startswith("xaxis")]
    assert x_axes, kind  # the time axis has a name
    if kind == "hr":
        assert x_axes == ["Time (min)"]


@pytest.mark.parametrize("theme", THEMES)
def test_t_ui_04_bar_charts_start_at_zero_stack_and_never_go_negative(theme: Theme) -> None:
    tokens = tokens_for(theme)
    for figure in (CHARTS["load"](tokens).figure, CHARTS["zones"](tokens).figure):
        assert figure.layout.barmode == "stack"
        assert figure.layout.yaxis.rangemode == "tozero"
        for trace in figure.data:
            assert trace.type == "bar"
            assert trace.base is None  # no offset: the height is the value
            assert (np.asarray(trace.y) >= 0).all()
    pmc = CHARTS["pmc"](tokens).figure
    (daily,) = [t for t in pmc.data if t.name == "Daily load"]
    assert daily.fill == "tozeroy"  # an area from zero, one step a day
    assert (np.asarray(daily.y) >= 0).all()
    assert pmc.layout.yaxis.rangemode == "tozero"  # the load panel


@pytest.mark.parametrize("theme", THEMES)
def test_t_ui_04_the_series_use_only_the_colours_of_the_theme(theme: Theme) -> None:
    tokens = tokens_for(theme)
    allowed = {
        tokens.ctl, tokens.atl, tokens.tsb, tokens.daily_load, tokens.error,
        *tokens.categories, *tokens.zones,
    }  # fmt: skip
    for kind, make in CHARTS.items():
        for trace in make(tokens).figure.data:
            color = (
                trace.marker.color
                if trace.type == "bar"
                else (trace.line.color if trace.mode == "lines" else trace.marker.color)
            )
            assert color in allowed, (kind, trace.name, color)


@pytest.mark.parametrize("theme", THEMES)
def test_t_ui_04_legends_have_one_distinct_named_entry_per_series(theme: Theme) -> None:
    tokens = tokens_for(theme)
    for kind, make in CHARTS.items():
        names = [t.name for t in make(tokens).figure.data]
        assert all(isinstance(n, str) and n for n in names), kind
        assert len(set(names)) == len(names), kind
    zones = [t.name for t in CHARTS["zones"](tokens).figure.data]
    assert zones == [zone_label(i) for i in range(5)]
    hr = [t.name for t in CHARTS["hr"](tokens).figure.data]
    assert hr == ["Heart rate", "Discarded"]


@pytest.mark.parametrize("theme", THEMES)
def test_t_ui_04_empty_data_gives_a_valid_figure_for_every_chart(theme: Theme) -> None:
    tokens = tokens_for(theme)
    empty_load = prepare_weekly_load_chart(weekly_load_by_sport([], THROUGH), LOAD_LABELS, tokens)
    empty_zones = prepare_weekly_zones_chart(weekly_zone_seconds([], THROUGH), ZONES_LABELS, tokens)
    empty_pmc = prepare_chart(compute_pmc({}, THROUGH, THROUGH), [], LABELS, tokens)
    one_sample = HeartRateTrace.of(HeartRateSeries(np.array([0.0]), np.array([120.0])), LIMITS)
    single = prepare_heart_rate_chart(one_sample, LIMITS, zone_starts_bpm(170), HR_LABELS, tokens)

    for prepared in (empty_load, empty_zones, empty_pmc, single):
        assert prepared.script.isascii()
        assert "NaN" not in prepared.script
        assert prepared.figure.layout.paper_bgcolor == tokens.background
    assert len(empty_load.figure.data) == 0
    assert len(empty_zones.figure.data) == 5  # the five zones are always there, at zero
    assert all((np.asarray(t.y) == 0).all() for t in empty_zones.figure.data)


def manual_weekly(
    items: list[DayLoad], through: date
) -> tuple[dict[tuple[date, Sport], float], dict[tuple[date, int], float]]:
    """Sums per ISO Monday written with dictionaries (oracle independent of the aggregator)."""
    load: dict[tuple[date, Sport], float] = defaultdict(float)
    zones: dict[tuple[date, int], float] = defaultdict(float)
    for item in items:
        if item.day > through:
            continue
        monday = item.day - timedelta(days=item.day.isoweekday() - 1)
        load[(monday, item.sport)] += item.load
        for zone, seconds in enumerate(item.zones.values):
            zones[(monday, zone)] += seconds
    return load, zones


@st.composite
def activities(draw: st.DrawFn) -> list[DayLoad]:
    count = draw(st.integers(0, 25))
    items: list[DayLoad] = []
    for _ in range(count):
        day = date(2025, 1, 1) + timedelta(days=draw(st.integers(0, 600)))  # even after THROUGH
        sport = draw(st.sampled_from(list(Sport)))
        zone_values = tuple(draw(st.integers(0, 4000)) / 2.0 for _ in range(5))
        load = draw(st.integers(0, 3000)) / 10.0
        items.append(DayLoad(day, sport, load, ZoneSeconds(zone_values)))  # type: ignore[arg-type]
    return items


@settings(max_examples=60, deadline=None)
@given(activities())
def test_t_ui_04_the_bars_hold_the_weekly_sums_of_every_week_and_open_on_the_last_26(
    items: list[DayLoad],
) -> None:
    tokens = tokens_for(Theme.LIGHT)
    load_by_week, zones_by_week = manual_weekly(items, THROUGH)
    load_figure = build_weekly_load_figure(
        weekly_load_by_sport(items, THROUGH), LOAD_LABELS, tokens
    )
    zones_figure = build_weekly_zones_figure(
        weekly_zone_seconds(items, THROUGH), ZONES_LABELS, tokens
    )
    last_monday = THROUGH - timedelta(days=THROUGH.isoweekday() - 1)
    first_monday = min((w for w, _ in load_by_week), default=None)
    # every week of the history is drawn (zooming out reaches them); the chart opens on the last 26
    weeks_count = 0 if first_monday is None else (last_monday - first_monday).days // 7 + 1
    shown_weeks = [last_monday - timedelta(weeks=n) for n in range(weeks_count - 1, -1, -1)]
    opening = load_figure.layout.xaxis.range
    if weeks_count > CHART_WEEKS:
        assert opening is not None
        assert opening[0] < shown_weeks[-CHART_WEEKS] < opening[1]
        assert opening[0] > shown_weeks[-CHART_WEEKS - 1]
    else:
        assert opening is None

    shown_set = set(shown_weeks)
    totals = {
        sport: sum(v for (w, s), v in load_by_week.items() if s is sport and w in shown_set)
        for sport in Sport
    }
    with_load = [sport for sport in Sport if totals[sport] > 0]
    # At most five series (R4): beyond that, the four sports with the most load remain (on a tie the
    # order of `Sport` counts) and all the others, with "Other", are summed into "Other".
    members: dict[Sport, list[Sport]] = {sport: [sport] for sport in with_load}
    if len(with_load) > MAX_SPORT_SERIES:
        named = [sport for sport in with_load if sport is not Sport.OTHER]
        kept = sorted(named, key=lambda sport: -totals[sport])[: MAX_SPORT_SERIES - 1]
        members = {sport: [sport] for sport in kept}
        members[Sport.OTHER] = [sport for sport in with_load if sport not in kept]

    drawn: set[Sport] = set()
    for trace in load_figure.data:
        sport = next(s for s, name in SPORT_NAMES.items() if name == trace.name)
        drawn.add(sport)
        assert trace.marker.color == sport_color(sport, tokens)
        xs = [d if isinstance(d, date) else date.fromisoformat(str(d)) for d in trace.x]
        assert (
            xs
            == [w for w in shown_weeks if first_monday is not None and w >= first_monday][
                -len(xs) :
            ]
        )
        for week, value in zip(xs, trace.y, strict=True):
            expected = sum(load_by_week.get((week, member), 0.0) for member in members[sport])
            assert value == pytest.approx(expected)
    assert drawn == set(members)
    assert len(load_figure.data) <= MAX_SPORT_SERIES
    for zone, trace in enumerate(zones_figure.data):
        for week, value in zip(trace.x, trace.y, strict=True):
            monday = week if isinstance(week, date) else date.fromisoformat(str(week))
            assert value == pytest.approx(zones_by_week.get((monday, zone), 0.0) / 3600.0)


def test_t_ui_04_the_heart_rate_curve_keeps_every_discarded_sample_marked_with_a_cross() -> None:
    tokens = tokens_for(Theme.DARK)
    trace = hr_trace()

    figure = build_heart_rate_figure(trace, LIMITS, zone_starts_bpm(170), HR_LABELS, tokens)

    curve, marks = figure.data
    assert curve.mode == "lines"
    assert marks.mode == "markers"
    assert marks.marker.symbol == "x"  # not only colour
    assert len(marks.x) == trace.discarded_count == 25
    assert set(np.asarray(marks.y)) == {40.0, 200.0}
    assert list(figure.layout.yaxis.range) == [35.0, 205.0]  # limits widened to the samples
    assert len(figure.layout.shapes) == 5
    assert [a.text for a in figure.layout.annotations] == [zone_label(i) for i in range(5)]


# --- Colour contrast (T-UI-05) -----------------------------------------------------------------


def _luminance(color: str) -> float:
    channels = [int(color[i : i + 2], 16) / 255 for i in (1, 3, 5)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast(first: str, second: str) -> float:
    high, low = sorted((_luminance(first), _luminance(second)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def blend(foreground: str, background: str, alpha: float) -> str:
    """Visible colour of `foreground` with opacity `alpha` over `background`."""
    mixed = [
        round(alpha * int(foreground[i : i + 2], 16) + (1 - alpha) * int(background[i : i + 2], 16))
        for i in (1, 3, 5)
    ]
    return "#{:02x}{:02x}{:02x}".format(*mixed)


@pytest.mark.parametrize("theme", THEMES)
def test_t_ui_05_text_colours_reach_4_5_to_1_and_the_axis_line_3_to_1(theme: Theme) -> None:
    tokens = tokens_for(theme)

    for name in ("text", "text_muted", "error"):
        assert contrast(getattr(tokens, name), tokens.background) >= 4.5, name
    assert contrast(tokens.axis_line, tokens.background) >= 3.0


@pytest.mark.parametrize("theme", THEMES)
def test_t_ui_05_every_series_and_zone_colour_reaches_3_to_1_on_the_background(
    theme: Theme,
) -> None:
    tokens = tokens_for(theme)
    series = {"ctl": tokens.ctl, "atl": tokens.atl, "tsb": tokens.tsb, "error": tokens.error}
    series |= {f"category{i}": c for i, c in enumerate(tokens.categories)}
    series |= {f"zone{i + 1}": c for i, c in enumerate(tokens.zones)}

    for name, color in series.items():
        assert contrast(color, tokens.background) >= 3.0, (name, color)


@pytest.mark.parametrize("theme", THEMES)
def test_t_ui_05_the_daily_load_bars_as_drawn_reach_3_to_1(theme: Theme) -> None:
    tokens = tokens_for(theme)
    visible = blend(tokens.daily_load, tokens.background, tokens.daily_load_opacity)

    assert contrast(visible, tokens.background) >= 3.0


def band_colour(tokens: ThemeTokens, zone: int) -> str:
    return blend(tokens.zones[zone], tokens.background, tokens.band_opacity)


@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize("zone", range(5))
def test_t_ui_05_the_heart_rate_curve_reaches_3_to_1_on_each_zone_band(
    theme: Theme, zone: int
) -> None:
    tokens = tokens_for(theme)

    assert contrast(tokens.ctl, band_colour(tokens, zone)) >= 3.0


@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize("zone", range(5))
def test_t_ui_05_the_zone_names_in_the_heart_rate_chart_reach_4_5_to_1_on_their_band(
    theme: Theme, zone: int
) -> None:
    tokens = tokens_for(theme)
    figure = build_heart_rate_figure(hr_trace(), LIMITS, zone_starts_bpm(170), HR_LABELS, tokens)
    label_color = figure.layout.annotations[zone].font.color
    assert label_color == tokens.text_muted

    assert contrast(label_color, band_colour(tokens, zone)) >= 4.5


@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize("zone", range(5))
def test_t_ui_05_the_discarded_sample_crosses_reach_3_to_1_on_each_zone_band(
    theme: Theme, zone: int
) -> None:
    tokens = tokens_for(theme)

    assert contrast(tokens.error, band_colour(tokens, zone)) >= 3.0


def test_t_ui_05_the_two_load_lines_of_the_pmc_differ_by_more_than_the_colour() -> None:
    pmc, entries = small_history()
    figure = build_pmc_figure(pmc, entries, LABELS, tokens_for(Theme.LIGHT))
    lines = [t for t in figure.data if t.type == "scatter" and t.yaxis in (None, "y")]

    assert len({(t.line.dash, t.line.width) for t in lines}) == len(lines)


# --- Export in the real web engine -------------------------------------------------------------


def png_size(data: bytes) -> tuple[int, int]:
    """Checks signature, CRC, IHDR, decompressible IDAT and IEND; gives width and height."""
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    position, kinds, idat, header = 8, [], b"", b""
    while position < len(data):
        (length,) = struct.unpack(">I", data[position : position + 4])
        kind = data[position + 4 : position + 8]
        body = data[position + 8 : position + 8 + length]
        (crc,) = struct.unpack(">I", data[position + 8 + length : position + 12 + length])
        assert zlib.crc32(kind + body) == crc, kind
        kinds.append(kind)
        if kind == b"IHDR":
            header = body
        elif kind == b"IDAT":
            idat += body
        position += 12 + length
    assert kinds[0] == b"IHDR"
    assert kinds[-1] == b"IEND"
    assert b"IDAT" in kinds
    assert len(zlib.decompress(idat)) > 0
    width, height = struct.unpack(">II", header[:8])
    return width, height


def draw_and_export(
    qtbot: QtBot, tmp_path: Path, prepared: PreparedChart
) -> tuple[dict[str, Path], list[str]]:
    """Draws the figure in the web engine and presses "Save as SVG" and "Save as PNG"."""
    asked: list[str] = []
    targets: dict[str, Path] = {}

    def choose(name: str) -> Path | None:
        asked.append(name)
        target = tmp_path / Path(name).name
        targets[target.suffix] = target
        return target

    engine = ChartEngine(choose)
    view = ChartView(engine, "Test chart")
    engine.setParent(view)
    qtbot.addWidget(view)
    view.resize(900, 560)
    view.show()
    with qtbot.waitSignal(view.drawn, timeout=WAIT_MS):
        view.show_prepared(prepared)
    downloads: list[QWebEngineDownloadRequest] = []
    view.page.profile().downloadRequested.connect(downloads.append)  # besides the app's handler
    for title in ("Save as SVG", "Save as PNG"):
        done: list[object] = []
        view.page.runJavaScript(
            f"document.querySelector('[data-title=\"{title}\"]').click(); 1", done.append
        )
        qtbot.waitUntil(partial(bool, done), timeout=WAIT_MS)
        suffix = ".svg" if "SVG" in title else ".png"
        qtbot.waitUntil(partial(_target_finished, targets, suffix), timeout=WAIT_MS)
    qtbot.waitUntil(
        lambda: len(downloads) == 2 and all(d.isFinished() for d in downloads), timeout=WAIT_MS
    )  # the file is final only when the download has finished
    assert {d.state() for d in downloads} == {
        QWebEngineDownloadRequest.DownloadState.DownloadCompleted
    }
    return targets, asked


def _target_finished(targets: dict[str, Path], suffix: str) -> bool:
    return suffix in targets and _finished(targets[suffix])


def _finished(path: Path) -> bool:
    if not path.exists() or path.stat().st_size == 0:
        return False
    data = path.read_bytes()
    if path.suffix == ".svg":
        return data.rstrip().endswith(b"</svg>")
    return data.rstrip(b"\x00").endswith(b"IEND\xaeB`\x82")


def hex_and_rgb(color: str) -> tuple[str, str]:
    r, g, b = (int(color[i : i + 2], 16) for i in (1, 3, 5))
    return color.lower(), f"rgb({r}, {g}, {b})"


@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize("kind", list(CHARTS))
def test_t_ui_04_each_chart_exports_a_valid_svg_and_png_in_both_themes(
    qtbot: QtBot, tmp_path: Path, kind: str, theme: Theme
) -> None:
    tokens = tokens_for(theme)

    targets, asked = draw_and_export(qtbot, tmp_path, CHARTS[kind](tokens))

    assert sorted(asked) == sorted(f"{EXPORT_STEMS[kind]}.{ext}" for ext in ("svg", "png"))
    svg_text = targets[".svg"].read_text(encoding="utf-8")
    root = ElementTree.fromstring(svg_text)  # noqa: S314 - file produced by the local engine
    assert root.tag == "{http://www.w3.org/2000/svg}svg"
    assert float(str(root.get("width")).removesuffix("px")) > 0
    assert float(str(root.get("height")).removesuffix("px")) > 0
    assert "<script" not in svg_text.lower()
    assert any(form in svg_text.lower() for form in hex_and_rgb(tokens.background))
    texts = " ".join(
        "".join(node.itertext()) for node in root.iter("{http://www.w3.org/2000/svg}text")
    )
    first_axis = next(a.title.text for n, a in axes_of(CHARTS[kind](tokens).figure) if a.title.text)
    assert first_axis in texts or kind == "hr"  # the axis title is in the file
    width, height = png_size(targets[".png"].read_bytes())
    assert width >= 800
    assert height >= 500
    image = QImage.fromData(targets[".png"].read_bytes())
    assert (image.width(), image.height()) == (width, height)
    assert image.pixelColor(2, 2).name() == tokens.background.lower()  # the theme background


def test_t_ui_04_a_hostile_label_is_drawn_as_text_and_runs_nothing(
    qtbot: QtBot, tmp_path: Path
) -> None:
    hostile = (
        "<img src=x onerror=\"document.title='pwned'\"><script>document.title='pwned'</script>"
    )
    labels = WeeklyLoadLabels("Week", "Load", {**SPORT_NAMES, Sport.RUNNING: hostile}, CHROME)
    prepared = prepare_weekly_load_chart(
        weekly_load_by_sport(sample_day_loads(8), THROUGH), labels, tokens_for(Theme.LIGHT)
    )
    engine = ChartEngine(lambda _name: None)
    view = ChartView(engine, "Test chart")
    engine.setParent(view)
    qtbot.addWidget(view)
    view.resize(900, 560)
    view.show()
    with qtbot.waitSignal(view.drawn, timeout=WAIT_MS):
        view.show_prepared(prepared)
    box: list[str] = []
    view.page.runJavaScript(
        "JSON.stringify([document.title, document.querySelectorAll('img').length,"
        " document.querySelectorAll('#chart script').length])",
        box.append,
    )
    qtbot.waitUntil(lambda: bool(box), timeout=WAIT_MS)

    title, images, scripts = json.loads(box[0])
    assert title.startswith("drawn-")
    assert (images, scripts) == (0, 0)
