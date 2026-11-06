# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Collaudo indipendente della sincronizzazione (T-SYNC-01..06) sulla pila vera, senza rete.

Profilo, SQLite, archivio e lock veri; l'adattatore Garmin vero sopra un client finto. Qui stanno
le prove che i test dei casi d'uso (fake in memoria) non fanno: sincronizzazione e import in
contemporanea sullo stesso profilo, la stessa attività due volte nell'elenco o cambiata tra
elenco e download, fuso diverso tra due giri, token scaduti dopo un kill vero.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from collections import Counter
from collections.abc import Callable
from datetime import date
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
import requests

from ponos_coach.activities.decoding import decode_stored_activity
from ponos_coach.activities.domain import (
    FailureReason,
    GarminActivityId,
    ListingKind,
    RemoteActivity,
    SyncStatus,
)
from ponos_coach.activities.sync import StopReason, SyncMode
from ponos_coach.activities.use_cases import ImportStatus
from ponos_coach.garmin.source import GarminActivitySource
from ponos_coach.load.use_cases import ComputeProfileOverview, ProfileOverview
from ponos_coach.ui.sync_model import SyncFinished, SyncNotStarted, SyncProblem
from tests.collaudo.fase2_support import (
    FAST,
    Stack,
    adapter_rig,
    build_stack,
    reopen_stack,
    scripted_client,
)
from tests.collaudo.support import TODAY, stored_files, temporaries
from tests.collaudo.test_sync_kill import COUNT, kill_at
from tests.fakes.garmin import FIRST_ACTIVITY_ID, make_remote_history
from tests.fixtures.generators.import_files import RUN_SECONDS, fit_run, gpx_run, run_start
from tests.fixtures.generators.zip_writer import garmin_download
from tests.garmin.scripted import (
    ScriptedClient,
    auth_error,
    entry_for,
    http_error,
    newest_first,
)


def finished(result: object) -> SyncFinished:
    assert isinstance(result, SyncFinished), result
    return result


def remote_run(number: int) -> RemoteActivity:
    return make_remote_history(number + 1)[number]


def assert_coherent(stack: Stack, expected_runs: int) -> None:
    """Indice, giornale e archivio concordano e non c'è nulla di orfano."""
    profile = stack.profile
    records = list(profile.records())
    for record in records:
        profile.archive.verify(record.archived)
    assert len(records) == expected_runs
    assert len({record.start for record in records}) == expected_runs  # nessun duplicato
    referenced = {profile.folder / "archivio" / r.archived.relative_path for r in records}
    assert set(stored_files(profile.folder)) == referenced  # nessun file orfano
    assert temporaries(profile.folder) == []
    garmin_ids = [r.garmin_activity_id for r in records if r.garmin_activity_id is not None]
    assert len(garmin_ids) == len(set(garmin_ids))
    connection = sqlite3.connect(str(profile.folder / "ponos.sqlite3"))
    try:
        assert connection.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
    finally:
        connection.close()


# --- Sincronizzazione e import in contemporanea (SQLite vero, due connessioni) ----------------


@pytest.mark.parametrize("round_number", range(3))
def test_t_sync_05_sync_and_folder_import_at_the_same_time_on_the_same_profile(
    tmp_path: Path, round_number: int
) -> None:
    garmin_runs, import_fit, import_gpx = range(0, 40), range(20, 60), range(10, 20)
    folder = tmp_path / "da-importare"
    folder.mkdir()
    for number in import_fit:
        (folder / f"f{number:03d}.fit").write_bytes(fit_run(number))
    for number in import_gpx:  # le stesse corse di Garmin, ma come GPX: gemelle di altro formato
        (folder / f"g{number:03d}.gpx").write_bytes(gpx_run(number))
    stack = build_stack(
        tmp_path / f"r{round_number}", scripted_client(history=[remote_run(n) for n in garmin_runs])
    )
    results: list[object] = []
    errors: list[BaseException] = []
    start = threading.Barrier(2)

    def sync_in_a_thread() -> None:
        try:
            start.wait(30)
            results.append(stack.sync())
        except BaseException as error:
            errors.append(error)

    thread = threading.Thread(target=sync_in_a_thread, name="ponos-garmin-collaudo")
    try:
        thread.start()
        start.wait(30)
        summary = stack.profile.import_folder(folder)
        thread.join(300)

        assert not thread.is_alive() and errors == []
        outcome = finished(results[0]).outcome
        assert outcome.completed and outcome.failures == ()
        assert summary.count(ImportStatus.FAILED) == 0
        counts = stack.journal_counts()
        assert counts[SyncStatus.PENDING] == counts[SyncStatus.FAILED] == 0
        assert counts[SyncStatus.DOWNLOADED] + counts[SyncStatus.MATCHED] == len(garmin_runs)
        assert_coherent(stack, 60)
        linked = {r.garmin_activity_id for r in stack.profile.records()} - {None}
        assert linked == {FIRST_ACTIVITY_ID + n for n in garmin_runs}

        # un secondo giro di tutti e due non cambia nulla
        again = finished(stack.sync()).outcome
        assert (again.new_entries, again.downloaded, again.matched) == (0, 0, 0)
        second = stack.profile.import_folder(folder)
        assert second.count(ImportStatus.IMPORTED) == 0
        assert_coherent(stack, 60)
    finally:
        thread.join(60)
        stack.close()


