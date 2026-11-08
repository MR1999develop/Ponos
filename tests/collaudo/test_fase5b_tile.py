# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
# ruff: noqa: E501, RUF001, S105, RUF028
"""Phase 5b acceptance campaign, tiles: T-MAP-10, T-MAP-11, T-SEC-14 and T-SEC-15 on the real stack.

The real `TileProxy`, `HttpTileSource` and `DiskTileCache` are wired together against a local HTTP
server (loopback, never the real provider) that plays hostile answers: a refused key, 429, a
timeout, a slow drip, a body that is not a PNG, a huge PNG, a redirect, a lying length. The tile
key is a placeholder; it must never reach a log, a file, an exception or a redirect target.
"""

from __future__ import annotations

import http.server
import logging
import random
import threading
from collections import OrderedDict
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import perf_counter

import pytest

from ponos_coach.maps.domain import (
    TileAddress,
    TileAddressError,
    TileKey,
    parse_cache_limit_gib,
    parse_tile_path,
)
from ponos_coach.maps.ports import TileImage, TileProblem
from ponos_coach.maps.use_cases import COOLDOWN_S, KeyCheck, TileKeySettings, TileProxy
from ponos_coach.platform.tile_cache import CACHE_FILE_NAME, DiskTileCache, accounted_bytes
from ponos_coach.platform.tile_http import HttpTileSource, TileEndpoint
from tests.fakes.tiles import InMemoryTileKeyStore, make_png

SECRET = "SENTINEL-key-0123456789abcdef"
NOW = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)
PNG = make_png()
HERE = TileAddress(12, 2200, 1500)


@dataclass
class Behaviour:
    """How the test server answers one request."""

    status: int = 200
    content_type: str | None = "image/png"
    body: bytes = PNG
    declared_length: int | None = None  # None: the real one; -1: no Content-Length (chunks)
    location: str | None = None
    hold_s: float = 0.0  # wait before answering (a timeout test)
    drip_s: float = 0.0  # wait between 1 KiB pieces of the body (a slow drip)
    headers: dict[str, str] = field(default_factory=dict)


class Server:
    """A loopback HTTP server; `script(path)` chooses the behaviour of each request."""

    def __init__(self, script: Callable[[str], Behaviour] | None = None) -> None:
        self.paths: list[str] = []
        self.script = script or (lambda _path: Behaviour())
        self.stop = threading.Event()
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_GET(self) -> None:
                outer.paths.append(self.path)
                behaviour = outer.script(self.path)
                if behaviour.hold_s:
                    outer.stop.wait(behaviour.hold_s)
                try:
                    self._answer(behaviour)
                except OSError:  # the client gave up
                    return

            def _answer(self, b: Behaviour) -> None:
                self.send_response(b.status)
                if b.content_type is not None:
                    self.send_header("Content-Type", b.content_type)
                if b.location is not None:
                    self.send_header("Location", b.location)
                for name, value in b.headers.items():
                    self.send_header(name, value)
                if b.declared_length != -1:
                    length = len(b.body) if b.declared_length is None else b.declared_length
                    self.send_header("Content-Length", str(length))
                self.send_header("Connection", "close")
                self.end_headers()
                for start in range(0, len(b.body), 1024):
                    if b.drip_s and outer.stop.wait(b.drip_s):
                        return
                    self.wfile.write(b.body[start : start + 1024])
                    self.wfile.flush()
                self.close_connection = True

            def log_message(self, *_args: object) -> None:
                return

        self._server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def close(self) -> None:
        self.stop.set()
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)


@pytest.fixture
def server() -> Iterator[Server]:
    running = Server()
    yield running
    running.close()


def always(behaviour: Behaviour) -> Callable[[str], Behaviour]:
    """A server script that answers every request the same way."""
    return lambda _path: behaviour


class Clocks:
    def __init__(self) -> None:
        self.utc = NOW
        self.monotonic = 5000.0

    def advance(self, seconds: float) -> None:
        self.utc += timedelta(seconds=seconds)
        self.monotonic += seconds


