# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""T-SYNC-01 su 20.000 attività con SQLite vero e T-UI-02 durante la sincronizzazione.

Test lunghi: solo nella sessione `collaudo`. Misure con distribuzione (p50, p95, massimo) nel
rapporto; le asserzioni sono ampie (il rumore della macchina rende instabile un confronto stretto),
tranne i 100 ms del piano per l'interfaccia, che restano rigidi.
"""

from __future__ import annotations

import statistics
import threading
import time
from collections.abc import Iterator
from itertools import pairwise
from pathlib import Path

import pytest
from PySide6.QtCore import QElapsedTimer, QTimer
from pytestqt.qtbot import QtBot

from ponos_coach.__main__ import extract_original_activity
from ponos_coach.activities.decoding import decode_stored_activity
from ponos_coach.activities.domain import (
    ActivityOrigin,
    ListingKind,
    SessionTokens,
    SyncPolicy,
    SyncStatus,
)
from ponos_coach.activities.sync import SyncMode, SyncPhase, SyncProgress
from ponos_coach.garmin.account import GarminConnectAccount
from ponos_coach.profiles.domain import ProfileId, ProfileName
from ponos_coach.storage.file_archive import FileArchive
from ponos_coach.storage.sqlite_profile import SqliteProfileStorage
from ponos_coach.ui.sync_controller import SyncController
from ponos_coach.ui.sync_model import SyncFinished
from ponos_coach.ui.sync_worker import SyncServices
from tests.builders import fit_identity, make_record
from tests.collaudo.fase2_support import FAST, scripted_client
from tests.collaudo.support import create_profile
from tests.fakes.archive import InMemoryActivityArchive
from tests.fakes.notifier import RecordingNotifier
from tests.fakes.tokens import InMemoryTokenStore
from tests.fixtures.generators.import_files import RUN_SECONDS, run_start
from tests.fixtures.generators.sync_harness import (
    FakeSyncClock,
    MidpointRandom,
    SyncRig,
    fast_decode,
    make_source,
    passthrough_extract,
    remote_id,
    run_file,
)
from tests.garmin.scripted import PLACEHOLDER_TOKENS, ScriptedFactory

pytestmark = pytest.mark.collaudo

HISTORY = 20_000
PAGE_SIZE = 100
UI_EVENT_LIMIT_S = 0.1  # PLAN 7.6: un evento di input si elabora entro 100 ms
LISTING_PAGE_BUDGET_S = 1.0  # una pagina di 100 voci (SQLite + giornale) in meno di 1 s
MATCH_ENTRY_BUDGET_S = 0.25  # un abbinamento con 20.000 attività nell'indice
TOTAL_BUDGET_S = 900.0


def report(line: str) -> None:
    print(line)  # noqa: T201 - il rapporto delle misure


def percentiles(values: list[float]) -> str:
    ordered = sorted(values)
    p95 = ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))]
    return (
        f"n={len(ordered)} p50={statistics.median(ordered) * 1000:.2f}ms "
        f"p95={p95 * 1000:.2f}ms max={ordered[-1] * 1000:.2f}ms"
    )


@pytest.fixture
def rig(tmp_path: Path) -> Iterator[SyncRig]:
    profile = SqliteProfileStorage().create(
        tmp_path,
        ProfileId.parse("00000000-0000-4000-8000-000000000001"),
        ProfileName.parse("Atleta di prova"),
        "UTC",
    )
    clock = FakeSyncClock()
    fake = make_source(HISTORY, clock=clock)
    for number in range(HISTORY):
        fake.set_original(remote_id(number), run_file(number))
    memory = InMemoryActivityArchive()
    try:
        yield SyncRig(
            fake,
            fake,
            profile.activities,
            profile.sync_journal,
            memory,
            memory,
            clock,
            SyncPolicy(page_size=PAGE_SIZE),
            extract=passthrough_extract,
            decode=fast_decode,
        )
    finally:
        profile.close()


def preimport_everything(rig: SyncRig) -> None:
    """20.000 attività già importate da file (cinque secondi di scarto): l'indice è pieno."""
    batch = 500
    for first in range(0, HISTORY, batch):
        with rig.index.transaction():
            for number in range(first, min(first + batch, HISTORY)):
                rig.index.add(
                    make_record(
                        fit_identity(10_000 + number),
                        start=run_start(number),
                        elapsed_s=float(RUN_SECONDS),
                        origin=ActivityOrigin.IMPORT,
                    )
                )