def test_t_sync_05_a_late_import_of_a_file_already_downloaded_is_not_a_second_record(
    tmp_path: Path,
) -> None:
    stack = build_stack(tmp_path, scripted_client(5))
    try:
        assert finished(stack.sync()).outcome.downloaded == 5
        folder = tmp_path / "da-importare"
        folder.mkdir()
        for number in range(5):
            (folder / f"f{number}.fit").write_bytes(fit_run(number))
            (folder / f"g{number}.gpx").write_bytes(gpx_run(number))

        summary = stack.profile.import_folder(folder)

        assert summary.count(ImportStatus.IMPORTED) == 0
        assert_coherent(stack, 5)
    finally:
        stack.close()


def test_t_sync_05_files_imported_first_are_matched_not_downloaded_then_a_late_upload_needs_verify(
    tmp_path: Path,
) -> None:
    runs = [remote_run(n) for n in range(205)]
    client = scripted_client(history=runs)
    stack = build_stack(tmp_path, client)
    try:
        folder = tmp_path / "da-importare"
        folder.mkdir()
        for number in range(195, 205):  # le dieci più recenti, già sul disco
            (folder / f"f{number}.fit").write_bytes(fit_run(number))
        stack.profile.import_folder(folder)

        first = finished(stack.sync()).outcome
        assert (first.matched, first.downloaded) == (10, 195)
        assert not set(client.downloaded) & {FIRST_ACTIVITY_ID + n for n in range(195, 205)}

        # un'attivita vecchia caricata in ritardo su Garmin: l'incrementale non la vede
        late_id = FIRST_ACTIVITY_ID + 900
        late = RemoteActivity(
            GarminActivityId(late_id), run_start(-20), float(RUN_SECONDS), "running", True
        )
        client._entries.append(entry_for(late))
        client._downloads[late_id] = garmin_download(fit_run(-20), late_id)
        incremental = finished(stack.sync()).outcome
        assert incremental.kind is ListingKind.INCREMENTAL and incremental.new_entries == 0

        verify = finished(stack.sync(SyncMode.VERIFY)).outcome
        assert (verify.new_entries, verify.downloaded) == (1, 1)
        assert_coherent(stack, 206)
    finally:
        stack.close()


# --- La stessa attività due volte, o cambiata tra elenco e download -----------------------------


def test_t_sync_01_the_same_activity_twice_in_the_listing_is_one_record_and_one_download(
    tmp_path: Path,
) -> None:
    runs = [remote_run(n) for n in range(150)]
    client = scripted_client(history=runs)
    oldest = client._entries[-1]
    client._entries.insert(0, oldest)  # la piu vecchia anche in cima: pagine 1 e 2 la nominano
    stack = build_stack(tmp_path, client)
    try:
        outcome = finished(stack.sync()).outcome

        assert outcome.completed and outcome.failures == ()
        assert Counter(client.downloaded).most_common(1)[0][1] == 1
        assert_coherent(stack, 150)
    finally:
        stack.close()


