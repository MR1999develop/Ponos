# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""HTTP tile source against a local test server: contract, T-MAP-10 answers and T-SEC-15 limits.

The server is a thread on 127.0.0.1 that plays scripted answers and records what it was asked.
Plain HTTP is accepted by `HttpTileSource` only on loopback, which is what makes this possible.
"""

from __future__ import annotations

import http.client
import http.server
import logging
import ssl
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime

import pytest

from ponos_coach.maps.domain import (
    BACKGROUNDS,
    CYCLOSM,
    OPENSTREETMAP,
    OPENTOPOMAP,
    MapBackground,
    TileAddress,
    TileAddressError,
)
from ponos_coach.maps.ports import (
    TileImage,
    TileNotModified,
    TileProblem,
    TileSource,
    TileUnavailable,
    TileValidators,
)
from ponos_coach.platform.tile_http import (
    DEFAULT_TTL_S,
    MAX_TILE_BYTES,
    MAX_TTL_S,
    MAX_USER_AGENT_CHARS,
    HttpTileSource,
    TileEndpoint,
    create_tile_sources,
    problem_of_failure,
    ttl_from_headers,
)
from tests.fakes.tiles import FakeTileSource, make_png

USER_AGENT = "PonosCoach/0.0.1 (+https://example.org/contact)"
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


def source_for(server: TileServer, **limits: float) -> HttpTileSource:
    return HttpTileSource(OPENSTREETMAP, endpoint=server.endpoint, user_agent=USER_AGENT, **limits)  # type: ignore[arg-type]


# --- Contract: the real adapter and the fake answer the same way ------------------------------


class TileSourceContract:
    """What every `TileSource` promises, whatever is behind it."""

    def make(self, outcome: str) -> TileSource:
        raise NotImplementedError

    def test_a_good_answer_gives_a_png_with_a_reuse_time(self) -> None:
        result = self.make("ok").fetch(12, 2200, 1500)

        assert isinstance(result, TileImage)
        assert result.png.startswith(b"\x89PNG")
        assert result.ttl_s > 0

    @pytest.mark.parametrize(
        ("outcome", "problem"),
        [
            ("forbidden", TileProblem.REFUSED),
            ("limited", TileProblem.REFUSED),
            ("offline", TileProblem.NETWORK_UNAVAILABLE),
            ("html", TileProblem.INVALID_RESPONSE),
        ],
    )
    def test_each_failure_is_a_result_with_its_own_problem(
        self, outcome: str, problem: TileProblem
    ) -> None:
        assert self.make(outcome).fetch(12, 2200, 1500) == TileUnavailable(problem)

    @pytest.mark.parametrize(
        ("z", "x", "y"), [(-1, 0, 0), (20, 0, 0), (3, 8, 0), (3, 0, -1), (0, 1, 0)]
    )
    def test_t_sec_15_coordinates_outside_the_pyramid_raise_before_any_work(
        self, z: int, x: int, y: int
    ) -> None:
        with pytest.raises(TileAddressError):
            self.make("ok").fetch(z, x, y)


class TestFakeTileSource(TileSourceContract):
    def make(self, outcome: str) -> TileSource:
        script = {
            "forbidden": [TileUnavailable(TileProblem.REFUSED)],
            "limited": [TileUnavailable(TileProblem.REFUSED)],
            "offline": [TileUnavailable(TileProblem.NETWORK_UNAVAILABLE)],
            "html": [TileUnavailable(TileProblem.INVALID_RESPONSE)],
        }.get(outcome)
        return FakeTileSource(script)


class TestHttpTileSource(TileSourceContract):
    @pytest.fixture(autouse=True)
    def _server(self, server: TileServer) -> None:
        self.server = server

    def make(self, outcome: str) -> TileSource:
        scripts = {
            "ok": Answer(headers={"Content-Type": "image/png", "Cache-Control": "max-age=600"}),
            "forbidden": Answer(status=403, body=b"nope", headers={}),
            "limited": Answer(status=429, body=b"quota", headers={}),
            "html": Answer(headers={"Content-Type": "text/html"}, body=b"<html>login</html>"),
            "offline": Answer(),
        }
        self.server.answers = [scripts[outcome]]
        if outcome == "offline":
            self.server.close()  # nothing listens any more: connection refused
        return source_for(self.server, timeout_s=2.0, total_timeout_s=4.0)


# --- What goes on the wire ---------------------------------------------------------------------


def test_the_request_has_the_plain_path_and_the_user_agent_of_the_app(server: TileServer) -> None:
    source_for(server).fetch(12, 2200, 1500)

    ((path, headers),) = server.requests
    assert path == "/12/2200/1500.png"  # no query: nothing but the tile
    assert headers["user-agent"] == USER_AGENT
    assert headers["accept"] == "image/png"
    assert "cookie" not in headers
    assert "authorization" not in headers


def test_a_first_request_is_not_conditional_and_never_asks_to_bypass_caches(
    server: TileServer,
) -> None:
    source_for(server).fetch(3, 1, 1)

    ((_, headers),) = server.requests
    assert "if-none-match" not in headers
    assert "if-modified-since" not in headers
    assert "cache-control" not in headers  # the usage policy forbids no-cache
    assert "pragma" not in headers


def test_a_conditional_request_carries_both_validators(server: TileServer) -> None:
    server.answers = [Answer(status=304, body=b"", headers={})]
    validators = TileValidators('"abc123"', "Tue, 03 Nov 2026 10:00:00 GMT")

    source_for(server).fetch(3, 1, 1, validators)

    ((_, headers),) = server.requests
    assert headers["if-none-match"] == '"abc123"'
    assert headers["if-modified-since"] == "Tue, 03 Nov 2026 10:00:00 GMT"
    assert "cache-control" not in headers
    assert "pragma" not in headers


@pytest.mark.parametrize(
    ("validators", "sent"),
    [
        (TileValidators('"only"', None), {"if-none-match"}),
        (TileValidators(None, "Tue, 03 Nov 2026 10:00:00 GMT"), {"if-modified-since"}),
    ],
)
def test_a_single_validator_is_sent_alone(
    server: TileServer, validators: TileValidators, sent: set[str]
) -> None:
    server.answers = [Answer(status=304, body=b"", headers={})]

    source_for(server).fetch(3, 1, 1, validators)

    ((_, headers),) = server.requests
    assert {"if-none-match", "if-modified-since"} & set(headers) == sent


def test_a_not_modified_answer_renews_with_the_ttl_of_its_own_headers(server: TileServer) -> None:
    server.answers = [Answer(status=304, body=b"", headers={"Cache-Control": "max-age=1234"})]

    result = source_for(server).fetch(3, 1, 1, TileValidators('"a"', None))

    assert result == TileNotModified(1234)


def test_a_not_modified_answer_without_headers_renews_for_the_default_seven_days(
    server: TileServer,
) -> None:
    server.answers = [Answer(status=304, body=b"", headers={})]

    result = source_for(server).fetch(3, 1, 1, TileValidators('"a"', None))

    assert result == TileNotModified(7 * 24 * 3600)


def test_a_not_modified_answer_to_a_request_that_was_not_conditional_is_invalid(
    server: TileServer,
) -> None:
    server.answers = [Answer(status=304, body=b"", headers={})]

    assert source_for(server).fetch(3, 1, 1) == TileUnavailable(TileProblem.INVALID_RESPONSE)


def test_a_validator_that_is_hostile_is_not_sent(server: TileServer) -> None:
    server.answers = [Answer(status=304, body=b"", headers={})]
    hostile = TileValidators('"x"\r\nX-Evil: 1', "m" * 300)

    result = source_for(server).fetch(3, 1, 1, hostile)

    ((_, headers),) = server.requests
    assert "x-evil" not in headers
    assert "if-none-match" not in headers
    assert "if-modified-since" not in headers
    assert result == TileUnavailable(TileProblem.INVALID_RESPONSE)  # nothing conditional was asked


def test_the_etag_and_last_modified_of_a_good_answer_come_with_the_image(
    server: TileServer,
) -> None:
    server.answers = [
        Answer(
            headers={
                "Content-Type": "image/png",
                "ETag": '"deadbeef"',
                "Last-Modified": "Tue, 03 Nov 2026 10:00:00 GMT",
            }
        )
    ]

    result = source_for(server).fetch(3, 1, 1)

    assert isinstance(result, TileImage)
    assert result.validators == TileValidators('"deadbeef"', "Tue, 03 Nov 2026 10:00:00 GMT")


@pytest.mark.parametrize("etag", ['"' + "e" * 300 + '"', "a\x01b", "caf\xe9"])
def test_t_sec_15_an_etag_that_is_too_long_or_has_odd_characters_is_dropped_not_kept(
    server: TileServer, etag: str
) -> None:
    server.answers = [Answer(headers={"Content-Type": "image/png", "ETag": etag})]

    result = source_for(server).fetch(3, 1, 1)

    assert isinstance(result, TileImage)  # the tile itself is fine
    assert result.validators.etag is None


def test_a_good_answer_without_cache_headers_is_reusable_for_seven_days(
    server: TileServer,
) -> None:
    server.answers = [Answer()]

    result = source_for(server).fetch(3, 1, 1)

    assert isinstance(result, TileImage)
    assert result.ttl_s == 7 * 24 * 3600


def test_one_attempt_per_call_even_when_the_answer_is_an_error(server: TileServer) -> None:
    server.answers = [Answer(status=500, body=b"boom", headers={})]

    result = source_for(server).fetch(5, 1, 1)

    assert result == TileUnavailable(TileProblem.NETWORK_UNAVAILABLE)
    assert len(server.requests) == 1


@pytest.mark.parametrize("status", [403, 429])
def test_t_map_10_a_server_that_refuses_or_limits_is_refused(
    server: TileServer, status: int
) -> None:
    server.answers = [Answer(status=status, body=b"", headers={})]

    assert source_for(server).fetch(5, 1, 1) == TileUnavailable(TileProblem.REFUSED)


@pytest.mark.parametrize("status", [500, 502, 503, 504])
def test_t_map_10_a_server_that_is_down_is_the_network_being_unavailable(
    server: TileServer, status: int
) -> None:
    server.answers = [Answer(status=status, body=b"", headers={})]

    assert source_for(server).fetch(5, 1, 1) == TileUnavailable(TileProblem.NETWORK_UNAVAILABLE)


@pytest.mark.parametrize("status", [204, 301, 302, 307, 400, 401, 402, 404, 410])
def test_t_sec_15_other_statuses_are_invalid_and_redirects_are_not_followed(
    server: TileServer, status: int
) -> None:
    other = TileServer()  # a second host the redirect points at
    try:
        location = f"http://127.0.0.1:{other.endpoint.port}/stolen"
        server.answers = [Answer(status=status, body=b"", headers={"Location": location})]

        result = source_for(server).fetch(5, 1, 1)

        assert result == TileUnavailable(TileProblem.INVALID_RESPONSE)
        assert other.requests == []  # nothing went to another place
    finally:
        other.close()


def test_t_map_10_a_server_that_does_not_answer_in_time_is_a_timeout_not_a_hang(
    server: TileServer,
) -> None:
    server.answers = [Answer(delay_s=3.0)]
    started = time.monotonic()

    result = source_for(server, timeout_s=0.3, total_timeout_s=1.0).fetch(5, 1, 1)

    assert result == TileUnavailable(TileProblem.NETWORK_UNAVAILABLE)
    assert time.monotonic() - started < 2.5


def test_t_map_10_a_connection_that_is_refused_is_the_network_being_unavailable() -> None:
    gone = TileServer()
    endpoint = gone.endpoint
    gone.close()
    source = HttpTileSource(OPENSTREETMAP, endpoint=endpoint, user_agent=USER_AGENT, timeout_s=1.0)

    assert source.fetch(5, 1, 1) == TileUnavailable(TileProblem.NETWORK_UNAVAILABLE)


@pytest.mark.parametrize(
    "error",
    [
        ssl.SSLCertVerificationError(1, "certificate verify failed: certificate has expired"),
        ssl.SSLCertVerificationError(1, "certificate verify failed: Hostname mismatch"),
    ],
)
def test_t_sec_15_a_certificate_that_does_not_verify_is_an_untrusted_server(
    error: BaseException,
) -> None:
    """The check stays on and fails closed; it only gets its own name, so the notice can say why
    (user's report of 2026-11-11: OpenTopoMap's certificate expired)."""
    assert problem_of_failure(error) is TileProblem.UNTRUSTED_SERVER


@pytest.mark.parametrize(
    "error",
    [
        TimeoutError(),
        ConnectionRefusedError(),
        ssl.SSLError(1, "wrong version number"),  # TLS broken, but not a certificate verdict
        http.client.BadStatusLine("x"),
    ],
)
def test_other_connection_failures_are_the_network_being_unavailable(error: BaseException) -> None:
    assert problem_of_failure(error) is TileProblem.NETWORK_UNAVAILABLE


def test_a_body_that_trickles_in_is_cut_by_the_total_deadline(server: TileServer) -> None:
    clock = iter([0.0, 100.0])  # the deadline is set, then it has passed at the first read
    source = HttpTileSource(
        OPENSTREETMAP,
        endpoint=server.endpoint,
        user_agent=USER_AGENT,
        monotonic=lambda: next(clock),
    )

    result = source.fetch(5, 1, 1)

    assert result == TileUnavailable(TileProblem.NETWORK_UNAVAILABLE)


# --- What the answer must be -------------------------------------------------------------------


def test_t_sec_15_html_instead_of_a_png_is_invalid(server: TileServer) -> None:
    server.answers = [Answer(headers={"Content-Type": "text/html"}, body=b"<html>captcha</html>")]

    assert source_for(server).fetch(5, 1, 1) == TileUnavailable(TileProblem.INVALID_RESPONSE)


def test_t_sec_15_html_with_a_png_content_type_is_still_invalid(server: TileServer) -> None:
    server.answers = [Answer(body=b"<html>" + b"x" * 200 + b"</html>")]

    assert source_for(server).fetch(5, 1, 1) == TileUnavailable(TileProblem.INVALID_RESPONSE)


def test_t_sec_15_a_truncated_png_is_invalid(server: TileServer) -> None:
    server.answers = [Answer(body=PNG[:-20])]

    assert source_for(server).fetch(5, 1, 1) == TileUnavailable(TileProblem.INVALID_RESPONSE)


def test_t_sec_15_a_png_that_declares_a_huge_image_is_invalid(server: TileServer) -> None:
    server.answers = [Answer(body=make_png(4000, 4000))]

    assert source_for(server).fetch(5, 1, 1) == TileUnavailable(TileProblem.INVALID_RESPONSE)


def test_t_sec_15_an_enormous_png_is_refused_by_its_declared_length_without_reading_it(
    server: TileServer,
) -> None:
    big = make_png(extra_bytes=3 * MAX_TILE_BYTES)
    server.answers = [Answer(body=big)]

    result = source_for(server).fetch(5, 1, 1)

    assert result == TileUnavailable(TileProblem.INVALID_RESPONSE)


def test_t_sec_15_a_body_that_does_not_match_its_declared_length_is_never_accepted(
    server: TileServer,
) -> None:
    cap = 20_000
    server.answers = [Answer(body=make_png(extra_bytes=cap * 3), declared_length=len(PNG))]

    result = source_for(server).fetch(5, 1, 1)

    assert isinstance(result, TileUnavailable)  # either way it is never accepted whole


def test_a_png_within_the_cap_but_above_a_lower_configured_cap_is_refused(
    server: TileServer,
) -> None:
    server.answers = [Answer(body=make_png(extra_bytes=5000))]
    source = HttpTileSource(
        OPENSTREETMAP, endpoint=server.endpoint, user_agent=USER_AGENT, max_bytes=len(PNG) + 100
    )

    assert source.fetch(5, 1, 1) == TileUnavailable(TileProblem.INVALID_RESPONSE)


# --- Reuse time ----------------------------------------------------------------------------------

NOW = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("cache_control", "expires", "date", "expected"),
    [
        ("public, max-age=3600", None, None, 3600),
        ("no-store", None, None, 0),
        ("max-age=99999999", None, None, MAX_TTL_S),
        ("public", None, None, DEFAULT_TTL_S),
        (None, "Sun, 08 Nov 2026 13:00:00 GMT", "Sun, 08 Nov 2026 12:00:00 GMT", 3600),
        (None, "Sun, 08 Nov 2026 11:00:00 GMT", None, 0),  # already in the past
        ("max-age=60", "Sun, 08 Nov 2026 23:00:00 GMT", None, 60),  # Cache-Control wins
    ],
)
def test_the_reuse_time_comes_from_the_headers(
    cache_control: str | None, expires: str | None, date: str | None, expected: int
) -> None:
    assert ttl_from_headers(cache_control, expires, date, NOW) == expected


def test_the_image_carries_the_reuse_time_of_the_response(server: TileServer) -> None:
    server.answers = [Answer(headers={"Content-Type": "image/png", "Cache-Control": "max-age=777"})]

    result = source_for(server).fetch(5, 1, 1)

    assert result == TileImage(PNG, 777, TileValidators(None, None))


# --- T-SEC-15: only that host, only HTTPS ------------------------------------------------------


def test_t_sec_15_the_app_sources_talk_to_the_three_known_servers_over_https_only() -> None:
    sources = create_tile_sources(USER_AGENT)

    assert set(sources) == set(BACKGROUNDS)  # one source per background, no other
    for background, source in sources.items():
        assert source._background is background  # the one place the hosts are decided
        assert source._endpoint is None  # HTTPS on the background's own host, port 443
        assert source._user_agent == USER_AGENT


@pytest.mark.parametrize(
    ("background", "x", "y", "url"),
    [
        (OPENSTREETMAP, 2200, 1500, "https://tile.openstreetmap.org/12/2200/1500.png"),
        (OPENTOPOMAP, 2200, 1500, "https://b.tile.opentopomap.org/12/2200/1500.png"),
        (OPENTOPOMAP, 2201, 1500, "https://c.tile.opentopomap.org/12/2201/1500.png"),
        (OPENTOPOMAP, 2202, 1500, "https://a.tile.opentopomap.org/12/2202/1500.png"),
        (CYCLOSM, 2200, 1500, "https://b.tile-cyclosm.openstreetmap.fr/cyclosm/12/2200/1500.png"),
        (CYCLOSM, 2201, 1500, "https://c.tile-cyclosm.openstreetmap.fr/cyclosm/12/2201/1500.png"),
    ],
    ids=["osm", "topo-1", "topo-2", "topo-3", "cyclosm-1", "cyclosm-2"],
)
def test_each_background_builds_its_own_url_with_a_fixed_subdomain(
    background: MapBackground, x: int, y: int, url: str
) -> None:
    assert background.url_for(TileAddress(12, x, y, background)) == url


@pytest.mark.parametrize(
    ("background", "highest"), [(OPENSTREETMAP, 19), (OPENTOPOMAP, 17), (CYCLOSM, 20)]
)
def test_a_zoom_beyond_the_maximum_of_the_background_is_refused_before_any_request(
    background: MapBackground, highest: int
) -> None:
    source = HttpTileSource(background, user_agent=USER_AGENT)  # no server: nothing is asked

    assert TileAddress(highest, 0, 0, background).z == highest
    with pytest.raises(TileAddressError):
        source.fetch(highest + 1, 0, 0)


def test_a_background_asks_the_server_at_its_own_path(server: TileServer) -> None:
    server.answers = [Answer()]
    source = HttpTileSource(
        CYCLOSM, endpoint=server.endpoint, user_agent=USER_AGENT
    )  # the host is the test server; the path is the one of CyclOSM

    assert isinstance(source.fetch(5, 1, 1), TileImage)

    assert server.requests[-1][0] == "/cyclosm/5/1/1.png"


@pytest.mark.parametrize(
    ("scheme", "host", "port"),
    [
        ("http", "tile.openstreetmap.org", 80),  # plain HTTP to a real host
        ("http", "example.org", 80),
        ("http", "192.168.1.10", 8080),  # a LAN address is not loopback
        ("ftp", "127.0.0.1", 21),
        ("file", "127.0.0.1", 1),
        ("https", "bad host", 443),
        ("https", "tile.openstreetmap.org/../x", 443),
        ("https", "user@tile.openstreetmap.org", 443),
        ("https", "", 443),
        ("https", "tile.openstreetmap.org", 0),
        ("https", "tile.openstreetmap.org", 70000),
    ],
)
def test_t_sec_15_endpoints_that_are_not_https_or_loopback_are_refused(
    scheme: str, host: str, port: int
) -> None:
    with pytest.raises(ValueError, match=r"endpoint|HTTP"):
        TileEndpoint(scheme, host, port)


def test_t_sec_15_neither_the_address_nor_the_port_is_in_a_log(
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
    for answer in outcomes:
        server.answers = [answer]
        source_for(server).fetch(7, 3, 4)
    dead = TileServer()
    endpoint = dead.endpoint
    dead.close()
    HttpTileSource(OPENSTREETMAP, endpoint=endpoint, user_agent=USER_AGENT, timeout_s=1.0).fetch(
        7, 3, 4
    )

    assert "/7/3/4" not in caplog.text  # it says which area is being looked at
    assert f"{endpoint.port}" not in caplog.text


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
        HttpTileSource(OPENSTREETMAP, user_agent=USER_AGENT, **kwargs)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "agent", ["", "x" * (MAX_USER_AGENT_CHARS + 1), "Ponos\r\nX-Evil: 1", "Pönos"]
)
def test_a_user_agent_that_could_inject_a_header_is_refused(agent: str) -> None:
    with pytest.raises(ValueError, match="user agent"):
        HttpTileSource(OPENSTREETMAP, user_agent=agent)


def test_a_user_agent_is_required_there_is_no_library_default() -> None:
    with pytest.raises(TypeError):
        HttpTileSource(OPENSTREETMAP)  # type: ignore[call-arg]
