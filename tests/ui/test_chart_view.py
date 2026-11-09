# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""T-UI-04 and T-SEC-06 (partial): the Plotly page in the real web engine (QtWebEngine).

These tests start Chromium: if unstable in a screenless environment they must be isolated
here, without touching the pure tests of the figure and the interceptor.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QUrl
from PySide6.QtWidgets import QWidget
from pytestqt.qtbot import QtBot
from shiboken6 import isValid

from ponos_coach.ui.chart_payload import prepare_figure
from ponos_coach.ui.chart_view import (
    ChartEngine,
    ChartView,
    DateRange,
    RangeReport,
    parse_range_title,
)
from ponos_coach.ui.pmc_figure import EXPORT_FILE_STEM, build_pmc_figure
from ponos_coach.ui.tokens import Theme, tokens_for
from tests.ui.helpers import LABELS, small_history

WAIT_MS = 60_000


def evaluate(qtbot: QtBot, view: ChartView, expression: str) -> object:
    """Evaluate an expression in the page and wait for its result (without sleep)."""
    box: list[str] = []
    view.page.runJavaScript(f"JSON.stringify(({expression}))", box.append)
    qtbot.waitUntil(lambda: bool(box), timeout=WAIT_MS)
    return json.loads(box[0])


def execute(qtbot: QtBot, view: ChartView, statements: str) -> None:
    """Run statements in the page and wait until they are done."""
    done: list[object] = []
    view.page.runJavaScript(f"{statements}\n1", done.append)
    qtbot.waitUntil(lambda: bool(done), timeout=WAIT_MS)


def drawn_view(qtbot: QtBot, export_to: Path | None = None, *, linked_x: bool = False) -> ChartView:
    engine = ChartEngine(lambda _name: export_to)
    view = ChartView(engine, "Test chart")
    engine.setParent(view)  # the engine's profile lives as long as the view
    qtbot.addWidget(view)
    view.resize(900, 560)
    view.show()
    pmc, entries = small_history()
    figure = build_pmc_figure(pmc, entries, LABELS, tokens_for(Theme.LIGHT))
    with qtbot.waitSignal(view.drawn, timeout=WAIT_MS):
        view.show_prepared(
            prepare_figure(
                figure,
                LABELS.chrome,
                EXPORT_FILE_STEM,
                tokens_for(Theme.LIGHT),
                linked_x=linked_x,
            )
        )
    return view


def test_plotly_draws_the_series_and_legend_in_order_from_local_files_only(qtbot: QtBot) -> None:
    view = drawn_view(qtbot)

    legend = evaluate(
        qtbot,
        view,
        "Array.from(document.querySelectorAll('.legendtext')).map(e => e.textContent)",
    )
    assert legend == ["Fitness (CTL)", "Fatigue (ATL)", "Form (TSB)", "Daily load"]
    assert evaluate(qtbot, view, "document.getElementById('chart').data[3].y.length") == 10
    assert view.page.url() == QUrl("ponos://app/chart.html")


def test_page_cannot_reach_the_network_and_has_no_channel(qtbot: QtBot) -> None:
    view = drawn_view(qtbot)
    script = (
        "window.__fetch = 'pending'; window.__image = 'pending';"
        "fetch('https://example.com/').then(() => { window.__fetch = 'ok'; },"
        " () => { window.__fetch = 'blocked'; });"
        "var img = new Image(); img.onload = () => { window.__image = 'ok'; };"
        "img.onerror = () => { window.__image = 'blocked'; };"
        "img.src = 'http://example.com/x.png';"
    )
    execute(qtbot, view, script)

    qtbot.waitUntil(lambda: evaluate(qtbot, view, "window.__fetch") == "blocked", timeout=WAIT_MS)
    qtbot.waitUntil(lambda: evaluate(qtbot, view, "window.__image") == "blocked", timeout=WAIT_MS)
    assert evaluate(qtbot, view, "typeof qt") == "undefined"
    assert evaluate(qtbot, view, "typeof QWebChannel") == "undefined"


def test_modebar_buttons_carry_the_titles_from_the_configuration(qtbot: QtBot) -> None:
    view = drawn_view(qtbot)

    titles = evaluate(
        qtbot,
        view,
        "Array.from(document.querySelectorAll('.modebar-btn')).map(e => e.dataset.title)",
    )
    ticks = evaluate(
        qtbot,
        view,
        "Array.from(document.querySelectorAll('.x2tick text')).map(e => e.textContent)",
    )

    assert isinstance(titles, list)
    assert {"Zoom (test)", "Pan (test)", "Autoscale (test)", "Reset axes (test)"} <= set(titles)
    assert not {"Zoom", "Pan", "Autoscale", "Reset axes"} & set(titles)  # no Plotly English
    assert isinstance(ticks, list)
    assert ticks
    assert all(isinstance(tick, str) and tick for tick in ticks)  # dates are still formatted


