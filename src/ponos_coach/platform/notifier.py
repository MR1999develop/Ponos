# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Notifica di sistema su `desktop-notifier` (adattatore della porta `SystemNotifier` della ui).

`notify` non blocca mai il chiamante (spesso il thread della UI) e non solleva mai: la notifica
parte in un thread proprio, con l'API sincrona della libreria; se il sistema non la supporta o
il sistema di notifiche fallisce, resta soltanto un record nel log col tipo dell'errore. Una
sola notifica per volta: se la precedente non è ancora partita la nuova si scarta, perché è
solo un avviso in più rispetto alla barra di stato.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Final, Protocol

_LOG = logging.getLogger(__name__)

_THREAD_NAME: Final = "ponos-notifier"


class SyncSender(Protocol):
    """Ciò che serve di `desktop_notifier.DesktopNotifierSync`: mandare un titolo e un testo."""

    def send(self, title: str, message: str) -> object:
        """Manda la notifica."""
        ...


def _default_sender(app_name: str) -> SyncSender:
    """Il mittente vero. L'import è qui perché su un sistema senza supporto fallisce all'uso,
    non all'avvio dell'app."""
    from desktop_notifier import DesktopNotifierSync  # noqa: PLC0415

    return DesktopNotifierSync(app_name=app_name)


class DesktopNotifier:
    """`SystemNotifier` per Windows e Linux.

    Args:
        app_name: nome dell'app mostrato dalla notifica.
        make_sender: costruisce il mittente nel thread della notifica (finto nei test).
    """

    def __init__(
        self,
        app_name: str,
        make_sender: Callable[[str], SyncSender] = _default_sender,
    ) -> None:
        self._app_name = app_name
        self._make_sender = make_sender
        self._sending = threading.Lock()
        self._sender: SyncSender | None = None  # creato al primo invio e riusato (un loop solo)

    def notify(self, title: str, message: str) -> None:
        """Manda la notifica senza attendere. Mai un errore per chi chiama."""
        if not self._sending.acquire(blocking=False):
            return
        try:
            thread = threading.Thread(
                target=self._send, args=(title, message), name=_THREAD_NAME, daemon=True
            )
            thread.start()
        except (
            RuntimeError
        ):  # nessun thread disponibile: l'avviso si perde, la barra di stato resta
            self._sending.release()
            _LOG.warning("System notification skipped: no thread could be started")

    def _send(self, title: str, message: str) -> None:
        try:
            if self._sender is None:
                self._sender = self._make_sender(self._app_name)
            self._sender.send(title, message)
        except Exception as error:  # confine del thread: il sistema può non supportare le notifiche
            # Solo il tipo: il testo di un errore del sistema può contenere percorsi.
            _LOG.warning("System notification failed (%s)", type(error).__name__)
        finally:
            self._sending.release()
