# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Data thread: the only thread that touches a profile's database.

A profile's SQLite connection is used from a single thread. The window owns a
`DataThread` with a queue of jobs run one at a time, in arrival order: open,
create, import, compute the overview, close. The UI receives progress and outcomes via
signals (delivered on its thread) and never waits. Cancellation is cooperative: each
job has its own event, which the job checks between one step and the next.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import ClassVar

from PySide6.QtCore import QObject, QThread, Signal

from ponos_coach.ui.diagnostics import exception_chain

_LOG = logging.getLogger(__name__)
_PROGRESS_INTERVAL_S = 0.05  # at most 20 updates per second
SHUTDOWN_WAIT_MS = 10_000


class JobCancelledError(Exception):
    """Raised by a job that stops because the user cancelled."""


class DataThreadClosedError(Exception):
    """A job was queued after the data thread was asked to close."""


@dataclass(frozen=True, slots=True)
class JobSucceeded:
    """The job finished; `value` is its result."""

    value: object


@dataclass(frozen=True, slots=True)
class JobFailed:
    """The job failed with this error."""

    error: Exception


@dataclass(frozen=True, slots=True)
class JobCancelled:
    """The job was cancelled before or during execution."""


type JobOutcome = JobSucceeded | JobFailed | JobCancelled


class JobContext:
    """What a job can do while it runs: know whether it is cancelled and report progress."""

    def __init__(self, cancel: threading.Event, emit_progress: Callable[[int, int], None]) -> None:
        self._cancel = cancel
        self._emit_progress = emit_progress
        self._last_emit = 0.0

    def is_cancelled(self) -> bool:
        """True if the user asked to stop."""
        return self._cancel.is_set()

    def report(self, done: int, total: int) -> None:
        """Report progress; the first and last steps always go through."""
        now = time.monotonic()
        if done <= 0 or done >= total or now - self._last_emit >= _PROGRESS_INTERVAL_S:
            self._last_emit = now
            self._emit_progress(done, total)


type Job = Callable[[JobContext], object]


class JobHandle:
    """Reference to a queued job: identifier and cancellation."""

    def __init__(self, job_id: int, cancel: threading.Event) -> None:
        self.job_id = job_id
        self._cancel = cancel

    def cancel(self) -> None:
        """Ask to stop (even if the job has not started yet)."""
        self._cancel.set()


@dataclass(frozen=True, slots=True)
class _Queued:
    job_id: int
    job: Job
    cancel: threading.Event


class _Stop:
    """Sentinel: closes the queue."""


class DataThread(QThread):
    """Queue of jobs run in sequence on a single thread.

    `submit`, `request_shutdown` and `shutdown` are called from the UI thread. `on_exit` runs on the
    data thread when the queue ends: the profile is closed there, on the same thread that
    opened it. After `request_shutdown` the thread accepts no more jobs.

    The thread has no Qt parent and, while it runs, the class keeps it alive: destroying a
    running `QThread` aborts the process, for example if the window is destroyed
    while the profile is still closing.
    """

    _running: ClassVar[set[DataThread]] = set()

    progressed = Signal(int, int, int)  # job id, done, total
    completed = Signal(int, object)  # job id, JobOutcome

    def __init__(self, on_exit: Callable[[], None], parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._on_exit = on_exit
        self._queue: queue.Queue[_Queued | _Stop] = queue.Queue()
        self._pending: dict[int, threading.Event] = {}
        self._lock = threading.Lock()
        self._next_id = 1
        self._closing = False

    @property
    def is_closing(self) -> bool:
        """True after `request_shutdown`: no new jobs."""
        return self._closing

    @property
    def has_stopped(self) -> bool:
        """True if the thread is not running (never started or already finished)."""
        return not self.isRunning()

    def submit(self, job: Job) -> JobHandle:
        """Queue a job; the thread starts if it has not started yet.

        Raises:
            DataThreadClosedError: closing has already been requested.
        """
        cancel = threading.Event()
        with self._lock:
            if self._closing:
                raise DataThreadClosedError
            job_id = self._next_id
            self._next_id += 1
            self._pending[job_id] = cancel
        self._queue.put(_Queued(job_id, job, cancel))
        if not self.isRunning():
            self._keep_alive()
            self.start()
        return JobHandle(job_id, cancel)

    def _keep_alive(self) -> None:
        """Keep the thread alive while it runs; drop finished ones (from the UI thread)."""
        DataThread._running = {thread for thread in DataThread._running if thread.isRunning()}
        DataThread._running.add(self)

    @classmethod
    def stop_all(cls, wait_ms: int = SHUTDOWN_WAIT_MS) -> bool:
        """Ask every still-live data thread to close and wait for them (for each one
        at most `wait_ms`). For whoever exits the process or closes a test; true if all
        have finished."""
        threads = list(cls._running)
        for thread in threads:
            thread.request_shutdown()
        return all(thread.wait(wait_ms) for thread in threads)

    def request_shutdown(self) -> None:
        """Cancel the jobs and close the queue, without waiting; can be repeated.

        The thread finishes by itself, closing the profile with `on_exit`. Whoever wants to know
        when
        it has finished looks at `has_stopped`.
        """
        with self._lock:
            if self._closing:
                return
            self._closing = True
            for cancel in self._pending.values():
                cancel.set()
        self._queue.put(_Stop())

    def shutdown(self, wait_ms: int = SHUTDOWN_WAIT_MS) -> bool:
        """Like `request_shutdown`, then wait for the thread at most `wait_ms`.

        The thread is never forcibly terminated: if it does not finish in time it is written to the
        log and the
        caller decides. Returns true if the thread has ended. Blocks the caller: the
        window uses `request_shutdown` and checks without waiting.
        """
        self.request_shutdown()
        if not self.isRunning():
            return True  # never started or already finished: no profile can have been left open
        if self.wait(wait_ms):
            return True
        _LOG.error("Data thread did not stop within %d ms", wait_ms)
        return False

    def run(self) -> None:
        """Thread body: runs the jobs until the sentinel arrives."""
        try:
            while True:
                item = self._queue.get()
                if isinstance(item, _Stop):
                    return
                self._run_one(item)
        finally:
            self._close()

    def _run_one(self, item: _Queued) -> None:
        outcome = self._execute(item)
        with self._lock:
            self._pending.pop(item.job_id, None)
        self.completed.emit(item.job_id, outcome)

    def _execute(self, item: _Queued) -> JobOutcome:
        if item.cancel.is_set():
            return JobCancelled()
        context = JobContext(
            item.cancel, lambda done, total: self.progressed.emit(item.job_id, done, total)
        )
        try:
            value = item.job(context)
        except JobCancelledError:
            return JobCancelled()
        except Exception as error:  # thread boundary: the error goes to the UI, which describes it
            # Only the types: an exception's text may contain paths or user data.
            _LOG.error("Job %d failed: %s", item.job_id, exception_chain(error))
            return JobFailed(error)
        return JobSucceeded(value)

    def _close(self) -> None:
        try:
            self._on_exit()
        except Exception as error:  # closing must not prevent the thread from finishing
            _LOG.error("Closing the profile failed: %s", exception_chain(error))