def shifting_client(tmp_runs: int) -> tuple[ScriptedClient, RemoteActivity]:
    """Un client la cui prima pagina e seguita da una nuova attivita in cima all'elenco."""
    runs = [remote_run(n) for n in range(tmp_runs + 20)]
    client = scripted_client(history=runs[:tmp_runs])
    newest = runs[tmp_runs + 10]
    original_list = client.list_activities
    calls = {"n": 0}

    def shifting(offset: int, limit: int) -> object:
        calls["n"] += 1
        if calls["n"] == 2:  # prima della seconda pagina Garmin riceve un'attivita piu recente
            client._entries.insert(0, entry_for(newest))
            client._total = len(client._entries)
            client._downloads[newest.garmin_id.value] = garmin_download(
                fit_run(tmp_runs + 10), newest.garmin_id.value
            )
        return original_list(offset, limit)

    client.list_activities = shifting  # type: ignore[method-assign]
    return client, newest


def test_d_f2_04_a_new_activity_during_the_first_listing_loses_no_old_one() -> None:
    import tempfile  # noqa: PLC0415

    with tempfile.TemporaryDirectory() as raw:
        client, _newest = shifting_client(200)
        stack = build_stack(Path(raw), client)
        try:
            finished(stack.sync())
            finished(stack.sync())

            assert len(stack.profile.records()) == 201
        finally:
            stack.close()


def test_d_f2_04_the_lost_activity_comes_back_with_a_full_verification(tmp_path: Path) -> None:
    client, _newest = shifting_client(200)
    stack = build_stack(tmp_path, client)
    try:
        finished(stack.sync())
        finished(stack.sync())
        finished(stack.sync(SyncMode.VERIFY))

        assert_coherent(stack, 201)
        assert Counter(client.downloaded).most_common(1)[0][1] == 1
    finally:
        stack.close()


def assert_every_downloaded_id_has_its_activity(
    stack: Stack, runs: list[RemoteActivity], rejected: frozenset[int] = frozenset()
) -> None:
    """Ogni voce `DOWNLOADED` ha un record con lo stesso id e l'inizio dell'elenco; le voci il
    cui file era di un'altra attività (`rejected`) sono fallite per sempre e senza record."""
    by_id = {r.garmin_activity_id: r for r in stack.profile.records()}
    for run in runs:
        entry = stack.profile.session.sync_journal.get(run.garmin_id)
        if run.garmin_id.value in rejected:
            assert entry is not None and entry.status is SyncStatus.FAILED
            assert entry.failure is FailureReason.REMOTE_REJECTED
            assert run.garmin_id.value not in by_id
            continue
        assert entry is not None and entry.status is SyncStatus.DOWNLOADED
        record = by_id.get(run.garmin_id.value)
        assert record is not None, f"id {run.garmin_id.value} segnato scaricato ma senza attivita"
        assert abs((record.start - run.start).total_seconds()) <= 60


@pytest.mark.parametrize(
    "listed, served", [(5, 9), (9, 5)], ids=["older-gets-newer", "newer-gets-older"]
)
def test_d_f2_03_a_download_that_is_another_activity_is_not_accepted_for_the_listed_id(
    tmp_path: Path, listed: int, served: int
) -> None:
    runs = [remote_run(n) for n in range(10)]
    client = scripted_client(history=runs)
    wrong = FIRST_ACTIVITY_ID + listed
    client._downloads[wrong] = garmin_download(fit_run(served), wrong)
    stack = build_stack(tmp_path, client)
    try:
        finished(stack.sync())

        assert_every_downloaded_id_has_its_activity(stack, runs, frozenset({wrong}))
    finally:
        stack.close()


@pytest.mark.parametrize("listed, served", [(5, 9), (9, 5)])
def test_d_f2_03_the_same_case_never_corrupts_the_database_or_the_archive(
    tmp_path: Path, listed: int, served: int
) -> None:
    runs = [remote_run(n) for n in range(10)]
    client = scripted_client(history=runs)
    wrong = FIRST_ACTIVITY_ID + listed
    client._downloads[wrong] = garmin_download(fit_run(served), wrong)
    stack = build_stack(tmp_path, client)
    try:
        outcome = finished(stack.sync()).outcome

        assert outcome.completed
        assert stack.journal_counts()[SyncStatus.PENDING] == 0
        for record in stack.profile.records():
            stack.profile.archive.verify(record.archived)
        assert temporaries(stack.folder) == []
    finally:
        stack.close()


