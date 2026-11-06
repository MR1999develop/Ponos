# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Sonda della reattivita dell'interfaccia, da avviare in un processo nuovo.

Uso: `python collaudo_ui_probe.py <cartella-di-lavoro> <quanti-FIT>`.

Compone la finestra come `main()` (Composition Root, Qt offscreen), crea un profilo, importa
`quanti` FIT sintetici veri (nessun cancello) e aspetta la panoramica. Un timer da 10 ms misura,
per fase, gli intervalli tra due scatti: il massimo e quanto il ciclo di eventi e rimasto fermo.
Un processo per misura perche il primo disegno di un grafico in un processo (avvio del motore
web) costa piu dei successivi. Ultima riga dell'output: un JSON `{fase: [intervalli in s]}`; la
chiave `avvio_motore` ha invece la durata (una sola) dell'avvio del motore web nel thread della UI,
che la finestra fa subito dopo il primo `show()` e che non e riducibile (vedi `TIDY.md`).
"""

from __future__ import annotations

import json
import os
import sys
import time
from collections.abc import Callable
from datetime import date
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu")
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))  # radice del repository: `tests.*`

from PySide6.QtCore import QEventLoop, QSettings, QTimer
from PySide6.QtWidgets import QApplication

from ponos_coach.__main__ import compose_main_window, parse_command_line
from ponos_coach.load.domain import (
    HeartRateLimits,
    Physiology,
    Sex,
    ThresholdHeartRate,
)
from ponos_coach.profiles.domain import ProfileName
from ponos_coach.profiles.use_cases import NewProfileRequest
from ponos_coach.ui.bootstrap import prepare_qt
from ponos_coach.ui.main_window import MainWindow
from ponos_coach.ui.overview_state import Ready
from tests.fixtures.generators.import_files import fit_run

PHASES = ("importazione", "panoramica", "disegno")
WAIT_S = 300.0
TICK_MS = 10


def wait_until(app: QApplication, condition: Callable[[], bool]) -> None:
    deadline = time.monotonic() + WAIT_S
    while not condition():
        if time.monotonic() > deadline:
            raise TimeoutError("la sonda non ha ricevuto l'esito atteso")
        app.processEvents(QEventLoop.ProcessEventsFlag.WaitForMoreEvents, 5)


def open_new_profile(app: QApplication, window: MainWindow, root: Path) -> None:
    physiology = Physiology(HeartRateLimits(50, 190), ThresholdHeartRate(170), Sex.MALE)
    request = NewProfileRequest(
        ProfileName.parse("Atleta di prova"),
        root / "profiles" / "Atleta",
        "Europe/Rome",
        physiology,
        date(2026, 1, 1),
    )
    controller = window.controller
    controller.create_profile(request)
    wait_until(
        app, lambda: controller.profile is not None and isinstance(controller.overview_state, Ready)
    )


def measure(
    app: QApplication, window: MainWindow, source: Path, count: int
) -> dict[str, list[float]]:
    controller = window.controller
    gaps: dict[str, list[float]] = {name: [] for name in PHASES}
    phase = [PHASES[0]]
    last = [time.perf_counter()]
    finished = [False]

    def tick() -> None:
        now = time.perf_counter()
        gaps[phase[0]].append(now - last[0])
        last[0] = now

    def on_import_finished(_report: object) -> None:
        phase[0], finished[0] = PHASES[1], True

    def on_overview(state: object) -> None:
        if finished[0] and isinstance(state, Ready):
            phase[0] = PHASES[2]

    controller.import_finished.connect(on_import_finished)
    controller.overview_changed.connect(on_overview)
    timer = QTimer()
    timer.setInterval(TICK_MS)
    timer.timeout.connect(tick)
    timer.start()
    window.import_folder(source)
    wait_until(
        app,
        lambda: (
            finished[0]
            and isinstance(controller.overview_state, Ready)
            and len(controller.overview_state.overview.activities) == count
        ),
    )
    for _ in range(50):  # lascia finire il disegno
        app.processEvents(QEventLoop.ProcessEventsFlag.WaitForMoreEvents, 5)
    timer.stop()
    return gaps


def main(argv: list[str]) -> int:
    root, count = Path(argv[0]), int(argv[1])
    prepare_qt()
    app = QApplication(["sonda"])
    window = compose_main_window(
        parse_command_line([]),
        settings=QSettings(str(root / "settings.ini"), QSettings.Format.IniFormat),
        config_dir=root / "config",
        profiles_dir=root / "profiles",
        system_timezone_id="Europe/Rome",
        clock=lambda _zone: date(2026, 12, 31),
    )
    engine_s: list[float] = []
    chart = window.overview_page.chart
    start_engine = chart.start_engine

    def timed_start_engine() -> None:
        started = time.perf_counter()
        start_engine()
        engine_s.append(time.perf_counter() - started)

    chart.start_engine = timed_start_engine  # type: ignore[method-assign]  # solo la misura
    window.show()
    open_new_profile(app, window, root)
    source = root / "storico"
    source.mkdir()
    for number in range(count):
        (source / f"a{number:05d}.fit").write_bytes(fit_run(number))
    gaps = measure(app, window, source, count)
    gaps["avvio_motore"] = engine_s
    window.close()
    wait_until(app, lambda: window.is_closed)  # il profilo si chiude nel thread dei dati
    print(json.dumps(gaps), flush=True)  # noqa: T201 - l'esito va al test su stdout
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
