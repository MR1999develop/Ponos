# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Network policy of the web pages (charts and map): only the app's scheme, nothing else.

The scheme has two hosts: `app` (the pages and their scripts) and `tiles` (map tiles, answered by
the tile proxy in Python, never by the network). The decisions are pure functions
(`is_request_allowed`, `is_navigation_allowed`); `register_app_scheme` must be called before
creating the `QApplication`.
"""

from __future__ import annotations

from PySide6.QtCore import QUrl
from PySide6.QtWebEngineCore import QWebEngineUrlScheme

from ponos_coach.maps.domain import MapBackground, TileAddressError, parse_tile_path

APP_SCHEME = "ponos"
APP_HOST = "app"
TILES_HOST = "tiles"
APP_PAGE_URL = f"{APP_SCHEME}://{APP_HOST}/chart.html"
APP_MAP_URL = f"{APP_SCHEME}://{APP_HOST}/map.html"


_registered = False


def tile_url_template(background: MapBackground) -> str:
    """`ponos://tiles/{key}/{z}/{x}/{y}` of a background: Leaflet fills `{z}`, `{x}` and `{y}`."""
    return f"{APP_SCHEME}://{TILES_HOST}/{background.key}/{{z}}/{{x}}/{{y}}"


def is_tile_request(url: QUrl) -> bool:
    """True for `ponos://tiles/source/z/x/y` with a known source and integers inside the tile
    pyramid of that source (and nothing else: no query, no fragment, no credentials, no port)."""
    if url.scheme() != APP_SCHEME or url.host() != TILES_HOST:
        return False
    if url.hasQuery() or url.hasFragment() or url.port() != -1 or url.userInfo():
        return False
    try:
        parse_tile_path(url.path())
    except TileAddressError:
        return False
    return True


def is_request_allowed(url: QUrl) -> bool:
    """True for `ponos://app/...` and for tile addresses `ponos://tiles/source/z/x/y`: http,
    https, file, data, blob and every other are forbidden."""
    if url.scheme() != APP_SCHEME:
        return False
    return url.host() == APP_HOST or is_tile_request(url)


def is_navigation_allowed(url: QUrl) -> bool:
    """True only for `ponos://app/...`: a page never navigates to tiles or anywhere else."""
    return url.scheme() == APP_SCHEME and url.host() == APP_HOST


def register_app_scheme() -> None:
    """Register the app's scheme (once per process, before the `QApplication`)."""
    global _registered  # noqa: PLW0603 - scheme registration is per process
    if _registered:
        return
    scheme = QWebEngineUrlScheme(APP_SCHEME.encode("ascii"))
    scheme.setSyntax(QWebEngineUrlScheme.Syntax.Host)
    scheme.setFlags(QWebEngineUrlScheme.Flag.SecureScheme)
    QWebEngineUrlScheme.registerScheme(scheme)
    _registered = True