def test_t_sync_the_same_file_under_two_garmin_ids_is_one_record_and_the_second_id_fails(
    tmp_path: Path,
) -> None:
    runs = [remote_run(n) for n in range(4)]
    client = scripted_client(history=runs)
    client._downloads[FIRST_ACTIVITY_ID + 1] = garmin_download(fit_run(0), FIRST_ACTIVITY_ID + 1)
    stack = build_stack(tmp_path, client)
    try:
        outcome = finished(stack.sync()).outcome

        # id 1 riceve il file di un'altra attività (D-F2-03): non coincide con la sua voce
        assert outcome.completed
        assert [(f.garmin_id.value, f.reason) for f in outcome.failures] == [
            (FIRST_ACTIVITY_ID + 1, FailureReason.REMOTE_REJECTED)
        ]
        assert len(stack.profile.records()) == 3
        assert stack.journal_counts()[SyncStatus.PENDING] == 0
    finally:
        stack.close()


# --- T-SYNC-03 con le eccezioni vere di rete e della libreria -----------------------------------


NETWORK_FAILURES = [
    pytest.param(lambda: http_error(500), id="500"),
    pytest.param(lambda: http_error(502), id="502"),
    pytest.param(lambda: http_error(503), id="503"),
    pytest.param(lambda: http_error(504), id="504"),
    pytest.param(lambda: requests.exceptions.ReadTimeout("t"), id="read-timeout"),
    pytest.param(lambda: requests.exceptions.ConnectionError("c"), id="connection"),
    pytest.param(lambda: TimeoutError("t"), id="timeout"),
    pytest.param(lambda: ConnectionResetError("r"), id="reset"),
    pytest.param(lambda: OSError("troncata"), id="oserror"),
]


@pytest.mark.parametrize("make_error", NETWORK_FAILURES)
def test_t_sync_03_a_persistent_network_failure_while_listing_stops_bounded(
    make_error: Callable[[], Exception],
) -> None:
    client = scripted_client(3)
    for _ in range(100):
        client.list_failures.append(make_error())
    rig = adapter_rig(client)

    outcome = rig.run()

    assert outcome.stop is StopReason.SERVICE_UNAVAILABLE
    assert len(client.list_calls) == rig.policy.max_attempts_per_request
    assert rig.wait.total_s <= rig.policy.max_attempts_per_request * rig.policy.backoff_cap_s


@pytest.mark.parametrize("make_error", NETWORK_FAILURES)
def test_t_sync_03_a_persistent_network_failure_while_downloading_stops_after_three_entries(
    make_error: Callable[[], Exception],
) -> None:
    client = scripted_client(10)
    for _ in range(500):
        client.download_failures.append(make_error())
    rig = adapter_rig(client)

    outcome = rig.run()

    assert outcome.stop is StopReason.SERVICE_UNAVAILABLE
    assert len(client.downloaded) <= 3 * rig.policy.max_attempts_per_request
    assert outcome.downloaded == 0


@pytest.mark.parametrize("content", [b"", b"PK\x03\x04", b"PK" * 10, b"\x00" * 1000, b"x" * 10_000])
def test_t_sync_03_truncated_or_malformed_bodies_fail_the_entry_only_and_never_loop(
    content: bytes,
) -> None:
    client = scripted_client(4)
    for entry_id in list(client._downloads):
        client.set_download(entry_id, content)
    rig = adapter_rig(client)

    outcome = rig.run()

    assert outcome.completed and outcome.downloaded == 0
    assert len(outcome.failures) == 4
    assert len(client.downloaded) == 4  # una richiesta per voce: non e transitorio


def test_t_sync_03_an_entry_that_keeps_failing_is_dropped_after_three_syncs(tmp_path: Path) -> None:
    client = scripted_client(2)
    client.set_download(FIRST_ACTIVITY_ID, b"questo non e uno zip")
    stack = build_stack(tmp_path, client)
    try:
        for _ in range(5):
            finished(stack.sync())

        assert stack.journal_counts()[SyncStatus.FAILED] == 1
        assert client.downloaded.count(FIRST_ACTIVITY_ID) <= 3
    finally:
        stack.close()


# --- T-SYNC-04: token --------------------------------------------------------------------------


def test_t_sync_04_a_service_that_is_down_at_resume_does_not_delete_the_tokens(
    tmp_path: Path,
) -> None:
    stack = build_stack(tmp_path, scripted_client(2))
    try:
        for error, problem in (
            (http_error(503), SyncProblem.SERVICE_UNAVAILABLE),
            (requests.exceptions.ConnectionError("x"), SyncProblem.SERVICE_UNAVAILABLE),
            (auth_error(), SyncProblem.AUTH_REQUIRED),
        ):
            stack.factory.resume_error = error
            result = stack.sync()

            assert isinstance(result, SyncNotStarted) and result.problem is problem
        assert stack.tokens.load(stack.profile_id) is not None
        stack.factory.resume_error = None
        assert finished(stack.sync()).outcome.completed
    finally:
        stack.close()