class Stack:
    """Proxy + HTTP source + disk cache + key store, on a local server."""

    def __init__(
        self, server: Server, folder: Path, *, limit: int = 8 * 1024 * 1024, key: str = SECRET
    ) -> None:
        self.clocks = Clocks()
        self.keys = InMemoryTileKeyStore(TileKey(key))
        self.source = HttpTileSource(
            self.keys,
            TileEndpoint("http", "127.0.0.1", server.port),
            timeout_s=0.4,
            total_timeout_s=0.8,
            now=lambda: self.clocks.utc,
        )
        self.cache = DiskTileCache(folder / "cache", limit)
        self.proxy = TileProxy(
            self.source, self.cache, lambda: self.clocks.utc, lambda: self.clocks.monotonic
        )
        self.settings = TileKeySettings(self.keys, self.source, self.proxy)


@pytest.fixture
def stack(server: Server, tmp_path: Path) -> Iterator[Stack]:
    built = Stack(server, tmp_path)
    yield built
    built.cache.close()


# --- T-MAP-10: the hostile answers of the provider ----------------------------------------------


def test_t_map_10_a_refused_key_gives_the_neutral_background_and_no_retry_storm(
    server: Server, stack: Stack
) -> None:
    server.script = lambda _path: Behaviour(status=403, content_type="text/plain", body=b"no")

    answers = [stack.proxy.tile(TileAddress(12, 2200 + i, 1500), "it") for i in range(20)]

    assert all(a.png is None and a.problem is TileProblem.KEY_REJECTED for a in answers)
    assert len(server.paths) == 1  # one attempt, then the cool-down protects the provider
    stack.clocks.advance(COOLDOWN_S[TileProblem.KEY_REJECTED] + 1)
    stack.proxy.tile(HERE, "it")
    assert len(server.paths) == 2  # and it asks again after the cool-down, once


def test_t_map_10_credit_over_serves_the_expired_tile_with_the_warning(
    server: Server, stack: Stack
) -> None:
    server.script = lambda _path: Behaviour(headers={"Cache-Control": "max-age=60"})
    assert stack.proxy.tile(HERE, "en").png == PNG
    stack.clocks.advance(120)
    server.script = lambda _path: Behaviour(status=429, content_type="text/plain", body=b"slow")

    answer = stack.proxy.tile(HERE, "en")

    assert answer.png == PNG
    assert answer.problem is TileProblem.CREDIT_EXHAUSTED


def test_t_map_10_a_silent_server_times_out_and_the_call_returns(
    server: Server, stack: Stack
) -> None:
    server.script = lambda _path: Behaviour(hold_s=5.0)

    started = perf_counter()
    answer = stack.proxy.tile(HERE, "it")

    assert perf_counter() - started < 3.0
    assert answer.problem is TileProblem.NETWORK_UNAVAILABLE


def test_t_sec_15_a_slow_drip_hits_the_total_deadline(server: Server, stack: Stack) -> None:
    body = make_png(extra_bytes=200_000)  # 200 pieces of 1 KiB, 50 ms apart: ~10 s
    server.script = lambda _path: Behaviour(body=body, drip_s=0.05)

    started = perf_counter()
    answer = stack.proxy.tile(HERE, "it")

    assert perf_counter() - started < 3.0
    assert answer.problem is TileProblem.NETWORK_UNAVAILABLE
    assert stack.cache.used_bytes() == 0


