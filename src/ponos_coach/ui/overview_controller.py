# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Controller della panoramica: avvia il calcolo in un thread e tiene lo stato di presentazione.

Il calcolo (`compute_fitness_overview`) non gira mai nel thread della UI. Il controller e il
proprietario del thread: lo annulla e lo attende alla chiusura (`shutdown`).
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Sequence
from datetime import date
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Signal, Slot

from ponos_coach.activities.ports import FitFileRef, FitFileSource
from ponos_coach.load.use_cases import FitnessOverview, FitnessParameters, compute_fitness_overview
from ponos_coach.ui.errors import describe_error
from ponos_coach.ui.overview_state import Cancelled, Failed, Idle, Loading, OverviewState, Ready

_LOG = logging.getLogger(__name__)
_PROGRESS_INTERVAL_S = 0.05  # al massimo 20 aggiornamenti al secondo
_SHUTDOWN_WAIT_MS = 10_000

type SourceFactory = Callable[[Path], FitFileSource]


class _CancelledError(Exception):
    """Sollevata dalla sorgente quando l'utente annulla; attraversa il caso d'uso."""


class _NoOutcomeError(Exception):
    """Il thread e terminato senza emettere alcun esito (non dovrebbe accadere)."""


class _ObservedSource:
    """Decoratore di `FitFileSource`: riporta l'avanzamento e interrompe se annullato."""

    def __init__(
        self,
        inner: FitFileSource,
        cancel: threading.Event,
        report: Callable[[int, int], None],
    ) -> None:
        self._inner = inner
        self._cancel = cancel
        self._report = report
        self._total = 0
        self._done = 0
        self._last_report = 0.0

    def list_files(self) -> Sequence[FitFileRef]:
        files = self._inner.list_files()
        self._total = len(files)
        self._report(0, self._total)
        return files

    def read(self, ref: FitFileRef, max_bytes: int) -> bytes:
        if self._cancel.is_set():
            raise _CancelledError
        try:
            return self._inner.read(ref, max_bytes)
        finally:
            self._done += 1
            now = time.monotonic()
            if self._done == self._total or now - self._last_report >= _PROGRESS_INTERVAL_S:
                self._last_report = now
                self._report(self._done, self._total)


class OverviewTask(QThread):
    """Un calcolo della panoramica. Emette un solo esito: `succeeded`, `failed` o `cancelled`."""

    progressed = Signal(int, int)
    succeeded = Signal(object)
    failed = Signal(object)
    cancelled = Signal()

    def __init__(
        self,
        source: FitFileSource,
        parameters: FitnessParameters,
        today: date,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._cancel = threading.Event()
        self._source = _ObservedSource(source, self._cancel, self.progressed.emit)
        self._parameters = parameters
        self._today = today

    def cancel(self) -> None:
        """Chiede di fermarsi al prossimo file (annullamento cooperativo)."""
        self._cancel.set()

    def run(self) -> None:
        """Corpo del thread."""
        try:
            overview = compute_fitness_overview(self._source, self._parameters, self._today)
        except _CancelledError:
            self.cancelled.emit()
        except Exception as error:
            _LOG.exception("Overview calculation failed")
            self.failed.emit(error)
        else:
            if self._cancel.is_set():
                self.cancelled.emit()
            else:
                self.succeeded.emit(overview)


class OverviewController(QObject):
    """Stato della panoramica e avvio del calcolo."""

    state_changed = Signal(object)

    def __init__(
        self,
        source_factory: SourceFactory,
        parameters: FitnessParameters,
        clock: Callable[[], date],
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._source_factory = source_factory
        self._parameters = parameters
        self._clock = clock
        self._state: OverviewState = Idle()
        self._task: OverviewTask | None = None
        self._folder = Path()

    @property
    def state(self) -> OverviewState:
        """Stato corrente."""
        return self._state

    @property
    def is_loading(self) -> bool:
        """Vero mentre un calcolo e in corso."""
        return isinstance(self._state, Loading)

    def load(self, folder: Path) -> bool:
        """Avvia il calcolo per la cartella. Falso (senza effetto) se ne e gia in corso uno."""
        if self._task is not None:
            return False
        task = OverviewTask(
            self._source_factory(folder), self._parameters, self._clock(), parent=self
        )
        # Solo slot di QObject: la consegna segue l'affinita del controller (thread della UI).
        task.progressed.connect(self._on_progress)
        task.succeeded.connect(self._on_succeeded)
        task.failed.connect(self._on_failed)
        task.cancelled.connect(self._on_cancelled)
        task.finished.connect(self._on_task_finished)
        self._folder = folder
        self._task = task
        self._set_state(Loading(folder, 0, 0))
        task.start()
        return True

    def cancel(self) -> None:
        """Annulla il calcolo in corso, se c'e."""
        if self._task is not None:
            self._task.cancel()

    def shutdown(self) -> None:
        """Annulla e attende il thread: da chiamare prima di distruggere il controller."""
        task = self._task
        if task is not None:
            task.cancel()
            if not task.wait(_SHUTDOWN_WAIT_MS):
                _LOG.error("Overview thread did not stop in time")

    @Slot(int, int)
    def _on_progress(self, done: int, total: int) -> None:
        if self.is_loading:
            self._set_state(Loading(self._folder, done, total))

    @Slot(object)
    def _on_succeeded(self, overview: FitnessOverview) -> None:
        self._set_state(Ready(self._folder, overview))

    @Slot(object)
    def _on_failed(self, error: BaseException) -> None:
        self._set_state(Failed(self._folder, describe_error(error)))

    @Slot()
    def _on_cancelled(self) -> None:
        self._set_state(Cancelled(self._folder))

    @Slot()
    def _on_task_finished(self) -> None:
        task, self._task = self._task, None
        if task is not None:
            task.deleteLater()
        if self.is_loading:  # thread finito senza esito: non deve restare "in corso"
            self._set_state(Failed(self._folder, describe_error(_NoOutcomeError())))

    def _set_state(self, state: OverviewState) -> None:
        self._state = state
        self.state_changed.emit(state)
