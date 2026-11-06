# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""T-UI-04 e T-SEC-06 (parziali): la pagina Plotly nel motore web reale (QtWebEngine).

Questi test avviano Chromium: se in un ambiente senza schermo fossero instabili vanno isolati
qui, senza toccare i test puri della figura e dell'intercettore.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from PySide6.QtCore import QUrl
from pytestqt.qtbot import QtBot

from ponos_coach.ui.chart_payload import prepare_figure
from ponos_coach.ui.chart_view import ChartEngine, ChartView
from ponos_coach.ui.pmc_figure import EXPORT_FILE_STEM, build_pmc_figure
from ponos_coach.ui.tokens import Theme, tokens_for
from tests.ui.helpers import LABELS, small_history

WAIT_MS = 60_000


def evaluate(qtbot: QtBot, view: ChartView, expression: str) -> object:
    """Valuta un'espressione nella pagina e ne attende il risultato (senza sleep)."""
    box: list[str] = []
    view.page.runJavaScript(f"JSON.stringify(({expression}))", box.append)
    qtbot.waitUntil(lambda: bool(box), timeout=WAIT_MS)
    return json.loads(box[0])


def execute(qtbot: QtBot, view: ChartView, statements: str) -> None:
    """Esegue istruzioni nella pagina e attende che siano finite."""
    done: list[object] = []
    view.page.runJavaScript(f"{statements}\n1", done.append)
    qtbot.waitUntil(lambda: bool(done), timeout=WAIT_MS)


def drawn_view(qtbot: QtBot, export_to: Path | None = None) -> ChartView:
    engine = ChartEngine(lambda _name: export_to)
    view = ChartView(engine, "Test chart")
    engine.setParent(view)  # il profilo del motore vive quanto la vista
    qtbot.addWidget(view)
    view.resize(900, 560)
    view.show()
    pmc, entries = small_history()
    figure = build_pmc_figure(pmc, entries, LABELS, tokens_for(Theme.LIGHT))
    with qtbot.waitSignal(view.drawn, timeout=WAIT_MS):
        view.show_prepared(
            prepare_figure(figure, LABELS.chrome, EXPORT_FILE_STEM, tokens_for(Theme.LIGHT))
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
    assert evaluate(qtbot, view, "document.querySelectorAll('.bars .point').length") == 10
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
    assert not {"Zoom", "Pan", "Autoscale", "Reset axes"} & set(titles)  # niente inglese di Plotly
    assert isinstance(ticks, list)
    assert ticks
    assert all(isinstance(tick, str) and tick for tick in ticks)  # le date si formattano ancora


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