@pytest.mark.parametrize(
    "behaviour",
    [
        Behaviour(content_type="text/html", body=b"<html>hi</html>"),
        Behaviour(content_type="image/png", body=b"<html>not a png</html>"),
        Behaviour(content_type=None),
        Behaviour(content_type="image/jpeg", body=PNG),
        Behaviour(body=make_png(4096, 4096)),  # a header that promises 16 Mpixels
        Behaviour(body=PNG[:-12]),  # truncated: no end marker
        Behaviour(body=PNG, declared_length=len(PNG) - 40),  # Content-Length shorter than the data
        Behaviour(status=204, body=b""),
        Behaviour(status=304, body=b""),
        Behaviour(status=404, content_type="text/plain", body=b"gone"),
        Behaviour(status=200, body=b""),
    ],
    ids=[
        "html",
        "html-as-png",
        "no-content-type",
        "jpeg-type",
        "huge-pixels",
        "truncated",
        "short-length",
        "204",
        "304",
        "404",
        "empty",
    ],  # fmt: skip
)
def test_t_sec_15_a_wrong_answer_is_not_stored_and_not_served(
    server: Server, stack: Stack, behaviour: Behaviour
) -> None:
    server.script = lambda _path: behaviour

    answer = stack.proxy.tile(HERE, "it")

    assert answer.png is None
    assert answer.problem is not None
    assert stack.cache.used_bytes() == 0


def test_t_sec_15_a_png_far_above_the_byte_cap_is_refused_in_both_framings(
    server: Server, stack: Stack
) -> None:
    huge = make_png(extra_bytes=6 * 1024 * 1024)
    server.script = lambda _path: Behaviour(body=huge)  # honest Content-Length
    assert stack.proxy.tile(HERE, "it").problem is TileProblem.INVALID_RESPONSE

    stack.clocks.advance(COOLDOWN_S[TileProblem.INVALID_RESPONSE] + 1)
    server.script = lambda _path: Behaviour(body=huge, declared_length=-1)  # no length at all
    assert stack.proxy.tile(HERE, "it").problem is TileProblem.INVALID_RESPONSE

    stack.clocks.advance(COOLDOWN_S[TileProblem.INVALID_RESPONSE] + 1)
    server.script = lambda _path: Behaviour(body=huge, declared_length=len(PNG))  # a lying length
    assert stack.proxy.tile(HERE, "it").png is None
    assert stack.cache.used_bytes() == 0


def test_t_sec_15_a_redirect_is_never_followed_and_the_key_does_not_travel(
    server: Server, stack: Stack
) -> None:
    elsewhere = Server()
    try:
        target = f"http://127.0.0.1:{elsewhere.port}/steal"
        for code in (301, 302, 303, 307, 308):
            stack.clocks.advance(COOLDOWN_S[TileProblem.INVALID_RESPONSE] + 1)
            server.script = always(
                Behaviour(status=code, content_type="text/plain", body=b"", location=target)
            )
            answer = stack.proxy.tile(HERE, "it")
            assert answer.problem is TileProblem.INVALID_RESPONSE
        assert elsewhere.paths == []
    finally:
        elsewhere.close()


def test_t_map_10_a_dead_port_is_a_network_problem_not_a_crash(tmp_path: Path) -> None:
    closed = Server()
    port = closed.port
    closed.close()
    keys = InMemoryTileKeyStore(TileKey(SECRET))
    source = HttpTileSource(keys, TileEndpoint("http", "127.0.0.1", port), timeout_s=0.3)

    result = source.fetch(0, 0, 0, "en")

    assert getattr(result, "problem", None) is TileProblem.NETWORK_UNAVAILABLE


def test_t_map_10_a_single_missing_tile_does_not_black_out_the_others(
    server: Server, stack: Stack
) -> None:
    """D-F5B-02 (low): the cool-down is global, so one 404 (a tile the provider does not have)
    silences the tiles around it for 15 seconds although they exist."""
    server.script = lambda path: (
        Behaviour(status=404, content_type="text/plain", body=b"none")
        if "/2200/" in path
        else Behaviour()
    )
    stack.proxy.tile(TileAddress(12, 2200, 1500), "it")  # the missing one

    neighbour = stack.proxy.tile(TileAddress(12, 2201, 1500), "it")

    assert neighbour.png == PNG


