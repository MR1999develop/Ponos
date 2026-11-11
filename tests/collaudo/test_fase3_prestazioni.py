# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""T-PERF-01 (partial) for phase 3: overview and charts with 5,000 activities over ten years.

Long tests: only in the `collaudo` session. Every measurement is repeated and reported with p50,
p95 and
maximum (never the mean alone); the assertions are the plan's budgets where they can be measured
here and
loose caps elsewhere (machine noise makes a tight comparison unstable). The activities
are synthetic: HR is generated, the files are not real FIT files except where decoding is measured.

Measured here:
- overview from the full cache (opening a known profile) and after a change of the weights of all
  sports (no load in cache: full recalculation, with synthetic decoding);
- PMC (`compute_pmc`) and weekly aggregations;
- construction of the three figures and their JSON;
- drawing of the PMC in the web engine with 3,650 days;
- decoding cost of a real FIT (to estimate the real full recalculation);
- opening of the window from the Composition Root with 5,000 activities in cache.
"""

from __future__ import annotations

import statistics
import time
from collections.abc import Callable, Iterator, Sequence
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pytest
from PySide6.QtCore import QSettings, QTimer
from pytestqt.qtbot import QtBot

from ponos_coach.activities.decoding import decode_stored_activity
from ponos_coach.activities.domain import (
    Activity,
    ActivitySummary,
    FileFormat,
    HeartRateSeries,
    Sport,
)
from ponos_coach.load.domain import (
    ActivityLoad,
    LoadSource,
    ZoneSeconds,
    compute_pmc,
    load_fingerprint,
)
from ponos_coach.load.use_cases import ComputeProfileOverview, ProfileOverview
from ponos_coach.ui.chart_view import ChartEngine, ChartView
from ponos_coach.ui.load_figures import prepare_weekly_load_chart, prepare_weekly_zones_chart
from ponos_coach.ui.overview_state import Ready
from ponos_coach.ui.pmc_figure import ChartActivity, prepare_chart
from ponos_coach.ui.tokens import Theme, tokens_for
from tests.builders import fit_identity, make_record, physiology, physiology_entry
from tests.collaudo.fase3_support import TODAY
from tests.collaudo.support import RealProfile, create_profile
from tests.collaudo.test_fase3_grafici import LOAD_LABELS, ZONES_LABELS
from tests.fakes.archive import InMemoryActivityArchive
from tests.fixtures.generators.import_files import fit_run
from tests.ui.helpers import LABELS
from tests.ui.workspace_support import compose, make_settings

pytestmark = pytest.mark.collaudo

ACTIVITIES = 5000
YEARS_DAYS = 3650
SAMPLES = 3600  # one hour at one sample per second: the cost of the calculation is the real one
REPEATS = 7
WAIT_MS = 120_000
FIRST_DAY = date(2016, 7, 2)
SPORTS = (Sport.RUNNING, Sport.RUNNING, Sport.CYCLING, Sport.TRAIL, Sport.STRENGTH, Sport.HIKING)
BUDGET_PMC_S = 1.0  # PLAN 7.8: full PMC recalculation < 1 s
BUDGET_WINDOW_S = 2.0  # PLAN 7.8: window usable within 2 s of start-up
LOOSE_COLD_S = 120.0  # loose cap: recalculation from scratch with synthetic decoding
LOOSE_FIGURE_S = 2.0


def report(line: str) -> None:
    print(line)  # noqa: T201 - the report of the measurements


def describe(values: Sequence[float]) -> str:
    ordered = sorted(values)
    p95 = ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))]
    return (
        f"n={len(ordered)} p50={statistics.median(ordered) * 1000:.1f}ms "
        f"p95={p95 * 1000:.1f}ms max={ordered[-1] * 1000:.1f}ms"
    )


def measure(action: Callable[[], object], repeats: int = REPEATS) -> list[float]:
    times: list[float] = []
    for _ in range(repeats):
        started = time.perf_counter()
        action()
        times.append(time.perf_counter() - started)
    return times


def percentile95(values: Sequence[float]) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))]


def start_of(number: int) -> datetime:
    day = FIRST_DAY + timedelta(days=int(number * YEARS_DAYS / ACTIVITIES))
    return datetime(day.year, day.month, day.day, 7, 0, tzinfo=UTC) + timedelta(minutes=number % 7)


def sport_of(number: int) -> Sport:
    return SPORTS[number % len(SPORTS)]


def synthetic_activity(number: int) -> Activity:
    rng = np.random.default_rng(number)
    seconds = np.arange(SAMPLES, dtype=float)
    bpm = np.clip(145 + 25 * np.sin(seconds / 420.0) + rng.normal(0, 4, SAMPLES), 60, 188)
    return Activity(
        fit_identity(number), FileFormat.FIT, sport_of(number), start_of(number),
        float(SAMPLES), float(SAMPLES), HeartRateSeries(seconds, bpm), ActivitySummary(),
    )  # fmt: skip


def synthetic_decode(content: bytes, _file_format: FileFormat) -> Activity:
    return synthetic_activity(int(content.removeprefix(b"hr:")))


@pytest.fixture
def profile(tmp_path: Path) -> Iterator[RealProfile]:
    opened = create_profile(tmp_path)
    yield opened
    opened.close()


def fill_index(profile: RealProfile, archive: InMemoryActivityArchive | None) -> None:
    """5,000 activities in the real index (SQLite), in blocks of 500 per transaction."""
    index = profile.session.activities
    for first in range(0, ACTIVITIES, 500):
        with index.transaction():
            for number in range(first, first + 500):
                record = make_record(
                    fit_identity(number),
                    start=start_of(number),
                    elapsed_s=float(SAMPLES),
                    sport=sport_of(number),
                )
                if archive is not None:
                    stored = archive.store(f"hr:{number}".encode(), FileFormat.FIT, record.start)
                    record = replace(record, archived=stored)
                index.add(record)


def fill_cache(profile: RealProfile) -> None:
    """A cached load for every activity, with the fingerprint the overview expects."""
    fingerprint = load_fingerprint(physiology())
    zones = ZoneSeconds((600.0, 900.0, 1200.0, 600.0, 300.0))
    with profile.session.activities.transaction():
        for record in profile.session.activities.list_by_start():
            load = ActivityLoad(
                60.0 * (1 + record.start.day % 3), SAMPLES, 0, 0.0,
                LoadSource.HEART_RATE, zones,
            )  # fmt: skip
            profile.session.load_cache.put(record.identity, fingerprint, load)


def overview_of(
    profile: RealProfile, archive: InMemoryActivityArchive | None = None
) -> ProfileOverview:
    session = profile.session
    return ComputeProfileOverview(
        session.activities,
        archive if archive is not None else profile.archive,
        session.physiology,
        session.load_cache,
        synthetic_decode,
        timezone=ZoneInfo("Europe/Rome"),
        today=lambda: TODAY,
    ).execute()


def test_t_perf_01_overview_from_the_cache_pmc_aggregations_and_figures_with_5000_activities(
    profile: RealProfile, capsys: pytest.CaptureFixture[str]
) -> None:
    fill_index(profile, None)
    fill_cache(profile)
    tokens = tokens_for(Theme.LIGHT)

    warm = measure(lambda: overview_of(profile))
    overview = overview_of(profile)
    daily: dict[date, float] = {}
    for item in overview.activities:
        daily[item.local_day] = daily.get(item.local_day, 0.0) + item.load.trimp_eq
    first_day = min(daily)
    pmc_times = measure(lambda: compute_pmc(daily, first_day, TODAY), repeats=15)
    aggregation = measure(
        lambda: (overview.weekly_load(TODAY), overview.weekly_zones(TODAY)), repeats=15
    )
    entries = [
        ChartActivity(a.local_day, a.load.trimp_eq, f"a{n}")
        for n, a in enumerate(overview.activities)
    ]
    pmc_figure = measure(lambda: prepare_chart(overview.pmc, entries, LABELS, tokens))
    load_figure = measure(
        lambda: prepare_weekly_load_chart(overview.weekly_load(TODAY), LOAD_LABELS, tokens)
    )
    zones_figure = measure(
        lambda: prepare_weekly_zones_chart(overview.weekly_zones(TODAY), ZONES_LABELS, tokens)
    )
    payload = prepare_chart(overview.pmc, entries, LABELS, tokens).script

    with capsys.disabled():
        report(
            f"\nT-PERF-01 [{ACTIVITIES} activities in {YEARS_DAYS} days, real SQLite, full cache]"
        )
        report(f"  overview from the cache:   {describe(warm)}")
        report(f"  PMC ({len(overview.pmc)} days): {describe(pmc_times)}")
        report(f"  aggregazioni settimanali: {describe(aggregation)}")
        report(f"  figura PMC + JSON:        {describe(pmc_figure)} ({len(payload) // 1024} KiB)")
        report(f"  load figure + JSON:       {describe(load_figure)}")
        report(f"  figura zone + JSON:       {describe(zones_figure)}")
    assert len(overview.activities) == ACTIVITIES
    assert len(overview.pmc) >= YEARS_DAYS
    assert percentile95(pmc_times) < BUDGET_PMC_S
    assert percentile95(warm) + percentile95(pmc_times) < BUDGET_PMC_S  # PLAN: full PMC < 1 s
    assert percentile95(aggregation) < 0.5
    for times in (pmc_figure, load_figure, zones_figure):
        assert percentile95(times) < LOOSE_FIGURE_S


def test_t_perf_01_full_recalculation_after_a_change_of_the_threshold_with_5000_activities(
    profile: RealProfile, capsys: pytest.CaptureFixture[str]
) -> None:
    archive = InMemoryActivityArchive()
    fill_index(profile, archive)
    session = profile.session
    cold: list[float] = []
    for run, threshold in enumerate((160, 165, 168)):  # every round changes the fingerprint
        session.physiology.put(physiology_entry(TODAY, threshold))
        started = time.perf_counter()
        overview = overview_of(profile, archive)
        cold.append(time.perf_counter() - started)
        assert len(overview.activities) == ACTIVITIES, run
        assert overview.excluded == ()
        assert all(a.load.trimp_eq > 0 for a in overview.activities)
    started = time.perf_counter()
    again = overview_of(
        profile, archive
    )  # same parameters as the last round: everything from the cache
    cached_s = time.perf_counter() - started

    with capsys.disabled():
        report(f"\nT-PERF-01 full recalculation ({SAMPLES} samples per activity, fake decoding)")
        report(
            f"  from scratch: {describe(cold)}  "
            f"per activity ~{statistics.median(cold) / ACTIVITIES * 1e6:.0f} us"
        )
        report(f"  right after, from the cache: {cached_s * 1000:.1f}ms")
    assert len(again.activities) == ACTIVITIES
    assert max(cold) < LOOSE_COLD_S
    assert cached_s < statistics.median(cold)  # the cache really works


def test_t_perf_01_cost_of_decoding_a_real_fit_and_the_extrapolated_cold_recalculation(
    capsys: pytest.CaptureFixture[str],
) -> None:
    files = [fit_run(n) for n in range(30)]
    times = measure(
        lambda: [decode_stored_activity(data, FileFormat.FIT) for data in files], repeats=5
    )
    per_file = [t / len(files) for t in times]
    extrapolated = statistics.median(per_file) * ACTIVITIES

    with capsys.disabled():
        report(
            f"\nT-PERF-01 decoding of a real FIT (30 min, one sample every 10 s): "
            f"{describe(per_file)}"
        )
        report(
            f"  from scratch for {ACTIVITIES} activities ~{extrapolated:.1f}s: above the "
            "1 s PMC budget, which only holds from the cache"
        )
    assert extrapolated < LOOSE_COLD_S


def test_t_perf_01_the_pmc_chart_redraws_in_the_web_engine_with_3650_days(
    qtbot: QtBot, capsys: pytest.CaptureFixture[str]
) -> None:
    days = YEARS_DAYS
    daily = {FIRST_DAY + timedelta(days=d): float(40 + (d * 37) % 90) for d in range(days)}
    pmc = compute_pmc(daily, FIRST_DAY, FIRST_DAY + timedelta(days=days - 1))
    entries = [ChartActivity(day, load, f"a{n}") for n, (day, load) in enumerate(daily.items())]
    tokens = tokens_for(Theme.DARK)
    prepared = prepare_chart(pmc, entries, LABELS, tokens)
    engine = ChartEngine(lambda _name: None)
    view = ChartView(engine, "PMC")
    engine.setParent(view)
    qtbot.addWidget(view)
    view.resize(1000, 600)
    view.show()
    times: list[float] = []

    for _ in range(6):
        started = time.perf_counter()
        with qtbot.waitSignal(view.drawn, timeout=WAIT_MS):
            view.show_prepared(prepared)
        times.append(time.perf_counter() - started)

    first, rest = times[0], times[1:]  # the first drawing includes the engine start
    with capsys.disabled():
        report(f"\nT-PERF-01 PMC drawing, {days} days, web engine: first {first * 1000:.0f}ms")
        report(f"  redraws: {describe(rest)}  (interactive budget 0.4 s, only as a reference)")
    assert percentile95(rest) < 5.0  # loose cap: the 0.4 s budget concerns zoom, not this


def open_once(qtbot: QtBot, tmp_path: Path, settings: QSettings) -> tuple[float, float]:
    """Opens the window, waits for the overview and measures an input event; then closes it."""
    window = compose(tmp_path, settings)
    qtbot.addWidget(window)
    started = time.perf_counter()
    window.start()
    qtbot.waitUntil(lambda: isinstance(window.controller.overview_state, Ready), timeout=WAIT_MS)
    opened_s = time.perf_counter() - started
    tick = time.perf_counter()
    handled: list[float] = []
    QTimer.singleShot(0, lambda: handled.append(time.perf_counter() - tick))
    qtbot.waitUntil(lambda: bool(handled), timeout=2000)
    state = window.controller.overview_state
    assert isinstance(state, Ready)
    assert len(state.overview.activities) == ACTIVITIES
    window.close()
    qtbot.waitUntil(lambda: window.is_closed, timeout=WAIT_MS)
    return opened_s, handled[0]


def test_t_perf_01_the_window_opens_a_profile_of_5000_activities_and_shows_the_overview(
    qtbot: QtBot, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    opened = create_profile(tmp_path)
    fill_index(opened, None)
    fill_cache(opened)
    opened.close()
    times: list[float] = []
    latencies: list[float] = []

    for run in range(3):
        settings = make_settings(tmp_path / f"s{run}")
        opened_s, latency_s = open_once(qtbot, tmp_path, settings)
        times.append(opened_s)
        latencies.append(latency_s)

    with capsys.disabled():
        report(f"\nT-PERF-01 window opening with {ACTIVITIES} activities (full cache)")
        report(f"  from start to overview ready: {describe(times)}")
        report(f"  an input event right after:   {describe(latencies)}")
    assert percentile95(times) < BUDGET_WINDOW_S
    assert max(latencies) < 0.1
