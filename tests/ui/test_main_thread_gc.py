# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""The cyclic garbage collector runs on the UI thread only (crash of 2026-11-10: a data thread
collected a cycle holding a Qt object of the UI thread, and Qt aborted the process)."""

from __future__ import annotations

import gc
import threading
import weakref
from collections.abc import Iterator

import pytest
from pytestqt.qtbot import QtBot

from ponos_coach.ui.main_thread_gc import MainThreadCollector
from tests.ui.workspace_support import WAIT_MS


class _Node:
    """A link of a reference cycle that says on which thread it was freed."""

    def __init__(self, freed_on: list[str]) -> None:
        self.other: _Node | None = None
        self._freed_on = freed_on

    def __del__(self) -> None:
        self._freed_on.append(threading.current_thread().name)


@pytest.fixture
def restore_gc() -> Iterator[None]:
    thresholds, enabled = gc.get_threshold(), gc.isenabled()
    yield
    gc.set_threshold(*thresholds)
    if enabled:
        gc.enable()


def make_cycle(freed_on: list[str]) -> weakref.ref[_Node]:
    first, second = _Node(freed_on), _Node(freed_on)
    first.other, second.other = second, first
    return weakref.ref(first)


def test_the_collector_switches_off_the_automatic_one(qtbot: QtBot, restore_gc: None) -> None:
    gc.enable()

    collector = MainThreadCollector()

    assert not gc.isenabled()
    collector.deleteLater()


def test_a_cycle_dropped_by_a_worker_is_freed_on_the_ui_thread(
    qtbot: QtBot, restore_gc: None
) -> None:
    gc.set_threshold(10, 1, 1)  # every generation due often: any collection reaches the cycle
    collector = MainThreadCollector()
    freed_on: list[str] = []
    alive: list[weakref.ref[_Node]] = []

    def worker() -> None:  # allocates a lot, as a data thread does: no collection may run here
        alive.append(make_cycle(freed_on))
        # Live containers: they push the counts past the thresholds.
        kept: list[list[object]] = [[] for _ in range(5000)]
        assert len(kept) == 5000

    thread = threading.Thread(target=worker, name="ponos-test-worker")
    thread.start()
    thread.join(WAIT_MS / 1000)
    assert freed_on == []  # the worker allocated far past the thresholds, nothing was collected

    qtbot.waitUntil(lambda: alive[0]() is None, timeout=WAIT_MS)

    assert freed_on and set(freed_on) == {threading.main_thread().name}
    collector.deleteLater()


def test_nothing_is_collected_until_a_threshold_is_passed(restore_gc: None) -> None:
    gc.collect()
    gc.set_threshold(1_000_000, 10, 10)
    collector = MainThreadCollector()

    assert collector.collect_if_due() == 0
    collector.deleteLater()