def test_t_map_11_only_the_asked_tiles_are_fetched_and_a_fresh_tile_is_not_asked_twice(
    server: Server, stack: Stack
) -> None:
    server.script = lambda _path: Behaviour(headers={"Cache-Control": "max-age=3600"})
    wanted = [TileAddress(10, 500 + i, 400) for i in range(5)]

    for address in wanted * 3:
        stack.proxy.tile(address, "en")

    assert len(server.paths) == 5  # no prefetch of neighbours, no repeat while fresh
    stack.clocks.advance(3601)
    stack.proxy.tile(wanted[0], "en")
    assert len(server.paths) == 6  # an expired tile is renewed


def test_t_sec_14_a_new_key_applies_at_once_and_a_refused_one_is_rolled_back(
    server: Server, stack: Stack
) -> None:
    good = "second-key-0123456789"
    server.script = lambda path: (
        Behaviour() if f"key={SECRET}" in path else Behaviour(status=401, content_type="text/plain")
    )

    assert stack.settings.set_key(good) is KeyCheck.REJECTED
    assert stack.keys.key == TileKey(SECRET)  # the previous key is back
    assert stack.settings.set_key("short") is KeyCheck.INVALID_FORMAT
    assert stack.settings.set_key("bad key with spaces!!") is KeyCheck.INVALID_FORMAT
    assert stack.keys.key == TileKey(SECRET)
    assert stack.settings.set_key(SECRET) is KeyCheck.ACCEPTED


# --- T-SEC-14: the key never leaves the request ----------------------------------------------------


def all_bytes_under(folder: Path) -> bytes:
    return b"".join(p.read_bytes() for p in folder.rglob("*") if p.is_file())


def test_t_sec_14_the_key_is_in_no_log_no_file_no_error_and_no_answer(
    server: Server, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    stack = Stack(server, tmp_path)
    outcomes: list[object] = []
    scripts = [
        Behaviour(),
        Behaviour(status=403, content_type="text/plain"),
        Behaviour(status=429, content_type="text/plain"),
        Behaviour(status=302, location="http://127.0.0.1:9/x", body=b""),
        Behaviour(content_type="text/html", body=b"x"),
        Behaviour(status=500, content_type="text/plain"),
    ]
    for i, behaviour in enumerate(scripts):
        server.script = always(behaviour)
        stack.clocks.advance(400)
        outcomes.append(stack.proxy.tile(TileAddress(12, 2200 + i, 1500), "it"))
        outcomes.append(stack.source.fetch(12, 2200 + i, 1500, "it"))
    stack.cache.close()

    assert SECRET not in caplog.text
    for record in caplog.records:
        assert SECRET not in record.getMessage()
        assert SECRET not in repr(record.args) + repr(record.exc_info)
    assert all(SECRET not in repr(o) for o in outcomes)
    assert SECRET.encode() not in all_bytes_under(tmp_path)
    assert SECRET not in repr(TileKey(SECRET)) + str(TileKey(SECRET)) + f"{TileKey(SECRET)!s:>40}"
    with pytest.raises(TypeError):
        import pickle  # noqa: PLC0415

        pickle.dumps(TileKey(SECRET))
    assert server.paths  # the key did go to the (local) provider, in the query, and only there


@pytest.mark.parametrize(
    "bad", ["a b c d e f g h", "key\r\nHost: evil", "short", "é" * 10, "x" * 129]
)
def test_t_sec_14_a_key_that_is_not_url_safe_is_refused_without_repeating_it(bad: str) -> None:
    with pytest.raises(ValueError, match="invalid tile key") as caught:
        TileKey(bad)

    assert bad not in str(caught.value)


# --- T-SEC-15: addresses and sizes from the page ---------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "../0/0/0", "0/0/0/0", "0/0", "-1/0/0", "0/-1/0", "20/0/0", "3/8/0", "3/0/8", "a/b/c",
        "0/0/0.png/", "0/0/0.png?x=1", "0/0/0%2e", "0/0/0.PNG", " 0/0/0", "0/0/0 ", "0/0/0\n",
        "0//0", "//0/0/0", "0x1/0/0", "1e1/0/0", "0/0/0" + "9" * 40, "",
    ],
)  # fmt: skip
def test_t_sec_15_hostile_tile_paths_are_rejected_before_any_request(
    server: Server, stack: Stack, path: str
) -> None:
    with pytest.raises(TileAddressError):
        stack.proxy.serve(path, "it")

    assert server.paths == []