def test_t_sync_04_tokens_that_expire_in_the_middle_stop_the_sync_and_keep_the_work(
    tmp_path: Path,
) -> None:
    client = scripted_client(6)
    original = client.download_original

    def expiring(activity_id: int) -> bytes:
        if len(client.downloaded) == 3:
            client.downloaded.append(activity_id)
            raise auth_error()
        return original(activity_id)

    client.download_original = expiring  # type: ignore[method-assign]
    stack = build_stack(tmp_path, client)
    try:
        outcome = finished(stack.sync()).outcome

        assert outcome.stop is StopReason.AUTHENTICATION_REQUIRED
        assert outcome.downloaded == 3 and outcome.failures == ()
        assert stack.journal_counts()[SyncStatus.PENDING] == 3
        assert_coherent(stack, 3)
        client.download_original = original  # type: ignore[method-assign]
        assert finished(stack.sync()).outcome.downloaded == 3
        assert_coherent(stack, 6)
    finally:
        stack.close()


def test_t_sync_02_a_real_kill_mid_listing_then_expired_tokens_at_restart_then_login(
    tmp_path: Path,
) -> None:
    stack = build_stack(tmp_path, scripted_client(COUNT))
    folder, tokens = stack.folder, stack.tokens
    stack.close()
    kill_at(folder, "cursor:2")  # la seconda pagina e aggiunta ma non confermata
    restarted = reopen_stack(tmp_path, folder, scripted_client(COUNT), tokens=tokens)
    try:
        cursor_before = restarted.profile.session.sync_journal.load_cursor(ListingKind.FULL)
        counts_before = dict(restarted.journal_counts())
        restarted.factory.resume_error = auth_error()  # i token sono scaduti nel frattempo

        result = restarted.sync()

        assert isinstance(result, SyncNotStarted) and result.problem is SyncProblem.AUTH_REQUIRED
        assert restarted.profile.session.sync_journal.load_cursor(ListingKind.FULL) == cursor_before
        assert dict(restarted.journal_counts()) == counts_before
        assert cursor_before is not None and cursor_before.next_offset == 3  # la prima pagina (3)
    finally:
        restarted.close()


def test_t_sync_02_after_the_login_the_resumed_sync_completes_without_repeating_work(
    tmp_path: Path,
) -> None:
    stack = build_stack(tmp_path, scripted_client(COUNT))
    folder, tokens = stack.folder, stack.tokens
    stack.close()
    kill_at(folder, "download:5")
    client = scripted_client(COUNT)
    restarted = reopen_stack(tmp_path, folder, client, tokens=tokens)
    try:
        restarted.factory.resume_error = auth_error()
        assert isinstance(restarted.sync(), SyncNotStarted)
        restarted.factory.resume_error = None
        restarted.link()  # il dialogo di login

        outcome = finished(restarted.sync()).outcome

        assert outcome.completed and outcome.failures == ()
        assert Counter(client.downloaded).most_common(1)[0][1] == 1
        assert len(client.downloaded) == COUNT - 4  # le quattro già confermate non si riscaricano
        assert_coherent(restarted, COUNT)
    finally:
        restarted.close()


# --- Fuso diverso tra due giri -----------------------------------------------------------------


def test_t_sync_the_profile_day_changes_with_the_timezone_but_activities_and_resync_do_not(
    tmp_path: Path,
) -> None:
    client = scripted_client(6)
    stack = build_stack(tmp_path, client)
    try:
        finished(stack.sync())
        session = stack.profile.session

        def overview(zone: str) -> ProfileOverview:
            return ComputeProfileOverview(
                session.activities,
                stack.profile.archive,
                session.physiology,
                session.load_cache,
                decode_stored_activity,
                timezone=ZoneInfo(zone),
                today=lambda: TODAY,
            ).execute()

        rome, honolulu = overview("Europe/Rome"), overview("Pacific/Honolulu")
        again = finished(stack.sync()).outcome

        assert [a.start for a in rome.activities] == [a.start for a in honolulu.activities]
        assert [a.identity for a in rome.activities] == [a.identity for a in honolulu.activities]
        shifted = [
            (r.local_day - h.local_day).days
            for r, h in zip(rome.activities, honolulu.activities, strict=True)
        ]
        assert shifted == [1] * 6  # 07:00 UTC e il giorno prima alle Hawaii
        assert [a.load for a in rome.activities] == [a.load for a in honolulu.activities]
        assert (again.new_entries, again.downloaded) == (0, 0)
        assert len(client.downloaded) == 6
        assert_coherent(stack, 6)
        assert date(2026, 5, 1) <= min(a.local_day for a in rome.activities)
    finally:
        stack.close()


