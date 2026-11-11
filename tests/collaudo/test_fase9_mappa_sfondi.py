# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Phase 9 acceptance campaign, map backgrounds: T-MAP and T-SEC-14/15 for OpenStreetMap,
OpenTopoMap and CyclOSM, and the tile server whose certificate does not verify.

The real `TileProxy`, `HttpTileSource` and `DiskTileCache` against local servers on loopback; no
real tile server is ever contacted. The TLS server uses a throwaway self-signed certificate that
nothing trusts, so the verification (never relaxed) must refuse it before any tile address is
sent: the server must see a failed handshake and zero HTTP requests.
"""

from __future__ import annotations

import logging
import socketserver
import ssl
import threading
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from ponos_coach.maps.domain import (
    BACKGROUNDS,
    CYCLOSM,
    OPENSTREETMAP,
    OPENTOPOMAP,
    MapBackground,
    TileAddress,
    TileAddressError,
    parse_tile_path,
)
from ponos_coach.maps.ports import TileImage, TileProblem, TileUnavailable
from ponos_coach.maps.use_cases import COOLDOWN_S, TileProxy
from ponos_coach.platform.tile_cache import DiskTileCache
from ponos_coach.platform.tile_http import HttpTileSource, TileEndpoint
from tests.collaudo.test_fase5b_tile import USER_AGENT, Behaviour, Clocks, Server
from tests.fakes.tiles import make_png
from tests.fixtures.generators.tls_material import CERTIFICATE_PEM, PRIVATE_KEY_PEM

HERE = (12, 2200, 1500)
PNG = make_png()


class TlsServer:
    """A loopback TLS server with a certificate that nothing trusts. It counts the handshakes that
    failed, the ones that succeeded and the HTTP requests (their paths) that arrived."""

    def __init__(self, folder: Path) -> None:
        cert, key = folder / "cert.pem", folder / "key.pem"
        cert.write_text(CERTIFICATE_PEM, encoding="ascii")
        key.write_text(PRIVATE_KEY_PEM, encoding="ascii")
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert, key)
        self.failed_handshakes = 0
        self.good_handshakes = 0
        self.requests: list[bytes] = []
        outer = self

        class Handler(socketserver.BaseRequestHandler):
            def handle(self) -> None:
                try:
                    secure = context.wrap_socket(self.request, server_side=True)
                except (ssl.SSLError, OSError):
                    outer.failed_handshakes += 1
                    return
                outer.good_handshakes += 1
                try:
                    outer.requests.append(secure.recv(4096))
                except OSError:
                    return

        self._server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)


@pytest.fixture
def tls(tmp_path: Path) -> Iterator[TlsServer]:
    running = TlsServer(tmp_path)
    yield running
    running.close()


def serve_png(body: bytes) -> Callable[[str], Behaviour]:
    return lambda _path: Behaviour(body=body, headers={"Cache-Control": "max-age=600"})


def https_source(
    background: MapBackground, port: int, clocks: Clocks | None = None
) -> HttpTileSource:
    now = (clocks or Clocks()).utc
    return HttpTileSource(
        background,
        endpoint=TileEndpoint("https", "127.0.0.1", port),
        user_agent=USER_AGENT,
        timeout_s=1.0,
        total_timeout_s=2.0,
        now=lambda: now,
    )


def http_source(background: MapBackground, server: Server) -> HttpTileSource:
    return HttpTileSource(
        background,
        endpoint=TileEndpoint("http", "127.0.0.1", server.port),
        user_agent=USER_AGENT,
        timeout_s=0.4,
        total_timeout_s=0.8,
    )


# --- A certificate that does not verify -------------------------------------------------------


@pytest.mark.parametrize("background", BACKGROUNDS, ids=lambda b: b.key)
def test_t_sec_15_a_certificate_that_does_not_verify_is_an_untrusted_server_and_no_tile_is_asked(
    tls: TlsServer, background: MapBackground
) -> None:
    source = https_source(background, tls.port)

    result = source.fetch(*HERE[:3])

    assert result == TileUnavailable(TileProblem.UNTRUSTED_SERVER)
    assert tls.requests == []  # the address of the tile never left the machine
    assert tls.good_handshakes == 0


def test_t_sec_15_the_proxy_stops_asking_an_untrusted_server_for_300_seconds_and_then_asks_once(
    tls: TlsServer, tmp_path: Path
) -> None:
    clocks = Clocks()
    cache = DiskTileCache(tmp_path / "cache", 8 * 1024 * 1024)
    proxy = TileProxy(
        dict.fromkeys(BACKGROUNDS, https_source(OPENSTREETMAP, tls.port)),
        cache,
        lambda: clocks.utc,
        lambda: clocks.monotonic,
    )
    try:
        answers = [proxy.tile(TileAddress(12, 2200 + i, 1500)) for i in range(25)]

        assert all(a.png is None and a.problem is TileProblem.UNTRUSTED_SERVER for a in answers)
        assert tls.failed_handshakes == 1  # one attempt, then the cool-down
        clocks.advance(COOLDOWN_S[TileProblem.UNTRUSTED_SERVER] - 1)
        proxy.tile(TileAddress(12, 2300, 1500))
        assert tls.failed_handshakes == 1
        clocks.advance(2)
        again = proxy.tile(TileAddress(12, 2301, 1500))
        assert again.problem is TileProblem.UNTRUSTED_SERVER
        assert tls.failed_handshakes == 2
        assert tls.requests == []
    finally:
        cache.close()


def test_t_sec_15_an_untrusted_background_does_not_stop_the_other_two_and_serves_its_old_tiles(
    tls: TlsServer, tmp_path: Path
) -> None:
    clocks = Clocks()
    good_topo, good_cyclo = Server(), Server()
    cache = DiskTileCache(tmp_path / "cache", 8 * 1024 * 1024)
    topo_png = make_png(256, 256, extra_bytes=10)
    good_topo.script = lambda _p: Behaviour(body=topo_png, headers={"Cache-Control": "max-age=60"})
    good_cyclo.script = lambda _p: Behaviour()
    osm_good = Server(lambda _p: Behaviour(headers={"Cache-Control": "max-age=60"}))
    sources = {
        OPENSTREETMAP: https_source(OPENSTREETMAP, tls.port),
        OPENTOPOMAP: http_source(OPENTOPOMAP, good_topo),
        CYCLOSM: http_source(CYCLOSM, good_cyclo),
    }
    proxy = TileProxy(sources, cache, lambda: clocks.utc, lambda: clocks.monotonic)
    try:
        # an old OSM tile, from before the certificate expired, in the cache
        old = HttpTileSource(
            OPENSTREETMAP,
            endpoint=TileEndpoint("http", "127.0.0.1", osm_good.port),
            user_agent=USER_AGENT,
            timeout_s=0.4,
            total_timeout_s=0.8,
        ).fetch(*HERE)
        assert isinstance(old, TileImage)
        cache.put(TileAddress(*HERE, OPENSTREETMAP), old, clocks.utc)
        clocks.advance(3600)

        osm = proxy.tile(TileAddress(*HERE, OPENSTREETMAP))
        topo = proxy.tile(TileAddress(*HERE, OPENTOPOMAP))
        cyclo = proxy.tile(TileAddress(*HERE, CYCLOSM))

        assert (osm.png, osm.problem) == (PNG, TileProblem.UNTRUSTED_SERVER)  # old tile + warning
        assert (topo.png, topo.problem) == (topo_png, None)
        assert (cyclo.png, cyclo.problem) == (PNG, None)
    finally:
        cache.close()
        for each in (good_topo, good_cyclo, osm_good):
            each.close()


def test_t_sec_14_a_plain_text_server_on_an_https_endpoint_is_a_network_problem_not_a_certificate(
    tmp_path: Path,
) -> None:
    plain = Server()
    try:
        result = https_source(OPENSTREETMAP, plain.port).fetch(*HERE)
    finally:
        plain.close()

    assert result == TileUnavailable(TileProblem.NETWORK_UNAVAILABLE)


def test_t_sec_14_the_failed_certificate_leaves_no_address_host_or_port_in_any_log(
    tls: TlsServer, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    source = https_source(OPENSTREETMAP, tls.port)

    source.fetch(*HERE)

    text = "\n".join(f"{r.getMessage()} {r.exc_text or ''}" for r in caplog.records)
    assert "UNTRUSTED" in text.upper() or "untrusted" in text
    for secret in (str(tls.port), "127.0.0.1", "2200", "1500", "certificate verify"):
        assert secret not in text
    assert all(r.exc_info is None for r in caplog.records)


def test_t_sec_15_plain_http_is_refused_for_every_host_that_is_not_loopback() -> None:
    for host in (
        "tile.openstreetmap.org",
        "a.tile.opentopomap.org",
        "192.0.2.1",
        "example.invalid",
    ):
        with pytest.raises(ValueError, match="loopback"):
            TileEndpoint("http", host, 80)


# --- The three backgrounds ----------------------------------------------------------------------


@pytest.fixture
def three() -> Iterator[dict[MapBackground, Server]]:
    servers = {b: Server() for b in BACKGROUNDS}
    yield servers
    for each in servers.values():
        each.close()


def test_t_map_12_each_background_asks_only_its_own_path_and_names_the_app(
    three: dict[MapBackground, Server],
) -> None:
    for background, server in three.items():
        result = http_source(background, server).fetch(*HERE)
        assert isinstance(result, TileImage)

    assert three[OPENSTREETMAP].paths == ["/12/2200/1500.png"]
    assert three[OPENTOPOMAP].paths == ["/12/2200/1500.png"]
    assert three[CYCLOSM].paths == ["/cyclosm/12/2200/1500.png"]
    for server in three.values():
        [headers] = server.request_headers
        assert headers["user-agent"] == USER_AGENT
        assert "cache-control" not in headers and "pragma" not in headers


def test_t_map_12_the_cache_keeps_the_three_backgrounds_apart_for_the_same_tile(
    three: dict[MapBackground, Server], tmp_path: Path
) -> None:
    pngs = {b: make_png(256, 256, extra_bytes=i + 1) for i, b in enumerate(BACKGROUNDS)}
    for background, server in three.items():
        server.script = serve_png(pngs[background])
    clocks = Clocks()
    cache = DiskTileCache(tmp_path / "cache", 8 * 1024 * 1024)
    sources = {b: http_source(b, three[b]) for b in BACKGROUNDS}
    proxy = TileProxy(sources, cache, lambda: clocks.utc, lambda: clocks.monotonic)
    try:
        first = {b: proxy.tile(TileAddress(*HERE, b)).png for b in BACKGROUNDS}
        again = {b: proxy.tile(TileAddress(*HERE, b)).png for b in reversed(BACKGROUNDS)}

        assert first == pngs == again
        assert sum(len(s.paths) for s in three.values()) == 3  # every tile asked once
    finally:
        cache.close()


@pytest.mark.parametrize("background", BACKGROUNDS, ids=lambda b: b.key)
def test_t_map_12_a_zoom_above_the_maximum_of_the_background_is_refused_before_any_request(
    three: dict[MapBackground, Server], background: MapBackground
) -> None:
    source = http_source(background, three[background])

    with pytest.raises(TileAddressError):
        source.fetch(background.max_zoom + 1, 0, 0)
    assert source.fetch(background.max_zoom, 0, 0) is not None  # the maximum itself is fine
    assert len(three[background].paths) == 1


@pytest.mark.parametrize(
    "path",
    [
        "bergfex/12/2200/1500.png",
        "satellite/1/0/0",
        "OpenStreetMap/12/2200/1500",
        "openstreetmap/12/2200/1500.png/../../x",
        "openstreetmap/12/2200/1500.jpg",
        "opentopomap/18/0/0",
        "cyclosm/21/0/0",
        "cyclosm/12/-1/5",
        "cyclosm/12/4096/5",
        "cyclosm/12/١٢/5",
        "https://tile.openstreetmap.org/12/1/1.png",
        "cyclosm/" + "1" * 80,
        "",
    ],
)
def test_t_sec_15_a_path_that_is_not_a_tile_of_the_closed_set_is_rejected(path: str) -> None:
    with pytest.raises(TileAddressError):
        parse_tile_path(path)


@pytest.mark.parametrize("background", [OPENTOPOMAP, CYCLOSM], ids=lambda b: b.key)
def test_t_sec_15_a_redirect_or_a_png_bomb_from_the_new_backgrounds_is_refused(
    three: dict[MapBackground, Server], background: MapBackground
) -> None:
    server = three[background]
    server.script = lambda _p: Behaviour(
        status=302, content_type="text/plain", body=b"", location="http://127.0.0.1:1/x.png"
    )
    redirected = http_source(background, server).fetch(*HERE)
    server.script = lambda _p: Behaviour(body=make_png(8192, 8192))
    bomb = http_source(background, server).fetch(*HERE)

    assert redirected == TileUnavailable(TileProblem.INVALID_RESPONSE)
    assert bomb == TileUnavailable(TileProblem.INVALID_RESPONSE)
