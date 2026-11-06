# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Thread dei dati: ordine dei lavori, esiti, annullamento e chiusura nello stesso thread."""

from __future__ import annotations

import threading

import pytest
from pytestqt.qtbot import QtBot

from ponos_coach.ui.data_thread import (
    DataThread,
    DataThreadClosedError,
    JobCancelled,
    JobCancelledError,
    JobContext,
    JobFailed,
    JobOutcome,
    JobSucceeded,
)

WAIT_MS = 20_000


class Recorder:
    """Raccoglie gli esiti in ordine di arrivo."""

    def __init__(self, thread: DataThread) -> None:
        self.outcomes: dict[int, JobOutcome] = {}
        self.progress: list[tuple[int, int, int]] = []
        thread.completed.connect(self._done)
        thread.progressed.connect(lambda *args: self.progress.append(args))

    def _done(self, job_id: int, outcome: JobOutcome) -> None:
        self.outcomes[job_id] = outcome


def test_jobs_run_in_order_in_one_thread_that_is_not_the_ui_thread(qtbot: QtBot) -> None:
    thread = DataThread(lambda: None)
    recorder = Recorder(thread)
    seen: list[int] = []

    def job(_context: JobContext) -> object:
        seen.append(threading.get_ident())
        return len(seen)

    handles = [thread.submit(job) for _ in range(3)]
    qtbot.waitUntil(lambda: len(recorder.outcomes) == 3, timeout=WAIT_MS)

    assert [recorder.outcomes[h.job_id] for h in handles] == [
        JobSucceeded(1),
        JobSucceeded(2),
        JobSucceeded(3),
    ]
    assert len(set(seen)) == 1
    assert seen[0] != threading.get_ident()
    assert thread.shutdown()


def test_a_failing_job_is_reported_and_the_next_one_still_runs(qtbot: QtBot) -> None:
    thread = DataThread(lambda: None)
    recorder = Recorder(thread)
    boom = ValueError("boom")

    def failing(_context: JobContext) -> object:
        raise boom

    first = thread.submit(failing)
    second = thread.submit(lambda _c: "ok")
    qtbot.waitUntil(lambda: len(recorder.outcomes) == 2, timeout=WAIT_MS)

    assert recorder.outcomes[first.job_id] == JobFailed(boom)
    assert recorder.outcomes[second.job_id] == JobSucceeded("ok")
    assert thread.shutdown()


def test_cancelling_before_the_start_skips_the_job_and_cooperation_stops_a_running_one(
    qtbot: QtBot,
) -> None:
    thread = DataThread(lambda: None)
    recorder = Recorder(thread)
    started = threading.Event()
    release = threading.Event()

    def blocker(context: JobContext) -> object:
        started.set()
        assert release.wait(timeout=20)
        if context.is_cancelled():
            raise JobCancelledError
        return "finished"

    running = thread.submit(blocker)
    queued = thread.submit(lambda _c: "never")
    assert started.wait(timeout=20)
    queued.cancel()
    running.cancel()
    release.set()
    qtbot.waitUntil(lambda: len(recorder.outcomes) == 2, timeout=WAIT_MS)

    assert recorder.outcomes[running.job_id] == JobCancelled()
    assert recorder.outcomes[queued.job_id] == JobCancelled()
    assert thread.shutdown()


def test_progress_always_reports_the_first_and_last_step(qtbot: QtBot) -> None:
    thread = DataThread(lambda: None)
    recorder = Recorder(thread)

    def counting(context: JobContext) -> object:
        for done in range(101):
            context.report(done, 100)
        return None

    handle = thread.submit(counting)
    qtbot.waitUntil(lambda: handle.job_id in recorder.outcomes, timeout=WAIT_MS)
    qtbot.waitUntil(lambda: bool(recorder.progress), timeout=WAIT_MS)

    steps = [done for _id, done, _total in recorder.progress]
    assert steps[0] == 0
    assert steps[-1] == 100
    assert len(steps) < 101  # limitato nel tempo: non uno per passo
    assert thread.shutdown()


def test_shutdown_runs_the_exit_hook_in_the_data_thread(qtbot: QtBot) -> None:
    exit_threads: list[int] = []
    job_threads: list[int] = []
    thread = DataThread(lambda: exit_threads.append(threading.get_ident()))
    recorder = Recorder(thread)
    handle = thread.submit(lambda _c: job_threads.append(threading.get_ident()))
    qtbot.waitUntil(lambda: handle.job_id in recorder.outcomes, timeout=WAIT_MS)

    assert thread.shutdown()

    assert exit_threads == job_threads  # il profilo si chiude dove si è aperto


def test_shutdown_of_a_thread_never_started_is_harmless() -> None:
    assert DataThread(lambda: None).shutdown()


def test_after_a_shutdown_request_no_job_is_accepted_and_the_thread_stops_by_itself(
    qtbot: QtBot,
) -> None:
    exits: list[int] = []
    thread = DataThread(lambda: exits.append(1))
    recorder = Recorder(thread)
    first = thread.submit(lambda _c: "done")
    qtbot.waitUntil(lambda: first.job_id in recorder.outcomes, timeout=WAIT_MS)

    thread.request_shutdown()
    thread.request_shutdown()  # ripetibile

    assert thread.is_closing
    with pytest.raises(DataThreadClosedError):
        thread.submit(lambda _c: "late")
    qtbot.waitUntil(lambda: thread.has_stopped, timeout=WAIT_MS)  # senza attendere nessuno
    assert exits == [1]  # il profilo si chiude una volta sola


def test_a_shutdown_request_cancels_queued_jobs_without_blocking_the_caller(qtbot: QtBot) -> None:
    thread = DataThread(lambda: None)
    recorder = Recorder(thread)
    started = threading.Event()
    release = threading.Event()

    def blocker(context: JobContext) -> object:
        started.set()
        assert release.wait(timeout=20)
        if context.is_cancelled():
            raise JobCancelledError
        return "finished"

    running = thread.submit(blocker)
    queued = thread.submit(lambda _c: "never")
    assert started.wait(timeout=20)

    thread.request_shutdown()  # ritorna subito anche se il lavoro e ancora in corso

    assert not thread.has_stopped
    release.set()
    qtbot.waitUntil(lambda: thread.has_stopped, timeout=WAIT_MS)
    assert recorder.outcomes[running.job_id] == JobCancelled()
    assert recorder.outcomes[queued.job_id] == JobCancelled()


def test_a_thread_never_started_has_stopped_and_refuses_jobs_after_the_request() -> None:
    thread = DataThread(lambda: None)

    thread.request_shutdown()

    assert thread.has_stopped
    with pytest.raises(DataThreadClosedError):
        thread.submit(lambda _c: None)
