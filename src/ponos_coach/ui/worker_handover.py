# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Hand a worker thread's result to a QObject that lives on the UI thread.

A Python worker that held the QObject strongly could drop the last reference to it: the object
would then be destroyed on the worker thread while the UI thread delivers its events, which
crashes Qt (seen on Ubuntu in CI). `Handover` holds the owner weakly, and takes and drops the
strong reference under a lock that `close` also takes: while the handover is open the owner has
not finished closing, so it still holds the object, and the last reference is always dropped on
the UI thread.
"""

from __future__ import annotations

import threading
import weakref
from collections.abc import Callable

from PySide6.QtCore import QObject


class Handover[Owner: QObject]:
    """Delivers results from worker threads to `owner` until `close`.

    `deliver` runs on the worker thread with the owner and must only post (emit a queued
    signal); it must not keep the owner.
    """

    def __init__(self, owner: Owner) -> None:
        self._owner = weakref.ref(owner)
        self._lock = threading.Lock()
        self._closed = False

    def close(self) -> None:
        """UI thread: no delivery starts after this returns, and none is still running."""
        with self._lock:
            self._closed = True

    def post(self, deliver: Callable[[Owner], None]) -> None:
        """Worker thread: `deliver(owner)` if the handover is open and the owner still exists."""
        with self._lock:
            if self._closed:
                return
            owner = self._owner()
            if owner is not None:
                deliver(owner)
            del owner  # under the lock: see the module docstring
