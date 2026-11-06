# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Notifica di sistema finta (porta `SystemNotifier` della ui): registra le chiamate."""

from __future__ import annotations

import threading


class RecordingNotifier:
    """Registra titolo e testo di ogni notifica. `fail_with` fa sollevare l'errore dato, per
    provare che una notifica che fallisce non cambia nulla per l'utente."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.fail_with: Exception | None = None
        self._lock = threading.Lock()

    def notify(self, title: str, message: str) -> None:
        """Vedi `SystemNotifier.notify`."""
        with self._lock:
            self.calls.append((title, message))
        if self.fail_with is not None:
            raise self.fail_with