def test_t_sec_15_tile_paths_accept_only_ascii_digits() -> None:
    """D-F5B-01 (low): `\\d` matches Unicode digits, so the Arabic-Indic digits are read as 12/0/0
    (and the cache size accepts them too); the contract says "digits only"."""
    with pytest.raises(TileAddressError):
        parse_tile_path("١٢/٠/٠")
    with pytest.raises(ValueError, match="not a size"):
        parse_cache_limit_gib("٤")


# --- T-MAP-11: the exact cap of the cache ---------------------------------------------------------


def disk_use(folder: Path) -> int:
    return sum(p.stat().st_size for p in folder.rglob("*") if p.is_file())


def test_t_map_11_the_cap_is_exact_least_recently_used_goes_first(tmp_path: Path) -> None:
    png = make_png(extra_bytes=20_000)
    cost = accounted_bytes(len(png))
    cache = DiskTileCache(tmp_path / "c", cost * 5)
    for i in range(5):
        cache.put("en", TileAddress(8, i, 0), TileImage(png, 3600), NOW)
    assert cache.used_bytes() == cost * 5  # exactly full, nothing evicted
    cache.get("en", TileAddress(8, 0, 0), NOW)  # touch the oldest: it is now the youngest

    cache.put("en", TileAddress(8, 99, 0), TileImage(png, 3600), NOW)

    assert cache.used_bytes() == cost * 5
    assert cache.get("en", TileAddress(8, 1, 0), NOW) is None  # the least recently used went
    assert cache.get("en", TileAddress(8, 0, 0), NOW) is not None
    assert cache.get("en", TileAddress(8, 99, 0), NOW) is not None
    cache.close()


def test_t_map_11_random_use_matches_a_naive_lru_model_and_never_exceeds_the_cap(
    tmp_path: Path,
) -> None:
    rng = random.Random(7)
    limit = 600_000
    cache = DiskTileCache(tmp_path / "c", limit)
    model: OrderedDict[tuple[str, int, int, int], int] = OrderedDict()
    for _ in range(600):
        address = TileAddress(9, rng.randrange(40), rng.randrange(3))
        style = rng.choice(["it", "en"])
        key = (style, address.z, address.x, address.y)
        if rng.random() < 0.6:
            png = make_png(extra_bytes=rng.choice([100, 5_000, 30_000, 90_000]))
            cache.put(style, address, TileImage(png, 3600), NOW)
            model.pop(key, None)
            model[key] = accounted_bytes(len(png))
            while sum(model.values()) > limit:
                model.popitem(last=False)
        else:
            hit = cache.get(style, address, NOW)
            assert (hit is not None) == (key in model)
            if key in model:
                model.move_to_end(key)
        assert cache.used_bytes() == sum(model.values()) <= limit
    cache.close()


def test_t_map_11_the_files_on_disk_stay_near_the_cap(tmp_path: Path) -> None:
    """The accounted cap is exact; the folder (database, WAL, shm) may add a bounded overhead."""
    limit = 4 * 1024 * 1024
    cache = DiskTileCache(tmp_path / "c", limit)
    for i in range(300):
        cache.put("en", TileAddress(10, i, 7), TileImage(make_png(extra_bytes=60_000), 3600), NOW)
    cache.close()  # checkpoint included

    assert (tmp_path / "c" / CACHE_FILE_NAME).stat().st_size <= limit + 128 * 1024
    assert disk_use(tmp_path / "c") <= limit + 128 * 1024


