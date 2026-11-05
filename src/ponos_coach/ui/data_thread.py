# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Thread dei dati: l'unico thread che tocca il database di un profilo.

La connessione SQLite di un profilo si usa da un solo thread. La finestra possiede un
`DataThread` con una coda di lavori eseguiti uno alla volta, nell'ordine di arrivo: aprire,
creare, importare, calcolare la panoramica, chiudere. La UI riceve avanzamento ed esiti via
segnali (consegnati nel suo thread) e non attende mai. L'annullamento è cooperativo: ogni
lavoro ha il suo evento, che il lavoro controlla tra un passo e l'altro.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from PySide6.QtCore import QObject, QThread, Signal

_LOG = logging.getLogger(__name__)
_PROGRESS_INTERVAL_S = 0.05  # al massimo 20 aggiornamenti al secondo
SHUTDOWN_WAIT_MS = 10_000


class JobCancelledError(Exception):
    """Sollevata da un lavoro che si ferma perché l'utente ha annullato."""


@dataclass(frozen=True, slots=True)
class JobSucceeded:
    """Il lavoro è finito; `value` è il suo risultato."""

    value: object


@dataclass(frozen=True, slots=True)
class JobFailed:
    """Il lavoro è fallito con questo errore."""

    error: Exception


@dataclass(frozen=True, slots=True)
class JobCancelled:
    """Il lavoro è stato annullato prima o durante l'esecuzione."""


type JobOutcome = JobSucceeded | JobFailed | JobCancelled


class JobContext:
    """Ciò che un lavoro può fare mentre gira: sapere se è annullato e riportare l'avanzamento."""

    def __init__(self, cancel: threading.Event, emit_progress: Callable[[int, int], None]) -> None:
        self._cancel = cancel
        self._emit_progress = emit_progress
        self._last_emit = 0.0

    def is_cancelled(self) -> bool:
        """Vero se l'utente ha chiesto di fermarsi."""
        return self._cancel.is_set()

    def report(self, done: int, total: int) -> None:
        """Riporta l'avanzamento; il primo e l'ultimo passo passano sempre."""
        now = time.monotonic()
        if done <= 0 or done >= total or now - self._last_emit >= _PROGRESS_INTERVAL_S:
            self._last_emit = now
            self._emit_progress(done, total)


type Job = Callable[[JobContext], object]


class JobHandle:
    """Riferimento a un lavoro accodato: identificativo e annullamento."""

    def __init__(self, job_id: int, cancel: threading.Event) -> None:
        self.job_id = job_id
        self._cancel = cancel

    def cancel(self) -> None:
        """Chiede di fermarsi (anche se il lavoro non è ancora partito)."""
        self._cancel.set()


@dataclass(frozen=True, slots=True)
class _Queued:
    job_id: int
    job: Job
    cancel: threading.Event


class _Stop:
    """Sentinella: chiude la coda."""


class DataThread(QThread):
    """Coda di lavori eseguiti in sequenza in un unico thread.

    `submit` e `shutdown` si chiamano dal thread della UI. `on_exit` gira nel thread dei dati
    quando la coda finisce: lì si chiude il profilo, nello stesso thread che lo ha aperto.
    """

    progressed = Signal(int, int, int)  # id del lavoro, fatti, totale
    completed = Signal(int, object)  # id del lavoro, JobOutcome

    def __init__(self, on_exit: Callable[[], None], parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._on_exit = on_exit
        self._queue: queue.Queue[_Queued | _Stop] = queue.Queue()
        self._pending: dict[int, threading.Event] = {}
        self._lock = threading.Lock()
        self._next_id = 1

    def submit(self, job: Job) -> JobHandle:
        """Accoda un lavoro; parte il thread se non è ancora partito."""
        cancel = threading.Event()
        with self._lock:
            job_id = self._next_id
            self._next_id += 1
            self._pending[job_id] = cancel
        self._queue.put(_Queued(job_id, job, cancel))
        if not self.isRunning():
            self.start()
        return JobHandle(job_id, cancel)

    def shutdown(self, wait_ms: int = SHUTDOWN_WAIT_MS) -> bool:
        """Annulla i lavori, chiude la coda e attende il thread, al massimo `wait_ms`.

        Il thread non si termina mai a forza: se non finisce in tempo si scrive nel log e il
        chiamante decide. Restituisce vero se il thread è terminato.
        """
        with self._lock:
            for cancel in self._pending.values():
                cancel.set()
        self._queue.put(_Stop())
        if not self.isRunning():
            return True  # mai avviato o già finito: nessun profilo può essere rimasto aperto
        if self.wait(wait_ms):
            return True
        _LOG.error("Data thread did not stop within %d ms", wait_ms)
        return False

    def run(self) -> None:
        """Corpo del thread: esegue i lavori finché non arriva la sentinella."""
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
        except Exception as error:  # confine del thread: l'errore va alla UI, che lo descrive
            _LOG.error("Job %d failed: %s", item.job_id, type(error).__qualname__, exc_info=True)
            return JobFailed(error)
        return JobSucceeded(value)

    def _close(self) -> None:
        try:
            self._on_exit()
        except Exception:  # la chiusura non deve impedire al thread di finire
            _LOG.error("Closing the profile failed", exc_info=True)
