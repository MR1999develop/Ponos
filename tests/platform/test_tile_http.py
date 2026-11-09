# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""HTTP tile source against a local test server: contract, T-MAP-10 answers and T-SEC-15 limits.

The server is a thread on 127.0.0.1 that plays scripted answers and records what it was asked.
Plain HTTP is accepted by `HttpTileSource` only on loopback, which is what makes this possible.
"""

from __future__ import annotations

import http.server
import logging
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime

import pytest

from ponos_coach.maps.domain import TileAddressError, TileKey
from ponos_coach.maps.ports import TileImage, TileKeyStore, TileProblem, TileSource, TileUnavailable
from ponos_coach.platform.tile_http import (
    DEFAULT_TTL_S,
    MAX_TILE_BYTES,
    MAX_TTL_S,
    TRACESTRACK,
    HttpTileSource,
    TileEndpoint,
    create_tracestrack_source,
    ttl_from_headers,
)
from tests.fakes.tiles import FakeTileSource, InMemoryTileKeyStore, make_png

KEY = TileKey("chiave-segnaposto-0123456789")
PNG = make_png()


@dataclass
class Answer:
    """One scripted answer of the test server."""

    status: int = 200
    headers: dict[str, str] = field(default_factory=lambda: {"Content-Type": "image/png"})
    body: bytes = PNG
    delay_s: float = 0.0
    declared_length: int | None = None  # a lying Content-Length, to test the limits


class TileServer:
    """A local HTTP server with an answer per request (the last one repeats)."""

    def __init__(self) -> None:
        self.answers: list[Answer] = [Answer()]
        self.requests: list[tuple[str, dict[str, str]]] = []
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                index = min(len(outer.requests), len(outer.answers) - 1)
                outer.requests.append((self.path, {k.lower(): v for k, v in self.headers.items()}))
                answer = outer.answers[index]
                time.sleep(answer.delay_s)
                self.send_response(answer.status)
                length = answer.declared_length
                self.send_header(
                    "Content-Length", str(len(answer.body) if length is None else length)
                )
                for name, value in answer.headers.items():
                    self.send_header(name, value)
                self.end_headers()
                try:
                    self.wfile.write(answer.body)
                except OSError:  # the client gave up (a timeout test): nothing to do
                    return

            def log_message(self, *_args: object) -> None:
                return

        self._server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(
            target=self._server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
        )
        self._thread.start()

    @property
    def endpoint(self) -> TileEndpoint:
        return TileEndpoint("http", "127.0.0.1", self._server.server_address[1])

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=10)


@pytest.fixture
def server() -> Iterator[TileServer]:
    running = TileServer()
    try:
        yield running
    finally:
        running.close()


def source_for(
    server: TileServer, keys: TileKeyStore | None = None, **limits: float
) -> HttpTileSource:
    store = keys if keys is not None else InMemoryTileKeyStore(KEY)
    return HttpTileSource(store, server.endpoint, user_agent="PonosCoach-test", **limits)  # type: ignore[arg-type]


# --- Contract: the real adapter and the fake answer the same way ------------------------------


class TileSourceContract:
    """What every `TileSource` promises, whatever is behind it."""

    def make(self, outcome: str) -> TileSource:
        raise NotImplementedError

    def test_a_good_key_gives_a_png_with_a_reuse_time(self) -> None:
        result = self.make("ok").fetch(12, 2200, 1500, "en")

        assert isinstance(result, TileImage)
        assert result.png.startswith(b"\x89PNG")
        assert result.ttl_s > 0

    @pytest.mark.parametrize(
        ("outcome", "problem"),
        [
            ("no_key", TileProblem.NO_KEY),
            ("rejected", TileProblem.KEY_REJECTED),
            ("credit", TileProblem.CREDIT_EXHAUSTED),
            ("offline", TileProblem.NETWORK_UNAVAILABLE),
            ("html", TileProblem.INVALID_RESPONSE),
        ],
    )
    def test_each_failure_is_a_result_with_its_own_problem(
        self, outcome: str, problem: TileProblem
    ) -> None:
        assert self.make(outcome).fetch(12, 2200, 1500, "en") == TileUnavailable(problem)

    @pytest.mark.parametrize(
        ("z", "x", "y"), [(-1, 0, 0), (20, 0, 0), (3, 8, 0), (3, 0, -1), (0, 1, 0)]
    )
    def test_t_sec_15_coordinates_outside_the_pyramid_raise_before_any_work(
        self, z: int, x: int, y: int
    ) -> None:
        with pytest.raises(TileAddressError):
            self.make("ok").fetch(z, x, y, "en")


class TestFakeTileSource(TileSourceContract):
    def make(self, outcome: str) -> TileSource:
        keys = InMemoryTileKeyStore(
            None if outcome == "no_key" else TileKey(FakeTileSource.ACCEPTED_KEY)
        )
        script = {
            "rejected": [TileUnavailable(TileProblem.KEY_REJECTED)],
            "credit": [TileUnavailable(TileProblem.CREDIT_EXHAUSTED)],
            "offline": [TileUnavailable(TileProblem.NETWORK_UNAVAILABLE)],
            "html": [TileUnavailable(TileProblem.INVALID_RESPONSE)],
        }.get(outcome)
        return FakeTileSource(keys, script)


class TestHttpTileSource(TileSourceContract):
    @pytest.fixture(autouse=True)
    def _server(self, server: TileServer) -> None:
        self.server = server

    def make(self, outcome: str) -> TileSource:
        scripts = {
            "ok": Answer(headers={"Content-Type": "image/png", "Cache-Control": "max-age=600"}),
            "no_key": Answer(),
            "rejected": Answer(status=403, body=b"nope", headers={}),
            "credit": Answer(status=429, body=b"quota", headers={}),
            "html": Answer(headers={"Content-Type": "text/html"}, body=b"<html>login</html>"),
            "offline": Answer(),
        }
        self.server.answers = [scripts[outcome]]
        keys = InMemoryTileKeyStore(None if outcome == "no_key" else KEY)
        if outcome == "offline":
            self.server.close()  # nothing listens any more: connection refused
        return source_for(self.server, keys, timeout_s=2.0, total_timeout_s=4.0)


# --- What goes on the wire ---------------------------------------------------------------------


def test_the_request_has_the_style_path_key_and_a_generic_user_agent(server: TileServer) -> None:
    source_for(server).fetch(12, 2200, 1500, "it")

    ((path, headers),) = server.requests
    assert path == f"/topo_it/12/2200/1500.png?key={KEY.reveal()}"
    assert headers["user-agent"] == "PonosCoach-test"
    assert headers["accept"] == "image/png"
    assert "cookie" not in headers
    assert "authorization" not in headers


@pytest.mark.parametrize(("language", "style"), [("it", "it"), ("en", "en"), ("de", ""), ("", "")])
def test_the_label_language_picks_the_style_or_the_neutral_one(
    server: TileServer, language: str, style: str
) -> None:
    source_for(server).fetch(3, 1, 1, language)

    assert server.requests[0][0].startswith(f"/topo_{style}/3/1/1.png")


def test_one_attempt_per_call_even_when_the_answer_is_an_error(server: TileServer) -> None:
    server.answers = [Answer(status=500, body=b"boom", headers={})]

    result = source_for(server).fetch(5, 1, 1, "en")

    assert result == TileUnavailable(TileProblem.NETWORK_UNAVAILABLE)
    assert len(server.requests) == 1


@pytest.mark.parametrize("status", [401, 403])
def test_t_map_10_a_refused_key_is_key_rejected(server: TileServer, status: int) -> None:
    server.answers = [Answer(status=status, body=b"", headers={})]

    assert source_for(server).fetch(5, 1, 1, "en") == TileUnavailable(TileProblem.KEY_REJECTED)


@pytest.mark.parametrize("status", [402, 429])
def test_t_map_10_an_exhausted_credit_or_quota_is_credit_exhausted(
    server: TileServer, status: int
) -> None:
    server.answers = [Answer(status=status, body=b"", headers={})]

    assert source_for(server).fetch(5, 1, 1, "en") == TileUnavailable(TileProblem.CREDIT_EXHAUSTED)


@pytest.mark.parametrize("status", [500, 502, 503, 504])
def test_t_map_10_a_provider_that_is_down_is_the_network_being_unavailable(
    server: TileServer, status: int
) -> None:
    server.answers = [Answer(status=status, body=b"", headers={})]

    assert source_for(server).fetch(5, 1, 1, "en") == TileUnavailable(
        TileProblem.NETWORK_UNAVAILABLE
    )


@pytest.mark.parametrize("status", [204, 301, 302, 307, 400, 404, 410])
def test_t_sec_15_other_statuses_are_invalid_and_redirects_are_not_followed(
    server: TileServer, status: int
) -> None:
    other = TileServer()  # a second host the redirect points at
    try:
        location = f"http://127.0.0.1:{other.endpoint.port}/stolen"
        server.answers = [Answer(status=status, body=b"", headers={"Location": location})]

        result = source_for(server).fetch(5, 1, 1, "en")

        assert result == TileUnavailable(TileProblem.INVALID_RESPONSE)
        assert other.requests == []  # the key never went to another place
    finally:
        other.close()


def test_t_map_10_a_server_that_does_not_answer_in_time_is_a_timeout_not_a_hang(
    server: TileServer,
) -> None:
    server.answers = [Answer(delay_s=3.0)]
    started = time.monotonic()

    result = source_for(server, timeout_s=0.3, total_timeout_s=1.0).fetch(5, 1, 1, "en")

    assert result == TileUnavailable(TileProblem.NETWORK_UNAVAILABLE)
    assert time.monotonic() - started < 2.5


def test_t_map_10_a_connection_that_is_refused_is_the_network_being_unavailable() -> None:
    gone = TileServer()
    endpoint = gone.endpoint
    gone.close()
    source = HttpTileSource(InMemoryTileKeyStore(KEY), endpoint, timeout_s=1.0)

    assert source.fetch(5, 1, 1, "en") == TileUnavailable(TileProblem.NETWORK_UNAVAILABLE)


def test_a_body_that_trickles_in_is_cut_by_the_total_deadline(server: TileServer) -> None:
    clock = iter([0.0, 100.0])  # the deadline is set, then it has passed at the first read
    source = HttpTileSource(
        InMemoryTileKeyStore(KEY), server.endpoint, monotonic=lambda: next(clock)
    )

    result = source.fetch(5, 1, 1, "en")

    assert result == TileUnavailable(TileProblem.NETWORK_UNAVAILABLE)


# --- What the answer must be -------------------------------------------------------------------


def test_t_sec_15_html_instead_of_a_png_is_invalid(server: TileServer) -> None:
    server.answers = [Answer(headers={"Content-Type": "text/html"}, body=b"<html>captcha</html>")]

    assert source_for(server).fetch(5, 1, 1, "en") == TileUnavailable(TileProblem.INVALID_RESPONSE)


def test_t_sec_15_html_with_a_png_content_type_is_still_invalid(server: TileServer) -> None:
    server.answers = [Answer(body=b"<html>" + b"x" * 200 + b"</html>")]

    assert source_for(server).fetch(5, 1, 1, "en") == TileUnavailable(TileProblem.INVALID_RESPONSE)


def test_t_sec_15_a_truncated_png_is_invalid(server: TileServer) -> None:
    server.answers = [Answer(body=PNG[:-20])]

    assert source_for(server).fetch(5, 1, 1, "en") == TileUnavailable(TileProblem.INVALID_RESPONSE)


def test_t_sec_15_a_png_that_declares_a_huge_image_is_invalid(server: TileServer) -> None:
    server.answers = [Answer(body=make_png(4000, 4000))]

    assert source_for(server).fetch(5, 1, 1, "en") == TileUnavailable(TileProblem.INVALID_RESPONSE)


def test_t_sec_15_an_enormous_png_is_refused_by_its_declared_length_without_reading_it(
    server: TileServer,
) -> None:
    big = make_png(extra_bytes=3 * MAX_TILE_BYTES)
    server.answers = [Answer(body=big)]

    result = source_for(server).fetch(5, 1, 1, "en")

    assert result == TileUnavailable(TileProblem.INVALID_RESPONSE)


def test_t_sec_15_a_body_that_does_not_match_its_declared_length_is_never_accepted(
    server: TileServer,
) -> None:
    cap = 20_000
    server.answers = [Answer(body=make_png(extra_bytes=cap * 3), declared_length=len(PNG))]

    result = source_for(server).fetch(5, 1, 1, "en")

    assert isinstance(result, TileUnavailable)  # either way it is never accepted whole


def test_a_png_within_the_cap_but_above_a_lower_configured_cap_is_refused(
    server: TileServer,
) -> None:
    server.answers = [Answer(body=make_png(extra_bytes=5000))]
    source = HttpTileSource(InMemoryTileKeyStore(KEY), server.endpoint, max_bytes=len(PNG) + 100)

    assert source.fetch(5, 1, 1, "en") == TileUnavailable(TileProblem.INVALID_RESPONSE)


# --- Reuse time ----------------------------------------------------------------------------------

NOW = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("cache_control", "expires", "date", "expected"),
    [
        ("public, max-age=3600", None, None, 3600),
        ("max-age=0", None, None, 0),
        ("no-store", None, None, 0),
        ("private, no-cache", None, None, 0),
        ("max-age=99999999", None, None, MAX_TTL_S),
        ("public", None, None, DEFAULT_TTL_S),
        ("max-age=abc", None, None, DEFAULT_TTL_S),
        (None, None, None, DEFAULT_TTL_S),
        (None, "Sun, 08 Nov 2026 13:00:00 GMT", "Sun, 08 Nov 2026 12:00:00 GMT", 3600),
        (None, "Sun, 08 Nov 2026 13:00:00 GMT", None, 3600),  # against the local clock
        (None, "Sun, 08 Nov 2026 11:00:00 GMT", None, 0),  # already in the past
        (None, "not a date", None, DEFAULT_TTL_S),
        ("max-age=60", "Sun, 08 Nov 2026 23:00:00 GMT", None, 60),  # Cache-Control wins
    ],
)
def test_the_reuse_time_comes_from_the_headers(
    cache_control: str | None, expires: str | None, date: str | None, expected: int
) -> None:
    assert ttl_from_headers(cache_control, expires, date, NOW) == expected


def test_the_image_carries_the_reuse_time_of_the_response(server: TileServer) -> None:
    server.answers = [Answer(headers={"Content-Type": "image/png", "Cache-Control": "max-age=777"})]

    result = source_for(server).fetch(5, 1, 1, "en")

    assert result == TileImage(PNG, 777)


# --- T-SEC-15: only that host, only HTTPS, the key nowhere it should not be ----------------


def test_t_sec_15_the_app_source_talks_to_tracestrack_over_https_only() -> None:
    assert TileEndpoint("https", "tile.tracestrack.com", 443) == TRACESTRACK
    source = create_tracestrack_source(InMemoryTileKeyStore(KEY))
    assert source._endpoint is TRACESTRACK  # the one place the host is decided


@pytest.mark.parametrize(
    ("scheme", "host", "port"),
    [
        ("http", "tile.tracestrack.com", 80),  # plain HTTP to a real host
        ("http", "example.org", 80),
        ("http", "192.168.1.10", 8080),  # a LAN address is not loopback
        ("ftp", "127.0.0.1", 21),
        ("file", "127.0.0.1", 1),
        ("https", "bad host", 443),
        ("https", "tile.tracestrack.com/../x", 443),
        ("https", "user@tile.tracestrack.com", 443),
        ("https", "", 443),
        ("https", "tile.tracestrack.com", 0),
        ("https", "tile.tracestrack.com", 70000),
    ],
)
def test_t_sec_15_endpoints_that_are_not_https_or_loopback_are_refused(
    scheme: str, host: str, port: int
) -> None:
    with pytest.raises(ValueError, match=r"endpoint|HTTP"):
        TileEndpoint(scheme, host, port)


def test_t_sec_15_the_key_is_never_in_a_log_an_exception_or_a_result(
    server: TileServer, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    outcomes = [
        Answer(status=403, body=b"", headers={}),
        Answer(status=429, body=b"", headers={}),
        Answer(status=500, body=b"", headers={}),
        Answer(headers={"Content-Type": "text/html"}, body=b"<html>"),
        Answer(),
    ]
    results = []
    for answer in outcomes:
        server.answers = [answer]
        results.append(source_for(server).fetch(7, 3, 4, "en"))
    dead = TileServer()
    endpoint = dead.endpoint
    dead.close()
    results.append(
        HttpTileSource(InMemoryTileKeyStore(KEY), endpoint, timeout_s=1.0).fetch(7, 3, 4, "en")
    )

    assert KEY.reveal() not in caplog.text
    assert KEY.reveal() not in repr(results)
    assert "/7/3/4" not in caplog.text  # nor the address: it says which area is being looked at
    assert f"{endpoint.port}" not in caplog.text


def test_t_sec_15_without_a_key_nothing_is_sent_at_all(server: TileServer) -> None:
    source = source_for(server, InMemoryTileKeyStore(None))

    assert source.fetch(5, 1, 1, "en") == TileUnavailable(TileProblem.NO_KEY)
    assert server.requests == []


def test_a_key_store_that_cannot_be_read_counts_as_no_key(server: TileServer) -> None:
    keys = InMemoryTileKeyStore(KEY)
    keys.unavailable = True

    assert source_for(server, keys).fetch(5, 1, 1, "en") == TileUnavailable(TileProblem.NO_KEY)
    assert server.requests == []


def test_a_new_key_applies_to_the_next_request_without_rebuilding_the_source(
    server: TileServer,
) -> None:
    keys = InMemoryTileKeyStore(KEY)
    source = source_for(server, keys)
    source.fetch(5, 1, 1, "en")
    other = TileKey("altra-chiave-segnaposto-9876")
    keys.save(other)

    source.fetch(5, 1, 1, "en")

    assert [p.split("key=")[1] for p, _ in server.requests] == [KEY.reveal(), other.reveal()]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"timeout_s": 0.0},
        {"timeout_s": -1.0},
        {"timeout_s": 5.0, "total_timeout_s": 1.0},
        {"max_bytes": 0},
    ],
)
def test_limits_that_make_no_sense_are_refused(kwargs: dict[str, float]) -> None:
    with pytest.raises(ValueError, match="positive"):
        HttpTileSource(InMemoryTileKeyStore(KEY), TRACESTRACK, **kwargs)  # type: ignore[arg-type]


@pytest.mark.parametrize("agent", ["", "x" * 65, "Ponos\r\nX-Evil: 1", "Pönos"])
def test_a_user_agent_that_could_inject_a_header_is_refused(agent: str) -> None:
    with pytest.raises(ValueError, match="user agent"):
        HttpTileSource(InMemoryTileKeyStore(KEY), TRACESTRACK, user_agent=agent)
