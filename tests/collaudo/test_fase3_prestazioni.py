# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""T-PERF-01 (parziale) per la fase 3: panoramica e grafici con 5.000 attività in dieci anni.

Test lunghi: solo nella sessione `collaudo`. Ogni misura si ripete e si riporta con p50, p95 e
massimo (mai la sola media); le asserzioni sono i budget del piano dove sono misurabili qui e
tetti larghi altrove (il rumore della macchina rende instabile un confronto stretto). Le attività
sono sintetiche: la FC è generata, i file non sono FIT veri tranne dove si misura la decodifica.

Misurato qui:
- panoramica dalla cache piena (apertura di un profilo noto) e dopo un cambio dei pesi di tutti
  gli sport (nessun carico in cache: ricalcolo completo, con decodifica sintetica);
- PMC (`compute_pmc`) e aggregazioni settimanali;
- costruzione delle tre figure e del loro JSON;
- disegno del PMC nel motore web con 3.650 giorni;
- costo di decodifica di un FIT vero (per stimare il ricalcolo completo reale);
- apertura della finestra dal Composition Root con 5.000 attività in cache.
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
    SportWeight,
    ZoneSeconds,
    compute_pmc,
    load_fingerprint,
)
from ponos_coach.load.use_cases import ComputeProfileOverview, ProfileOverview, SetSportWeights
from ponos_coach.ui.chart_view import ChartEngine, ChartView
from ponos_coach.ui.load_figures import prepare_weekly_load_chart, prepare_weekly_zones_chart
from ponos_coach.ui.overview_state import Ready
from ponos_coach.ui.pmc_figure import ChartActivity, prepare_chart
from ponos_coach.ui.tokens import Theme, tokens_for
from tests.builders import fit_identity, make_record, physiology
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
SAMPLES = 3600  # un'ora a un campione al secondo: il costo del calcolo è quello vero
REPEATS = 7
WAIT_MS = 120_000
FIRST_DAY = date(2016, 7, 2)
SPORTS = (Sport.RUNNING, Sport.RUNNING, Sport.CYCLING, Sport.TRAIL, Sport.STRENGTH, Sport.HIKING)
BUDGET_PMC_S = 1.0  # PLAN 7.8: ricalcolo completo del PMC < 1 s
BUDGET_WINDOW_S = 2.0  # PLAN 7.8: finestra usabile entro 2 s dall'avvio
LOOSE_COLD_S = 120.0  # tetto largo: il ricalcolo da zero con decodifica sintetica
LOOSE_FIGURE_S = 2.0