def test_t_map_11_disk_use_while_open_stays_within_the_cap_plus_a_few_kib(tmp_path: Path) -> None:
    """D-F5B-03: the WAL is truncated after every write, so the folder stays near the cap."""
    limit = 4 * 1024 * 1024
    cache = DiskTileCache(tmp_path / "c", limit)
    worst = 0
    for i in range(300):
        cache.put("en", TileAddress(10, i, 7), TileImage(make_png(extra_bytes=60_000), 3600), NOW)
        worst = max(worst, disk_use(tmp_path / "c"))

    cache.close()
    assert worst <= limit + 256 * 1024


def test_t_map_11_clear_returns_the_disk_to_almost_nothing_and_the_cache_keeps_working(
    tmp_path: Path,
) -> None:
    cache = DiskTileCache(tmp_path / "c", 2 * 1024 * 1024)
    for i in range(40):
        cache.put("it", TileAddress(10, i, 0), TileImage(make_png(extra_bytes=30_000), 60), NOW)

    cache.clear()

    assert cache.used_bytes() == 0
    assert disk_use(tmp_path / "c") < 64 * 1024
    cache.put("it", TileAddress(10, 1, 1), TileImage(PNG, 60), NOW)
    assert cache.get("it", TileAddress(10, 1, 1), NOW) is not None
    cache.close()


def test_t_map_11_a_lower_cap_evicts_at_once_and_on_reopen(tmp_path: Path) -> None:
    folder = tmp_path / "c"
    cache = DiskTileCache(folder, 3 * 1024 * 1024)
    for i in range(50):
        cache.put("it", TileAddress(10, i, 0), TileImage(make_png(extra_bytes=40_000), 3600), NOW)
    cache.set_limit(1024 * 1024)
    assert cache.used_bytes() <= 1024 * 1024
    cache.close()

    reopened = DiskTileCache(folder, 256 * 1024)

    assert reopened.used_bytes() <= 256 * 1024
    reopened.close()


def test_t_map_11_a_tile_bigger_than_the_cap_and_a_zero_ttl_are_not_stored(tmp_path: Path) -> None:
    cache = DiskTileCache(tmp_path / "c", 8192)
    cache.put("it", TileAddress(5, 1, 1), TileImage(make_png(extra_bytes=50_000), 60), NOW)
    cache.put("it", TileAddress(5, 1, 2), TileImage(PNG[:100] + PNG[100:], 0), NOW)

    assert cache.used_bytes() == 0
    cache.close()


def test_t_map_11_a_damaged_cache_file_is_rebuilt_empty_not_fatal(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    folder = tmp_path / "c"
    DiskTileCache(folder, 1024 * 1024).close()
    (folder / CACHE_FILE_NAME).write_bytes(b"this is not a database" * 500)

    cache = DiskTileCache(folder, 1024 * 1024)

    assert cache.used_bytes() == 0
    cache.put("it", TileAddress(1, 0, 0), TileImage(PNG, 60), NOW)
    assert cache.get("it", TileAddress(1, 0, 0), NOW) is not None
    cache.close()


def test_t_map_11_concurrent_use_never_breaks_the_cap(tmp_path: Path) -> None:
    limit = 500_000
    cache = DiskTileCache(tmp_path / "c", limit)
    errors: list[BaseException] = []

    def hammer(seed: int) -> None:
        rng = random.Random(seed)
        try:
            for _ in range(150):
                address = TileAddress(9, rng.randrange(30), seed)
                if rng.random() < 0.5:
                    cache.put("en", address, TileImage(make_png(extra_bytes=20_000), 600), NOW)
                else:
                    cache.get("en", address, NOW)
                assert cache.used_bytes() <= limit
        except BaseException as error:
            errors.append(error)

    threads = [threading.Thread(target=hammer, args=(n,)) for n in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)

    assert errors == []
    assert cache.used_bytes() <= limit
    cache.close()
