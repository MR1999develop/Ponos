# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""T-PERF-01 (parziale), T-UI-02 sotto carico vero e T-IMP-02 ai tetti di default.

Test lunghi: solo nella sessione `collaudo`. Sono misure con distribuzione (p50, p95, massimo),
stampate nel rapporto; le asserzioni sono ampie, sui budget del piano con un margine, perche il
rumore della macchina rende instabile un confronto stretto. Dove una soglia e rigida lo dice il
commento.
"""

from __future__ import annotations

import json
import os
import statistics
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from ponos_coach.activities.import_limits import ImportLimits
from ponos_coach.activities.ports import ImportLimit, ImportLimitExceededError
from ponos_coach.activities.use_cases import ImportStatus, import_activities
from ponos_coach.storage.import_scan import FolderImportSource
from tests.activities.import_fakes import FixedClock
from tests.collaudo.support import NOW, RealProfile, create_profile
from tests.fixtures.generators.import_files import fit_run

pytestmark = pytest.mark.collaudo

FILES = 1000
IMPORT_BUDGET_S = 120.0  # PLAN 7.8: import di 1.000 FIT in meno di 2 minuti
OVERVIEW_BUDGET_S = 1.0  # PLAN 7.8: ricalcolo completo del PMC (carichi in cache) in meno di 1 s
UI_EVENT_LIMIT_S = 0.1  # PLAN 7.6: un evento di input si elabora entro 100 ms


def report(line: str) -> None:
    print(line)  # noqa: T201 - il rapporto delle misure e l'esito del test


def percentiles(values: list[float]) -> str:
    ordered = sorted(values)
    p95 = ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))]
    median, worst = statistics.median(ordered), ordered[-1]
    return f"n={len(ordered)} p50={median:.4f}s p95={p95:.4f}s max={worst:.4f}s"


def write_history(folder: Path, count: int) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    for number in range(count):
        # Un'attivita ogni giorno per `count` giorni, sempre la stessa forma: file distinti.
        (folder / f"a{number:05d}.fit").write_bytes(fit_run(number))


def environment() -> str:
    return f"cpu={os.cpu_count()} python=3.13 os={os.name}"


@pytest.fixture
def profile(tmp_path: Path) -> Iterator[RealProfile]:
    opened = create_profile(tmp_path)
    yield opened
    opened.close()


def test_t_perf_01_partial_import_of_1000_fit_and_the_full_overview(
    tmp_path: Path, profile: RealProfile, capsys: pytest.CaptureFixture[str]
) -> None:
    source = tmp_path / "storico"
    write_history(source, FILES)
    per_file: list[float] = []
    last = [time.perf_counter()]

    def tick(done: int, _total: int) -> None:
        now = time.perf_counter()
        if done:
            per_file.append(now - last[0])
        last[0] = now

    started = time.perf_counter()
    summary = import_activities(
        FolderImportSource(source), profile.archive, profile.session.activities,
        FixedClock(NOW), report=tick,
    )  # fmt: skip
    import_s = time.perf_counter() - started

    cold_started = time.perf_counter()
    cold = profile.overview()
    cold_s = time.perf_counter() - cold_started
    warm: list[float] = []
    for _ in range(5):
        warm_started = time.perf_counter()
        profile.overview()
        warm.append(time.perf_counter() - warm_started)

    with capsys.disabled():
        report(f"\nT-PERF-01 [{environment()}]")
        report(f"  import {FILES} FIT: totale {import_s:.2f}s; per file {percentiles(per_file)}")
        report(f"  panoramica a freddo (decodifica di tutti i file): {cold_s:.2f}s")
        report(f"  panoramica a caldo (carichi in cache, PMC compreso): {percentiles(warm)}")
    assert summary.count(ImportStatus.IMPORTED) == FILES
    assert len(cold.activities) == FILES
    assert import_s < IMPORT_BUDGET_S
    assert statistics.median(warm) < OVERVIEW_BUDGET_S


PROBE = Path(__file__).resolve().parents[1] / "fixtures" / "generators" / "collaudo_ui_probe.py"


def measure_event_gaps(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> dict[str, list[float]]:
    """Intervalli tra gli scatti di un timer da 10 ms, per fase, in un processo nuovo che
    importa 300 FIT veri dalla finestra dell'app (vedi `collaudo_ui_probe.py`)."""
    result = subprocess.run(  # noqa: S603 - interprete e script fissi
        [sys.executable, "-I", str(PROBE), str(tmp_path), "300"],
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
        env={**os.environ, "QT_QPA_PLATFORM": "offscreen"},
    )
    assert result.returncode == 0, result.stderr[-2000:]
    by_phase: dict[str, list[float]] = json.loads(result.stdout.strip().splitlines()[-1])
    with capsys.disabled():
        report(f"\nT-UI-02 [{environment()}] intervalli tra scatti del timer da 10 ms")
        for name, values in by_phase.items():
            report(f"  fase {name}: {percentiles(values)}")
    return by_phase


def test_t_ui_02_the_event_loop_is_never_held_for_100_ms_during_import_and_calculation(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Soglia del piano (100 ms per un evento). Le misure stanno attorno a 15-30 ms: il margine
    # e grande, quindi la soglia resta rigida.
    by_phase = measure_event_gaps(tmp_path, capsys)

    assert max(by_phase["importazione"]) < UI_EVENT_LIMIT_S
    assert max(by_phase["panoramica"]) < UI_EVENT_LIMIT_S


def test_d_f1_10_drawing_the_first_chart_does_not_hold_the_event_loop_for_100_ms(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # D-F1-10, corretto: la figura e il suo JSON si costruiscono nel thread dei dati e la
    # tabella guarda un campione di righe per le larghezze (prima 180 ms, ora 25-35 ms). Resta
    # l'avvio del motore web (Chromium, circa 180 ms): non si puo spostare fuori dal thread della
    # UI, quindi la finestra lo fa subito dopo il primo `show()`, quando l'utente non ha ancora
    # un'azione in corso. Il test misura la reattivita durante il primo disegno, non quella
    # durata (che il rapporto stampa come fase `avvio_motore`).
    by_phase = measure_event_gaps(tmp_path, capsys)

    assert max(by_phase["disegno"]) < UI_EVENT_LIMIT_S
    assert max(by_phase["avvio_motore"]) < 1.0  # solo contro una regressione grossolana


def test_t_imp_02_default_cap_of_50000_activity_files_at_the_limit_and_one_over(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Sul disco vero: 50.000 file `.fit` (vuoti, solo i nomi contano all'esame) passano,
    50.001 no, e nulla viene importato."""
    root = tmp_path / "tanti"
    created = time.perf_counter()
    for group in range(50):
        folder = root / f"g{group:02d}"
        folder.mkdir(parents=True)
        for number in range(1000):
            (folder / f"{number:04d}.fit").touch()
    creating_s = time.perf_counter() - created

    scanned = time.perf_counter()
    scan = FolderImportSource(root, ImportLimits()).scan()
    scan_s = time.perf_counter() - scanned

    (root / "g00" / "uno_di_troppo.fit").touch()
    with pytest.raises(ImportLimitExceededError) as raised:
        FolderImportSource(root, ImportLimits()).scan()
    with capsys.disabled():
        report(f"\nT-IMP-02: creazione {creating_s:.1f}s, esame di 50.000 file {scan_s:.2f}s")
    assert len(scan.entries) == 50_000
    assert scan.examined == 50_000 + 50  # file e sottocartelle
    assert raised.value.limit is ImportLimit.ACTIVITY_FILES
    assert raised.value.maximum == 50_000
