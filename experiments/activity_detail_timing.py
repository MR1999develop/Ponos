# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Time, phase by phase, what opening an activity's detail window costs.

Synthetic 4 hour run at 1 Hz (HR, GPS, altitude, speed) in memory: no personal data. Phases:
use case (read + decode, GPS track, load and zones), HR figure and JSON, then the window
(dialog, web engine start, first drawing). Usage:

    uv run --locked python experiments/activity_detail_timing.py [--runs 5] [--hours 4]

Prints median and maximum in milliseconds for each phase.
"""

from __future__ import annotations

import argparse
import math
import os
import statistics
import sys
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu")

from PySide6.QtCore import QCoreApplication, QEventLoop, QTimer
from PySide6.QtWidgets import QApplication
from tests.builders import IMPORTED_AT, physiology_entry
from tests.fixtures.generators import fit_writer as fw
from tests.load.test_profile_overview import FIRST_PARAMS_DAY, ROME, World

from ponos_coach.activities.decoding import decode_stored_activity, decode_stored_track
from ponos_coach.activities.domain import ActivityOrigin, ActivityRecord, FileFormat
from ponos_coach.load.activity_detail import ActivityDetail, GetActivityDetail
from ponos_coach.ui.activity_detail import DetailReady
from ponos_coach.ui.activity_detail_dialog import ActivityDetailDialog
from ponos_coach.ui.chart_payload import PreparedChart
from ponos_coach.ui.chart_texts import chart_chrome
from ponos_coach.ui.chart_view import ChartEngine
from ponos_coach.ui.hr_figure import HeartRateLabels, prepare_heart_rate_chart
from ponos_coach.ui.tokens import Theme, tokens_for
from ponos_coach.ui.web_policy import register_app_scheme

START = datetime(2026, 2, 10, 7, 0, tzinfo=UTC)


def _world(hours: float) -> tuple[World, ActivityRecord]:
    count = int(hours * 3600)
    first = fw.datetime_to_fit(START)
    samples = []
    for i in range(count):
        bpm = int(150 + 25 * math.sin(i / 600) + 5 * math.sin(i / 7))
        samples.append(
            fw.Sample(
                first + i,
                bpm,
                lat_deg=45.0 + 0.01 * math.sin(i / 3000),
                lon_deg=9.0 + 0.01 * math.cos(i / 3000),
                altitude_m=200 + 30 * math.sin(i / 900),
                distance_m=i * 3.0,
                speed_ms=3.0,
            )
        )
    spec = fw.ActivitySpec(START, fw.Sport.RUNNING, fw.SubSport.GENERIC, samples)
    data = fw.build_activity(spec)
    world = World()
    world.profile.physiology.put(physiology_entry(FIRST_PARAMS_DAY))
    activity = decode_stored_activity(data, FileFormat.FIT)
    archived = world.archive.store(data, FileFormat.FIT, activity.start)
    record = ActivityRecord.from_activity(activity, archived, ActivityOrigin.IMPORT, IMPORTED_AT)
    world.profile.activities.add(record)
    return world, record


def _timed(table: dict[str, list[float]], name: str, work: Callable[[], object]) -> object:
    begin = time.perf_counter()
    result = work()
    table.setdefault(name, []).append((time.perf_counter() - begin) * 1000)
    return result


def _wait(app: QApplication, done: Callable[[], bool], timeout_s: float = 30.0) -> None:
    loop = QEventLoop()
    poll = QTimer()
    poll.setInterval(5)
    deadline = time.perf_counter() + timeout_s

    def check() -> None:
        if done() or time.perf_counter() > deadline:
            loop.quit()

    poll.timeout.connect(check)
    poll.start()
    loop.exec()
    poll.stop()
    app.processEvents()


def _one_run(
    app: QApplication,
    engine: ChartEngine,
    world: World,
    record: ActivityRecord,
    table: dict[str, list[float]],
    *,
    prewarm: bool,
) -> None:
    tokens = tokens_for(Theme.LIGHT)
    labels = HeartRateLabels("Time", "Heart rate", "Heart rate", "Discarded", chart_chrome())
    use_case = GetActivityDetail(
        world.profile.activities,
        world.archive,
        world.profile.physiology,
        decode_stored_activity,
        timezone=ROME,
    )
    data = world.archive.read(record.archived, 50_000_000)
    _timed(table, "1a decode activity", lambda: decode_stored_activity(data, FileFormat.FIT))
    _timed(
        table,
        "1b decode GPS track (no longer done)",
        lambda: decode_stored_track(data, FileFormat.FIT),
    )

    # End to end: window opened, then what the worker does (use case + figure), then the drawing.
    begin = time.perf_counter()
    dialog = ActivityDetailDialog(engine, ZoneInfo("Europe/Rome"), theme=lambda: Theme.LIGHT)
    dialog.show()
    table.setdefault("3a dialog built + shown", []).append((time.perf_counter() - begin) * 1000)
    drawn: list[bool] = []
    dialog.chart.drawn.connect(lambda: drawn.append(True))
    if prewarm:
        app.processEvents()
        dialog.warm_up_chart()
    worker_begin = time.perf_counter()

    def work() -> tuple[ActivityDetail, PreparedChart, float, float]:
        """What the data thread does."""
        detail = use_case(record.identity)
        assert isinstance(detail, ActivityDetail)  # noqa: S101 - script
        assert detail.trace is not None  # noqa: S101
        decoded = time.perf_counter()
        chart = prepare_heart_rate_chart(
            detail.trace, detail.limits, detail.zone_starts_bpm, labels, tokens
        )
        return detail, chart, decoded, time.perf_counter()

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(work)
        _wait(app, future.done)  # the UI thread keeps serving the page meanwhile
    detail, chart, decoded, finished = future.result()
    table.setdefault("1 use case total", []).append((decoded - worker_begin) * 1000)
    table.setdefault("2 HR figure + JSON", []).append((finished - decoded) * 1000)
    table.setdefault("W worker total (1 + 2)", []).append((finished - worker_begin) * 1000)
    shown = time.perf_counter()
    dialog.show_outcome(DetailReady(detail, chart))
    table.setdefault("3b show_outcome (UI thread)", []).append((time.perf_counter() - shown) * 1000)
    _wait(app, lambda: bool(drawn))
    table.setdefault("3c data ready to first drawing", []).append(
        (time.perf_counter() - shown) * 1000
    )
    table.setdefault("E open to first drawing (end to end)", []).append(
        (time.perf_counter() - begin) * 1000
    )
    dialog.close()
    app.processEvents()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--hours", type=float, default=4.0)
    parser.add_argument("--prewarm", action="store_true", help="start the web page early")
    args = parser.parse_args()
    register_app_scheme()  # before the application object, as the real start does
    app = QApplication(sys.argv)
    QCoreApplication.setApplicationName("activity-detail-timing")
    world, record = _world(args.hours)
    table: dict[str, list[float]] = {}
    engine = ChartEngine(lambda _name: None)
    for _ in range(args.runs):
        _one_run(app, engine, world, record, table, prewarm=args.prewarm)
    engine.dispose()
    print(
        f"{args.hours} h at 1 Hz, {args.runs} runs, prewarm={args.prewarm}, "
        f"Python {sys.version.split()[0]}"
    )
    for name in sorted(table):
        values = table[name]
        print(f"{name:40s} median {statistics.median(values):8.1f} ms   max {max(values):8.1f} ms")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
