# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Fake system notification (`SystemNotifier` port of the ui): records the calls."""

from __future__ import annotations

import threading


class RecordingNotifier:
    """Record title and text of each notification. `fail_with` makes it raise the given error, to
    check that a failing notification changes nothing for the user."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.fail_with: Exception | None = None
        self._lock = threading.Lock()

    def notify(self, title: str, message: str) -> None:
        """See `SystemNotifier.notify`."""
        with self._lock:
            self.calls.append((title, message))
        if self.fail_with is not None:
            raise self.fail_with
