# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""HTTPS tile source (`TileSource` port): Tracestrack Topo with a personal key.

The only host the app contacts for the map is `tile.tracestrack.com`, over HTTPS with full
certificate verification. Every request has a socket timeout and a total deadline; there is one
attempt per call and no retry; redirects are never followed and no system proxy is used (the
client is `http.client`, which does neither). The answer must be a PNG, within a byte cap, with
a size in pixels that makes sense: anything else is `INVALID_RESPONSE`. The key travels in the
query of the request, as the provider requires, and nowhere else: never in a log, an exception or
a message; the URL is not logged either (it says which area is looked at).

A plain-HTTP endpoint is accepted only on a loopback address (a local tile server).
"""

from __future__ import annotations

import http.client
import logging
import re
import ssl
import struct
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Final

from ponos_coach.maps.domain import TileAddress, TileKey, tile_style
from ponos_coach.maps.ports import (
    MAX_TILE_TTL_S,
    TileImage,
    TileKeyStore,
    TileKeyStoreError,
    TileProblem,
    TileResult,
    TileUnavailable,
)

_LOG = logging.getLogger(__name__)

MAX_TILE_BYTES: Final = 1024 * 1024  # a 512 px tile is a few hundred KiB at most
MAX_TILE_PIXELS: Final = 1024  # side of the tile image; tiles are 256 or 512
DEFAULT_TTL_S: Final = 24 * 3600  # when the provider says nothing about reuse
MAX_TTL_S: Final = 30 * 24 * 3600  # however long the provider allows, the app renews monthly
_LOOPBACK_HOSTS: Final = frozenset({"127.0.0.1", "::1", "localhost"})
_HOST: Final = re.compile(r"[A-Za-z0-9.-]{1,253}|::1")
_USER_AGENT: Final = re.compile(r"[ -~]{1,64}")  # printable ASCII only: no line breaks
_MAX_AGE: Final = re.compile(r"max-age=(\d{1,10})")
_CHUNK: Final = 16 * 1024
_PNG_SIGNATURE: Final = b"\x89PNG\r\n\x1a\n"
_PNG_END: Final = b"IEND\xaeB`\x82"
_IHDR: Final = struct.Struct(">I4sII")  # chunk length, type, width, height
_KEY_REJECTED: Final = frozenset({401, 403})
_CREDIT_OVER: Final = frozenset({402, 429})
_SERVER_ERROR_FIRST: Final = 500
_HTTP_OK: Final = 200


@dataclass(frozen=True, slots=True)
class TileEndpoint:
    """Where the tiles are: scheme, host and port. `https` or, on loopback only, `http`."""

    scheme: str
    host: str
    port: int

    def __post_init__(self) -> None:
        if self.scheme not in {"https", "http"} or _HOST.fullmatch(self.host) is None:
            raise ValueError("invalid tile endpoint")
        if not 0 < self.port < 65536:
            raise ValueError("invalid tile endpoint")
        if self.scheme == "http" and self.host not in _LOOPBACK_HOSTS:
            raise ValueError("plain HTTP is only allowed on a loopback address")


TRACESTRACK: Final = TileEndpoint("https", "tile.tracestrack.com", 443)


def _png_is_sane(data: bytes) -> bool:
    """A complete PNG whose first chunk is a header with plausible dimensions."""
    header_end = len(_PNG_SIGNATURE) + _IHDR.size
    if len(data) < header_end + len(_PNG_END) or not data.startswith(_PNG_SIGNATURE):
        return False
    length, kind, width, height = _IHDR.unpack_from(data, len(_PNG_SIGNATURE))
    return (
        length == 13
        and kind == b"IHDR"
        and 0 < width <= MAX_TILE_PIXELS
        and 0 < height <= MAX_TILE_PIXELS
        and data.endswith(_PNG_END)
    )


def ttl_from_headers(
    cache_control: str | None, expires: str | None, date: str | None, now: datetime
) -> int:
    """Seconds a tile may be reused, from the response headers.

    `Cache-Control: max-age=N` wins; `no-store` and `no-cache` mean "do not keep" (0); otherwise
    `Expires` minus `Date` (or `now`); with neither, `DEFAULT_TTL_S`. Never above `MAX_TTL_S`.
    Unparseable values count as absent.
    """
    if cache_control is not None:
        directives = [part.strip().lower() for part in cache_control.split(",")]
        if "no-store" in directives or "no-cache" in directives:
            return 0
        for directive in directives:
            match = _MAX_AGE.fullmatch(directive)
            if match:
                return min(int(match.group(1)), MAX_TTL_S)
    return _ttl_from_expires(expires, date, now) if expires is not None else DEFAULT_TTL_S


def _ttl_from_expires(expires: str, date: str | None, now: datetime) -> int:
    try:
        end = parsedate_to_datetime(expires)
        start = parsedate_to_datetime(date) if date is not None else now
    except (TypeError, ValueError):
        return DEFAULT_TTL_S
    if end.tzinfo is None or start.tzinfo is None:
        return DEFAULT_TTL_S
    return max(0, min(int((end - start).total_seconds()), MAX_TTL_S))


class HttpTileSource:
    """Tiles over HTTP(S) from one endpoint, with the key from the key store.

    Args:
        key_store: where the key is read at every request (so a new key applies at once).
        endpoint: `TRACESTRACK` in the app; a loopback `http` endpoint for a local server.
        user_agent: generic name of the app; no personal data.
        timeout_s: limit for connecting and for each read.
        total_timeout_s: limit for the whole request, however slowly the bytes arrive.
        max_bytes: largest answer accepted.
        monotonic: clock for the deadline (injected for tests).
        now: UTC clock for the expiry arithmetic (injected for tests).
    """

    def __init__(  # noqa: PLR0913 - each one is a distinct limit or volatile dependency
        self,
        key_store: TileKeyStore,
        endpoint: TileEndpoint = TRACESTRACK,
        *,
        user_agent: str = "PonosCoach",
        timeout_s: float = 8.0,
        total_timeout_s: float = 20.0,
        max_bytes: int = MAX_TILE_BYTES,
        monotonic: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if not (timeout_s > 0 and total_timeout_s >= timeout_s and max_bytes > 0):
            raise ValueError("timeouts and byte cap must be positive")
        if _USER_AGENT.fullmatch(user_agent) is None:
            raise ValueError("invalid user agent")
        self._keys = key_store
        self._endpoint = endpoint
        self._user_agent = user_agent
        self._timeout_s = timeout_s
        self._total_timeout_s = total_timeout_s
        self._max_bytes = max_bytes
        self._monotonic = monotonic
        self._now = now

    def fetch(self, z: int, x: int, y: int, language: str) -> TileResult:
        """See `TileSource.fetch`. One attempt; the key never leaves this method."""
        address = TileAddress(z, x, y)  # raises TileAddressError before anything else
        try:
            key = self._keys.load()
        except TileKeyStoreError:
            key = None
        if key is None:
            return TileUnavailable(TileProblem.NO_KEY)
        result = self._request(address, tile_style(language), key)
        if isinstance(result, TileUnavailable):
            _LOG.info("map tile unavailable (%s)", result.problem.value)
        return result

    def _request(self, address: TileAddress, style: str, key: TileKey) -> TileResult:
        path = f"/topo_{style}/{address.z}/{address.x}/{address.y}.png?key={key.reveal()}"
        deadline = self._monotonic() + self._total_timeout_s
        connection = self._connection()
        try:
            connection.request(
                "GET",
                path,
                headers={
                    "User-Agent": self._user_agent,
                    "Accept": "image/png",
                    "Connection": "close",
                },
            )
            return self._answer(connection.getresponse(), deadline)
        except (OSError, http.client.HTTPException):
            # Timeouts, refused or reset connections, TLS failures, malformed answers. The
            # exception is dropped on purpose: its text can carry the host and the URL.
            return TileUnavailable(TileProblem.NETWORK_UNAVAILABLE)
        finally:
            connection.close()

    def _connection(self) -> http.client.HTTPConnection:
        endpoint = self._endpoint
        if endpoint.scheme == "https":
            return http.client.HTTPSConnection(
                endpoint.host,
                endpoint.port,
                timeout=self._timeout_s,
                context=ssl.create_default_context(),
            )
        return http.client.HTTPConnection(endpoint.host, endpoint.port, timeout=self._timeout_s)

    def _answer(self, response: http.client.HTTPResponse, deadline: float) -> TileResult:
        problem = self._problem_in_head(response)
        if problem is not None:
            return TileUnavailable(problem)
        data = self._read_body(response, deadline)
        if data is None:
            return TileUnavailable(TileProblem.NETWORK_UNAVAILABLE)  # too slow: deadline passed
        if len(data) > self._max_bytes or not _png_is_sane(data):
            return TileUnavailable(TileProblem.INVALID_RESPONSE)
        ttl = ttl_from_headers(
            response.getheader("Cache-Control"),
            response.getheader("Expires"),
            response.getheader("Date"),
            self._now(),
        )
        return TileImage(data, min(ttl, MAX_TILE_TTL_S))

    def _problem_in_head(self, response: http.client.HTTPResponse) -> TileProblem | None:
        """What the status line and the headers already say is wrong; `None` if all is well."""
        status = response.status
        if status in _KEY_REJECTED:
            return TileProblem.KEY_REJECTED
        if status in _CREDIT_OVER:
            return TileProblem.CREDIT_EXHAUSTED
        if status >= _SERVER_ERROR_FIRST:
            return TileProblem.NETWORK_UNAVAILABLE
        declared = response.getheader("Content-Length")
        too_big = declared is not None and (
            not declared.isdigit() or int(declared) > self._max_bytes
        )
        content_type = (response.getheader("Content-Type") or "").split(";")[0].strip().lower()
        if status != _HTTP_OK or too_big or content_type != "image/png":
            return TileProblem.INVALID_RESPONSE  # redirects included: they are never followed
        return None

    def _read_body(self, response: http.client.HTTPResponse, deadline: float) -> bytes | None:
        """The body up to one byte past the cap, or `None` if the deadline passes first."""
        chunks: list[bytes] = []
        total = 0
        while total <= self._max_bytes:
            if self._monotonic() >= deadline:
                return None
            chunk = response.read(min(_CHUNK, self._max_bytes + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
        return b"".join(chunks)


def create_tracestrack_source(
    key_store: TileKeyStore, *, user_agent: str = "PonosCoach"
) -> HttpTileSource:
    """The tile source of the app: Tracestrack Topo, HTTPS, the app's limits (single place)."""
    return HttpTileSource(key_store, TRACESTRACK, user_agent=user_agent)
