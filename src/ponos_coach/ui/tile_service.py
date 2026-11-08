# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Tiles for the map page, off the UI thread.

The scheme handler asks `TileService.request(path, on_done)` for a tile; the work (`TileProxy`:
cache, then the provider with its timeouts) runs in a small pool and the answer comes back on the
UI thread. The service also keeps the last problem the provider had (no key, key refused, no
network, credit over) so that the map view can show a notice with the remedy; a good tile clears
it. The path of a tile says which area is looked at: it is never logged.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor

from PySide6.QtCore import QObject, Qt, Signal, Slot

from ponos_coach.maps.domain import TileAddressError
from ponos_coach.maps.ports import TileProblem
from ponos_coach.maps.use_cases import TileAnswer, TileProxy

_LOG = logging.getLogger(__name__)
_WORKERS = 4  # the provider's connections are slow, not heavy
MAX_PENDING = 256  # tile requests waiting; the page asks for the visible ones only

type TileDone = Callable[[bytes | None], None]


class TileService(QObject):
    """Serves tile requests of the page with `TileProxy`, one pool, answers on the UI thread.

    `problem_changed` carries the provider problem in force, or `None` when tiles flow again.
    The language is read at each request (it can change while the app runs).
    """

    problem_changed = Signal(object)  # TileProblem | None
    _completed = Signal(int, object)  # request id, TileAnswer | None

    def __init__(
        self,
        proxy: TileProxy,
        language: Callable[[], str],
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._proxy = proxy
        self._language = language
        self._pool: ThreadPoolExecutor | None = ThreadPoolExecutor(
            max_workers=_WORKERS, thread_name_prefix="ponos-tiles"
        )
        self._pending: dict[int, TileDone] = {}
        self._next_id = 1
        self._problem: TileProblem | None = None
        self._completed.connect(self._on_completed, Qt.ConnectionType.QueuedConnection)

    @property
    def problem(self) -> TileProblem | None:
        """The provider problem in force, if any."""
        return self._problem

    def request(self, path: str, on_done: TileDone) -> bool:
        """Ask for the tile at `path` (`/z/x/y`); `on_done(png | None)` runs on the UI thread.

        False (nothing starts) if the service was shut down or too many requests wait.
        """
        pool = self._pool
        if pool is None or len(self._pending) >= MAX_PENDING:
            return False
        request_id = self._next_id
        self._next_id += 1
        self._pending[request_id] = on_done
        language = self._language()

        def work() -> TileAnswer | None:
            return self._serve(path, language)

        future = pool.submit(work)
        future.add_done_callback(lambda done: self._finished(request_id, done))
        return True

    def shutdown(self) -> None:
        """Stop accepting requests; answers still on their way are dropped. Idempotent."""
        pool, self._pool = self._pool, None
        self._pending.clear()
        if pool is not None:
            pool.shutdown(wait=False, cancel_futures=True)

    def _serve(self, path: str, language: str) -> TileAnswer | None:
        try:
            return self._proxy.serve(path, language)
        except TileAddressError:
            return None
        except Exception as error:  # worker boundary: the page just gets "no tile"
            # Only the type: the message could carry an address, which says where the user looks.
            _LOG.error("Tile request failed: %s", type(error).__name__)
            return None

    def _finished(self, request_id: int, future: Future[TileAnswer | None]) -> None:
        """Runs on a worker thread: hand the result to the UI thread through a queued signal."""
        if future.cancelled():
            return
        answer = future.result()
        try:
            self._completed.emit(request_id, answer)
        except RuntimeError:  # the service was destroyed while the request was running
            return

    @Slot(int, object)
    def _on_completed(self, request_id: int, answer: object) -> None:
        on_done = self._pending.pop(request_id, None)
        if on_done is None:
            return
        png: bytes | None = None
        if isinstance(answer, TileAnswer):
            png = answer.png
            self._set_problem(answer.problem)
        on_done(png)

    def _set_problem(self, problem: TileProblem | None) -> None:
        if problem != self._problem:
            self._problem = problem
            self.problem_changed.emit(problem)
