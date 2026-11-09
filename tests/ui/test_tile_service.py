# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""T-SEC-15 (proxy side) and T-MAP-10: the tile service that answers the page, off the UI thread."""

from __future__ import annotations

import gc
import threading
import time
import weakref
from datetime import UTC, datetime

from pytestqt.qtbot import QtBot

from ponos_coach.maps.domain import TileKey
from ponos_coach.maps.ports import TileImage, TileProblem, TileResult
from ponos_coach.maps.use_cases import TileProxy
from ponos_coach.ui.tile_service import MAX_PENDING, TileService
from tests.fakes.tiles import FakeTileSource, InMemoryTileCache, InMemoryTileKeyStore, make_png
from tests.ui.workspace_support import WAIT_MS


def service_with(
    source_keys: InMemoryTileKeyStore | None = None, language: str = "en"
) -> tuple[TileService, FakeTileSource, InMemoryTileKeyStore, TileProxy]:
    keys = source_keys or InMemoryTileKeyStore()
    source = FakeTileSource(keys)
    proxy = TileProxy(source, InMemoryTileCache(), lambda: datetime.now(UTC), time.monotonic)
    return TileService(proxy, lambda: language), source, keys, proxy


def ask(qtbot: QtBot, service: TileService, path: str) -> list[bytes | None]:
    answers: list[bytes | None] = []
    assert service.request(path, answers.append)
    qtbot.waitUntil(lambda: bool(answers), timeout=WAIT_MS)
    return answers


def test_a_tile_comes_back_on_the_ui_thread_with_the_app_language(qtbot: QtBot) -> None:
    keys = InMemoryTileKeyStore(TileKey(FakeTileSource.ACCEPTED_KEY))
    service, source, _keys, _proxy = service_with(keys, language="it")
    thread_of_answer: list[bool] = []

    def done(png: bytes | None) -> None:
        thread_of_answer.append(threading.current_thread() is threading.main_thread())
        assert png is not None

    service.request("/3/4/5", done)
    qtbot.waitUntil(lambda: bool(thread_of_answer), timeout=WAIT_MS)

    assert thread_of_answer == [True]
    assert source.calls == [(3, 4, 5, "it")]
    service.shutdown()


def test_a_path_that_is_not_a_tile_is_refused_without_asking_the_provider(
    qtbot: QtBot,
) -> None:
    service, source, _keys, _proxy = service_with()

    for path in (
        "/99/0/0",
        "/3/8/0",
        "/3/-1/0",
        "/a/b/c",
        "/3/4",
        "/../../etc/passwd",
        "/3/4/5/6",
        "",
    ):
        assert ask(qtbot, service, path) == [None]

    assert source.calls == []
    service.shutdown()


def test_the_problem_of_the_provider_is_reported_once_and_cleared_by_a_good_tile(
    qtbot: QtBot,
) -> None:
    service, _source, keys, proxy = service_with()  # no key yet
    problems: list[object] = []
    service.problem_changed.connect(problems.append)

    assert ask(qtbot, service, "/2/1/1") == [None]
    assert ask(qtbot, service, "/2/1/2") == [None]
    assert problems == [TileProblem.NO_KEY]  # told once, not once per tile

    keys.save(TileKey(FakeTileSource.ACCEPTED_KEY))
    proxy.key_changed()
    assert ask(qtbot, service, "/2/1/3")[0] is not None

    assert problems == [TileProblem.NO_KEY, None]
    assert service.problem is None
    service.shutdown()


def test_a_service_that_was_shut_down_takes_no_more_requests(qtbot: QtBot) -> None:
    service, source, _keys, _proxy = service_with()
    service.shutdown()
    service.shutdown()  # idempotent

    assert not service.request("/1/0/0", lambda _png: None)
    assert source.calls == []


def test_a_worker_still_busy_after_shutdown_keeps_no_reference_to_the_service(
    qtbot: QtBot,
) -> None:
    # The QObject lives on the UI thread: if a worker dropped the last reference, the object
    # would be destroyed off that thread while the UI thread delivers its events (a crash on
    # Ubuntu in CI). So once shut down and released, the service must be gone at once.
    gate = threading.Event()
    started = threading.Event()
    keys = InMemoryTileKeyStore(TileKey(FakeTileSource.ACCEPTED_KEY))

    class Blocked(FakeTileSource):
        def fetch(self, z: int, x: int, y: int, language: str) -> TileResult:  # noqa: ARG002
            started.set()
            gate.wait(timeout=30)
            return TileImage(make_png(), 3600)

    proxy = TileProxy(Blocked(keys), InMemoryTileCache(), lambda: datetime.now(UTC), time.monotonic)
    service = TileService(proxy, lambda: "en")
    try:
        assert service.request("/5/1/1", lambda _png: None)
        assert started.wait(timeout=WAIT_MS / 1000)
        service.shutdown()
        released = weakref.ref(service)
        del service
        gc.collect()

        assert released() is None
    finally:
        gate.set()


def test_the_requests_that_wait_are_limited(qtbot: QtBot) -> None:
    gate = threading.Event()
    keys = InMemoryTileKeyStore(TileKey(FakeTileSource.ACCEPTED_KEY))

    class Blocked(FakeTileSource):
        def fetch(self, z: int, x: int, y: int, language: str) -> TileResult:  # noqa: ARG002
            gate.wait(timeout=30)
            return TileImage(make_png(), 3600)

    proxy = TileProxy(Blocked(keys), InMemoryTileCache(), lambda: datetime.now(UTC), time.monotonic)
    service = TileService(proxy, lambda: "en")
    try:
        accepted = [service.request("/5/1/1", lambda _png: None) for _ in range(MAX_PENDING + 20)]
    finally:
        gate.set()
        service.shutdown()

    assert accepted.count(True) == MAX_PENDING
    assert accepted.count(False) == 20