# --- T-SYNC-06: l'annullamento entro 1 s, con le pause vere ---------------------------------------


def test_t_sync_06_cancel_during_the_real_pause_stops_within_a_second_and_keeps_a_coherent_state(
    tmp_path: Path,
) -> None:
    from ponos_coach.activities.domain import DEFAULT_POLICY  # noqa: PLC0415

    client = scripted_client(10)
    stack = build_stack(tmp_path, client, policy=DEFAULT_POLICY)  # pausa vera: 1 s + jitter
    cancel = threading.Event()
    cancelled_at: list[float] = []
    results: list[object] = []
    seen_download = threading.Event()
    original = client.download_original

    def download(activity_id: int) -> bytes:
        content = original(activity_id)
        seen_download.set()
        return content

    client.download_original = download  # type: ignore[method-assign]
    thread = threading.Thread(
        target=lambda: results.append(stack.sync(cancelled=cancel)), name="ponos-garmin-collaudo"
    )
    try:
        thread.start()
        assert seen_download.wait(60)  # il primo download e fatto: ora il giro è nella pausa
        cancelled_at.append(time.monotonic())
        cancel.set()
        thread.join(5)
        stopped_after = time.monotonic() - cancelled_at[0]

        assert not thread.is_alive()
        assert stopped_after < 1.0
        outcome = finished(results[0]).outcome
        assert outcome.cancelled
        counts = stack.journal_counts()
        assert counts[SyncStatus.DOWNLOADED] == len(stack.profile.records())
        assert counts[SyncStatus.DOWNLOADED] + counts[SyncStatus.PENDING] == 10
        assert_coherent(stack, counts[SyncStatus.DOWNLOADED])
    finally:
        cancel.set()
        thread.join(30)
        stack.close()


def test_t_sync_06_a_cancelled_sync_can_be_run_again_to_the_end(tmp_path: Path) -> None:
    client = scripted_client(8)
    stack = build_stack(tmp_path, client)
    try:
        cancel = threading.Event()
        cancel.set()
        assert finished(stack.sync(cancelled=cancel)).outcome.cancelled
        assert client.list_calls == [] and client.downloaded == []

        assert finished(stack.sync()).outcome.downloaded == 8
        assert_coherent(stack, 8)
    finally:
        stack.close()


# --- T-SYNC-01 con l'adattatore vero e FIT veri ------------------------------------------


def test_t_sync_01_a_real_adapter_full_then_incremental_with_real_fit_files(
    tmp_path: Path,
) -> None:
    client = scripted_client(230)
    stack = build_stack(tmp_path, client)
    try:
        first = finished(stack.sync()).outcome
        assert (first.kind, first.downloaded, first.new_entries) == (ListingKind.FULL, 230, 230)
        assert first.status_counts[SyncStatus.DOWNLOADED] == 230
        listed_first = len(client.list_calls)

        for number in range(230, 233):
            late = remote_run(number)
            client._entries.insert(0, entry_for(late))
            client._downloads[late.garmin_id.value] = garmin_download(
                fit_run(number), late.garmin_id.value
            )
        incremental = finished(stack.sync()).outcome

        assert (incremental.kind, incremental.new_entries, incremental.downloaded) == (
            ListingKind.INCREMENTAL,
            3,
            3,
        )
        assert len(client.list_calls) - listed_first == 2  # la seconda pagina e già nota
        assert Counter(client.downloaded).most_common(1)[0][1] == 1
        assert_coherent(stack, 233)
        assert FAST.page_size == 100
    finally:
        stack.close()


def test_t_sync_01_the_adapter_and_the_fake_agree_on_what_a_listing_means() -> None:
    runs = make_remote_history(30)
    page = GarminActivitySource(
        ScriptedClient([entry_for(a) for a in newest_first(runs)])
    ).list_page(0, 100)

    assert [i.garmin_id for i in page.items] == [a.garmin_id for a in newest_first(runs)]
