# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""System notification: does not block, does not raise, records only the type of an error."""

from __future__ import annotations

import logging
import threading

import pytest

from ponos_coach.platform.notifier import DesktopNotifier, SyncSender

WAIT_S = 10.0


class RecordingSender:
    """Fake sender: records what it receives and signals when it is done."""

    def __init__(self, *, fail_with: Exception | None = None) -> None:
        self.sent: list[tuple[str, str]] = []
        self.sent_event = threading.Event()
        self.release = threading.Event()
        self.threads: list[str] = []
        self._fail_with = fail_with

    def send(self, title: str, message: str) -> object:
        self.threads.append(threading.current_thread().name)
        if not self.release.wait(WAIT_S):
            raise TimeoutError
        try:
            if self._fail_with is not None:
                raise self._fail_with
            self.sent.append((title, message))
        finally:
            self.sent_event.set()
        return "id"


def notifier_with(sender: RecordingSender) -> tuple[DesktopNotifier, list[str]]:
    names: list[str] = []

    def make(app_name: str) -> SyncSender:
        names.append(app_name)
        return sender

    return DesktopNotifier("Ponos Coach", make), names


def test_notify_returns_at_once_and_sends_from_its_own_thread() -> None:
    sender = RecordingSender()
    notifier, names = notifier_with(sender)

    notifier.notify(
        "Titolo di prova", "5 nuove"
    )  # the sender is stopped: returning immediately proves it does not wait

    assert sender.sent == []
    sender.release.set()
    assert sender.sent_event.wait(WAIT_S)
    assert sender.sent == [("Titolo di prova", "5 nuove")]
    assert sender.threads == ["ponos-notifier"]
    assert names == ["Ponos Coach"]


def test_a_failing_system_is_logged_by_type_and_nobody_sees_an_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    sender = RecordingSender(fail_with=OSError("C:/percorso/segreto"))
    sender.release.set()
    notifier, _ = notifier_with(sender)

    with caplog.at_level(logging.DEBUG):
        notifier.notify("Titolo", "Testo")
        assert sender.sent_event.wait(WAIT_S)
        _wait_until_free(notifier)

    assert "OSError" in caplog.text
    assert "segreto" not in caplog.text


def test_a_system_without_support_fails_when_the_sender_is_built_not_when_notifying(
    caplog: pytest.LogCaptureFixture,
) -> None:
    done = threading.Event()

    def make(_app_name: str) -> SyncSender:
        done.set()
        raise ImportError("nessun backend")

    notifier = DesktopNotifier("Ponos Coach", make)

    with caplog.at_level(logging.DEBUG):
        notifier.notify("Titolo", "Testo")
        assert done.wait(WAIT_S)
        _wait_until_free(notifier)

    assert "ImportError" in caplog.text


def test_the_sender_is_built_once_and_reused() -> None:
    sender = RecordingSender()
    sender.release.set()
    notifier, names = notifier_with(sender)

    for text in ("uno", "due"):
        sender.sent_event.clear()
        notifier.notify("Titolo", text)
        assert sender.sent_event.wait(WAIT_S)
        _wait_until_free(notifier)

    assert [text for _, text in sender.sent] == ["uno", "due"]
    assert names == ["Ponos Coach"]


def test_a_notification_arriving_while_one_is_being_sent_is_dropped() -> None:
    sender = RecordingSender()
    notifier, _ = notifier_with(sender)

    notifier.notify("Titolo", "prima")
    notifier.notify("Titolo", "seconda")  # the first has not started yet: this one is discarded
    sender.release.set()
    assert sender.sent_event.wait(WAIT_S)
    _wait_until_free(notifier)

    assert sender.sent == [("Titolo", "prima")]


def _wait_until_free(notifier: DesktopNotifier) -> None:
    """Wait until the notification thread has released its slot."""
    deadline = threading.Event()
    for _ in range(int(WAIT_S / 0.01)):
        if notifier._sending.acquire(blocking=False):
            notifier._sending.release()
            return
        deadline.wait(0.01)
    raise AssertionError("the notification thread did not finish")