@pytest.mark.parametrize(("title", "suffix"), [("Save as SVG", ".svg"), ("Save as PNG", ".png")])
def test_modebar_exports_svg_and_png_through_the_download_handler(
    qtbot: QtBot, tmp_path: Path, title: str, suffix: str
) -> None:
    target = tmp_path / f"chart{suffix}"
    view = drawn_view(qtbot, export_to=target)

    execute(qtbot, view, f"document.querySelector('[data-title=\"{title}\"]').click();")

    qtbot.waitUntil(lambda: target.exists() and target.stat().st_size > 0, timeout=WAIT_MS)
    qtbot.waitUntil(lambda: _complete(target), timeout=WAIT_MS)


def _complete(path: Path) -> bool:
    data = path.read_bytes()
    if path.suffix == ".svg":
        return data.rstrip().endswith(b"</svg>")
    return data.startswith(b"\x89PNG\r\n\x1a\n") and data.rstrip(b"\x00").endswith(
        b"IEND\xaeB`\x82"
    )


def test_disposing_the_engine_destroys_every_registered_page_and_then_the_profile(
    qtbot: QtBot,
) -> None:
    host = QWidget()
    qtbot.addWidget(host)
    engine = ChartEngine(lambda _name: None, host)
    views = [ChartView(engine, f"Chart {number}", host) for number in range(3)]
    pages = [view.page for view in views]
    profile = engine.start()
    assert profile is not None
    assert all(isValid(page) for page in pages)

    engine.dispose()

    assert not any(isValid(page) for page in pages)  # immediately, not in the destruction queue
    assert not isValid(profile)
    assert engine.disposed
    assert not engine.started
    assert not any(view.engine_started for view in views)
    views[0].start_engine()  # after teardown no page is born
    assert not views[0].engine_started
    engine.dispose()  # idempotent


def test_a_view_closed_earlier_has_already_destroyed_its_page_and_the_engine_skips_it(
    qtbot: QtBot,
) -> None:
    host = QWidget()
    qtbot.addWidget(host)
    engine = ChartEngine(lambda _name: None, host)
    early = ChartView(engine, "Early", host)
    late = ChartView(engine, "Late", host)
    early_page, late_page = early.page, late.page

    early.dispose()  # for example a closed dialog
    assert not isValid(early_page)
    early.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    engine.dispose()  # the destroyed view is not touched; the other is

    assert not isValid(late_page)


def test_a_zoom_by_the_user_is_reported_and_a_range_set_by_the_app_is_not(qtbot: QtBot) -> None:
    view = drawn_view(qtbot, linked_x=True)
    reports: list[object] = []
    view.x_range_changed.connect(reports.append)

    config = evaluate(qtbot, view, "document.getElementById('chart')._context.scrollZoom")
    assert config is True  # the wheel zooms
    assert evaluate(qtbot, view, "document.getElementById('chart').layout.dragmode") == "pan"

    view.set_x_range(DateRange(datetime(2026, 1, 3), datetime(2026, 1, 6)))
    qtbot.waitUntil(
        lambda: (
            evaluate(qtbot, view, "document.getElementById('chart').layout.xaxis.range[0]")
            == "2026-01-03 00:00:00"
        ),
        timeout=WAIT_MS,
    )
    execute(
        qtbot,
        view,
        "Plotly.relayout(document.getElementById('chart'),"
        " {'xaxis2.range[0]': '2026-01-02', 'xaxis2.range[1]': '2026-01-05 12:00'});",
    )
    qtbot.waitUntil(lambda: bool(reports), timeout=WAIT_MS)

    assert reports == [DateRange(datetime(2026, 1, 2), datetime(2026, 1, 5, 12))]


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        (
            "xrange|2026-01-02|2026-03-04 05:06:07.5|3",
            RangeReport(DateRange(datetime(2026, 1, 2), datetime(2026, 3, 4, 5, 6, 7, 500000))),
        ),
        ("xrange|auto|9", RangeReport(None)),
        ("xrange|2026-03-04|2026-01-02|1", None),  # backwards
        ("xrange|2026-01-02|2026-01-02|1", None),  # empty
        ("xrange|2026-01-02|<b>|1", None),
        ("xrange|2026-13-02|2026-14-02|1", None),
        ("xrange|2026-01-02|2026-01-03", None),
        ("xrange|" + "1" * 40 + "|2026-01-03|1", None),
        ("drawn-1", None),
    ],
)
def test_only_well_formed_ranges_pass_from_the_page_title(
    title: str, expected: RangeReport | None
) -> None:
    assert parse_range_title(title) == expected
