# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Python's cyclic garbage collector runs only on the UI thread.

The automatic collector runs in whichever thread happens to allocate when its thresholds are
passed. A cycle that holds a Qt object of the UI thread (a widget, a timer, a web page), collected
on a worker thread, destroys that object there, and Qt aborts the process (seen in the data thread
while it built a chart: "Windows fatal exception 0x80000003" during garbage collection). So the
automatic collector is switched off and a timer on the UI thread collects when the counts pass the
same thresholds, the youngest generation that is due first, as the automatic collector would.
Reference counting still frees everything that is not in a cycle at once, on any thread.
"""

from __future__ import annotations

import gc

from PySide6.QtCore import QObject, QTimer

_CHECK_INTERVAL_MS = 500  # often enough that cycles do not pile up, cheap when nothing is due
_GENERATIONS = 3


class MainThreadCollector(QObject):
    """Turns off the automatic collector and collects from a timer of the thread it lives on.

    Create it once, on the UI thread, before any worker thread starts (`main`), with the
    application as parent so that it lives as long as the event loop.
    """

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._thresholds = gc.get_threshold()
        gc.disable()
        self._timer = QTimer(self)
        self._timer.setInterval(_CHECK_INTERVAL_MS)
        self._timer.timeout.connect(self.collect_if_due)
        self._timer.start()

    def collect_if_due(self) -> int:
        """Collect the oldest generation whose count passed its threshold; the number of
        unreachable objects found (0 if nothing was due)."""
        counts = gc.get_count()
        for generation in reversed(range(_GENERATIONS)):
            threshold = self._thresholds[generation] if generation < len(self._thresholds) else 0
            if threshold > 0 and counts[generation] > threshold:
                return gc.collect(generation)
        return 0
