# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""System notification on `desktop-notifier` (adapter of the ui's `SystemNotifier` port).

`notify` never blocks the caller (often the UI thread) and never raises: the notification
starts in a thread of its own, with the library's synchronous API; if the system does not support
it or
the notification system fails, only a record with the error type remains in the log. One
notification at a time: if the previous one has not started yet the new one is discarded, because
it is
only one more warning compared to the status bar.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Final, Protocol

_LOG = logging.getLogger(__name__)

_THREAD_NAME: Final = "ponos-notifier"


class SyncSender(Protocol):
    """What is needed of `desktop_notifier.DesktopNotifierSync`: send a title and a text."""

    def send(self, title: str, message: str) -> object:
        """Sends the notification."""
        ...


def _default_sender(app_name: str) -> SyncSender:
    """The real sender. The import is here because on a system without support it fails on use,
    not at app startup."""
    from desktop_notifier import DesktopNotifierSync  # noqa: PLC0415

    return DesktopNotifierSync(app_name=app_name)


class DesktopNotifier:
    """`SystemNotifier` for Windows and Linux.

    Args:
        app_name: name of the app shown by the notification.
        make_sender: builds the sender in the notification thread (fake in tests).
    """

    def __init__(
        self,
        app_name: str,
        make_sender: Callable[[str], SyncSender] = _default_sender,
    ) -> None:
        self._app_name = app_name
        self._make_sender = make_sender
        self._sending = threading.Lock()
        self._sender: SyncSender | None = None  # created on first send and reused (a single loop)

    def notify(self, title: str, message: str) -> None:
        """Sends the notification without waiting. Never an error for the caller."""
        if not self._sending.acquire(blocking=False):
            return
        try:
            thread = threading.Thread(
                target=self._send, args=(title, message), name=_THREAD_NAME, daemon=True
            )
            thread.start()
        except RuntimeError:  # no thread available: the warning is lost, the status bar remains
            self._sending.release()
            _LOG.warning("System notification skipped: no thread could be started")

    def _send(self, title: str, message: str) -> None:
        try:
            if self._sender is None:
                self._sender = self._make_sender(self._app_name)
            self._sender.send(title, message)
        except Exception as error:  # thread boundary: the system may not support notifications
            # Type only: the text of a system error may contain paths.
            _LOG.warning("System notification failed (%s)", type(error).__name__)
        finally:
            self._sending.release()