def report(line: str) -> None:
    print(line)  # noqa: T201 - il rapporto delle misure


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
    """5.000 attività nell'indice vero (SQLite), a blocchi di 500 per transazione."""
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
    """Un carico in cache per ogni attività, con l'impronta che la panoramica si aspetta."""
    fingerprint = load_fingerprint(physiology(), {})
    zones = ZoneSeconds((600.0, 900.0, 1200.0, 600.0, 300.0))
    with profile.session.activities.transaction():
        for record in profile.session.activities.list_by_start():
            load = ActivityLoad(
                60.0, 60.0, 60.0 * (1 + record.start.day % 3), SAMPLES, 0, 0.0,
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
        sport_weights=session.sport_weights,
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
        daily[item.local_day] = daily.get(item.local_day, 0.0) + item.load.load
    first_day = min(daily)
    pmc_times = measure(lambda: compute_pmc(daily, first_day, TODAY), repeats=15)
    aggregation = measure(
        lambda: (overview.weekly_load(TODAY), overview.weekly_zones(TODAY)), repeats=15
    )
    entries = [
        ChartActivity(a.local_day, a.load.load, f"a{n}") for n, a in enumerate(overview.activities)
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
            f"\nT-PERF-01 [{ACTIVITIES} attivita in {YEARS_DAYS} giorni, SQLite vero, cache piena]"
        )
        report(f"  panoramica dalla cache:   {describe(warm)}")
        report(f"  PMC ({len(overview.pmc)} giorni): {describe(pmc_times)}")
        report(f"  aggregazioni settimanali: {describe(aggregation)}")
        report(f"  figura PMC + JSON:        {describe(pmc_figure)} ({len(payload) // 1024} KiB)")
        report(f"  figura carico + JSON:     {describe(load_figure)}")
        report(f"  figura zone + JSON:       {describe(zones_figure)}")
    assert len(overview.activities) == ACTIVITIES
    assert len(overview.pmc) >= YEARS_DAYS
    assert percentile95(pmc_times) < BUDGET_PMC_S
    assert percentile95(warm) + percentile95(pmc_times) < BUDGET_PMC_S  # PLAN: PMC completo < 1 s
    assert percentile95(aggregation) < 0.5
    for times in (pmc_figure, load_figure, zones_figure):
        assert percentile95(times) < LOOSE_FIGURE_S


def test_t_perf_01_full_recalculation_after_a_change_of_every_weight_with_5000_activities(
    profile: RealProfile, capsys: pytest.CaptureFixture[str]
) -> None:
    archive = InMemoryActivityArchive()
    fill_index(profile, archive)
    session = profile.session
    save = SetSportWeights(session.activities, session.sport_weights)
    cold: list[float] = []
    for run, factor in enumerate((1.5, 0.75, 1.25)):  # ogni giro cambia l'impronta di ogni sport
        save({sport: SportWeight(factor) for sport in Sport})
        started = time.perf_counter()
        overview = overview_of(profile, archive)
        cold.append(time.perf_counter() - started)
        assert len(overview.activities) == ACTIVITIES, run
        assert overview.excluded == ()
        assert all(
            a.load.load == pytest.approx(a.load.hr_tss * factor) for a in overview.activities
        )
    started = time.perf_counter()
    again = overview_of(profile, archive)  # stessi pesi dell'ultimo giro: tutto dalla cache
    cached_s = time.perf_counter() - started

    with capsys.disabled():
        report(
            f"\nT-PERF-01 ricalcolo completo ({SAMPLES} campioni per attivita, decodifica finta)"
        )
        report(
            f"  da zero: {describe(cold)}  "
            f"per attivita ~{statistics.median(cold) / ACTIVITIES * 1e6:.0f} us"
        )
        report(f"  subito dopo, dalla cache: {cached_s * 1000:.1f}ms")
    assert len(again.activities) == ACTIVITIES
    assert max(cold) < LOOSE_COLD_S
    assert cached_s < statistics.median(cold)  # la cache serve davvero


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
            f"\nT-PERF-01 decodifica di un FIT vero (30 min, un campione ogni 10 s): "
            f"{describe(per_file)}"
        )
        report(
            f"  ricalcolo da zero di {ACTIVITIES} attivita ~{extrapolated:.1f}s: oltre il "
            "budget di 1 s del PMC, che vale solo dalla cache"
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

    first, rest = times[0], times[1:]  # il primo disegno comprende l'avvio del motore
    with capsys.disabled():
        report(
            f"\nT-PERF-01 disegno del PMC, {days} giorni, motore web: primo {first * 1000:.0f}ms"
        )
        report(f"  ridisegni: {describe(rest)}  (budget interattivo 0,4 s, solo come riferimento)")
    assert percentile95(rest) < 5.0  # tetto largo: il budget di 0,4 s riguarda lo zoom, non questo


def open_once(qtbot: QtBot, tmp_path: Path, settings: QSettings) -> tuple[float, float]:
    """Apre la finestra, attende la panoramica e misura un evento di input; poi la chiude."""
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
        report(f"\nT-PERF-01 apertura della finestra con {ACTIVITIES} attivita (cache piena)")
        report(f"  dall'avvio alla panoramica pronta: {describe(times)}")
        report(f"  un evento di input subito dopo:     {describe(latencies)}")
    assert percentile95(times) < BUDGET_WINDOW_S
    assert max(latencies) < 0.1