def test_t_sync_01_collaudo_twenty_thousand_activities_listing_and_matching_times(
    rig: SyncRig, capsys: pytest.CaptureFixture[str]
) -> None:
    preimport_everything(rig)
    ticks: list[tuple[SyncPhase, float]] = []

    def on_progress(progress: SyncProgress) -> None:
        ticks.append((progress.phase, time.perf_counter()))

    started = time.perf_counter()
    outcome = rig.run(report=on_progress)
    total_s = time.perf_counter() - started

    pairs = list(pairwise(ticks))
    listing = [b - a for (pa, a), (pb, b) in pairs if pa is pb is SyncPhase.LISTING]
    matching = [b - a for (pa, a), (pb, b) in pairs if pa is pb is SyncPhase.DOWNLOADING]
    with capsys.disabled():
        report(f"\nT-SYNC-01 [{HISTORY} attivita, SQLite vero] totale {total_s:.1f}s")
        report(f"  elenco, per pagina da {PAGE_SIZE}: {percentiles(listing)}")
        report(f"  abbinamento, per voce: {percentiles(matching)}")
    assert outcome.completed and outcome.failures == ()
    assert (outcome.new_entries, outcome.matched, outcome.downloaded) == (HISTORY, HISTORY, 0)
    assert rig.fake.downloads == []  # tutto abbinato: nessun download
    assert len(rig.records()) == HISTORY  # nessun duplicato
    assert max(listing) < LISTING_PAGE_BUDGET_S
    assert max(matching) < MATCH_ENTRY_BUDGET_S
    assert total_s < TOTAL_BUDGET_S


def test_t_sync_01_collaudo_twenty_thousand_incremental_and_full_verification_times(
    rig: SyncRig, capsys: pytest.CaptureFixture[str]
) -> None:
    first_started = time.perf_counter()
    first = rig.run()
    first_s = time.perf_counter() - first_started
    assert first.completed and first.downloaded == HISTORY

    incremental_started = time.perf_counter()
    incremental = rig.run()
    incremental_s = time.perf_counter() - incremental_started
    verify_started = time.perf_counter()
    verify = rig.run(SyncMode.VERIFY)
    verify_s = time.perf_counter() - verify_started

    with capsys.disabled():
        report(f"\nT-SYNC-01 [{HISTORY} attivita, SQLite vero, archivio in memoria]")
        report(f"  prima sincronizzazione completa: {first_s:.1f}s")
        report(
            f"  incrementale senza novita: {incremental_s:.3f}s; verifica completa: {verify_s:.1f}s"
        )
    assert incremental.kind is ListingKind.INCREMENTAL and incremental.downloaded == 0
    assert verify.kind is ListingKind.VERIFY and (verify.new_entries, verify.downloaded) == (0, 0)
    assert rig.journal.counts()[SyncStatus.DOWNLOADED] == HISTORY
    assert incremental_s < 5.0  # una pagina: quasi nulla
    assert verify_s < TOTAL_BUDGET_S


# --- T-UI-02 durante la sincronizzazione ---------------------------------------------------------


def test_t_ui_02_collaudo_the_event_loop_is_never_held_for_100_ms_during_a_sync(
    qtbot: QtBot, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    count = 120
    profile = create_profile(tmp_path)
    profile_id = profile.session.info.profile_id
    tokens = InMemoryTokenStore()
    tokens.save(profile_id, SessionTokens(PLACEHOLDER_TOKENS))
    services = SyncServices(
        account=GarminConnectAccount(ScriptedFactory(scripted_client(count))),
        tokens=tokens,
        extract_original=extract_original_activity,
        decode=decode_stored_activity,
        clock=FakeSyncClock(),
        make_rng=MidpointRandom,
        notifier=RecordingNotifier(),
        policy=FAST,
    )
    controller = SyncController(services, SqliteProfileStorage(), FileArchive.in_profile_folder)
    gaps: list[float] = []
    clock = QElapsedTimer()
    clock.start()
    last = [clock.nsecsElapsed()]

    def tick() -> None:
        now = clock.nsecsElapsed()
        gaps.append((now - last[0]) / 1e9)
        last[0] = now

    timer = QTimer()
    timer.setInterval(10)
    timer.timeout.connect(tick)
    results: list[object] = []
    controller.sync_finished.connect(results.append)
    try:
        timer.start()
        with qtbot.waitSignal(controller.sync_finished, timeout=300_000):
            controller.profile_opened(profile_id, profile.folder)  # controlla e sincronizza
        timer.stop()

        with capsys.disabled():
            report(f"\nT-UI-02 durante la sincronizzazione ({count} FIT veri): scatti da 10 ms")
            report(f"  {percentiles(gaps)}")
        assert isinstance(results[0], SyncFinished) and results[0].outcome.downloaded == count
        assert max(gaps) < UI_EVENT_LIMIT_S
    finally:
        timer.stop()
        controller.begin_shutdown()
        qtbot.waitUntil(lambda: controller.has_stopped, timeout=60_000)
        for thread in threading.enumerate():
            if thread.name.startswith("ponos-garmin-"):
                thread.join(30)
        profile.close()
