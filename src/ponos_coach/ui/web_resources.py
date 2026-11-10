# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Local resources served to the web pages (charts and map): closed list, no path from outside."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from importlib import resources

from PySide6.QtCore import QFile, QIODevice


@dataclass(frozen=True, slots=True)
class WebResource:
    """A served content: bytes and MIME type."""

    content: bytes
    mime_type: str


def _read_plotly_js() -> bytes:
    """`plotly.min.js` from the installed `plotly` package (never from a CDN)."""
    return (resources.files("plotly") / "package_data" / "plotly.min.js").read_bytes()


def _read_page_file(name: str) -> bytes:
    return (resources.files("ponos_coach.ui") / "web" / name).read_bytes()


def _read_chart_html() -> bytes:
    return _read_page_file("chart.html")


def _read_chart_js() -> bytes:
    return _read_page_file("chart.js")


def _vendor_reader(name: str) -> Callable[[], bytes]:
    """Reader of a file vendored from npm (`web/vendor/`, hash-checked by a test)."""
    return lambda: _read_page_file(f"vendor/{name}")


def _read_qwebchannel_js() -> bytes:
    """`qwebchannel.js` from Qt's own resources (the page cannot reach `qrc:`)."""
    source = QFile(":/qtwebchannel/qwebchannel.js")
    if not source.open(QIODevice.OpenModeFlag.ReadOnly):
        raise FileNotFoundError("qwebchannel.js is not available in this Qt build")
    try:
        return bytes(source.readAll().data())
    finally:
        source.close()


_HTML = "text/html; charset=utf-8"
_JS = "text/javascript; charset=utf-8"
_CSS = "text/css; charset=utf-8"
_LOADERS: dict[str, tuple[Callable[[], bytes], str]] = {
    "/chart.html": (_read_chart_html, _HTML),
    "/chart.js": (_read_chart_js, _JS),
    "/plotly.min.js": (_read_plotly_js, _JS),
    "/map.html": (lambda: _read_page_file("map.html"), _HTML),
    "/map.js": (lambda: _read_page_file("map.js"), _JS),
    "/qwebchannel.js": (_read_qwebchannel_js, _JS),
    "/leaflet.js": (_vendor_reader("leaflet.js"), _JS),
    "/leaflet.css": (_vendor_reader("leaflet.css"), _CSS),
    "/leaflet.markercluster.js": (_vendor_reader("leaflet.markercluster.js"), _JS),
    "/MarkerCluster.css": (_vendor_reader("MarkerCluster.css"), _CSS),
    "/MarkerCluster.Default.css": (_vendor_reader("MarkerCluster.Default.css"), _CSS),
}

_cache: dict[str, WebResource] = {}


def resource_for_path(path: str) -> WebResource | None:
    """The resource of a URL path (`/chart.html`...), `None` if it is not in the list."""
    loader = _LOADERS.get(path)
    if loader is None:
        return None
    if path not in _cache:
        read, mime_type = loader
        _cache[path] = WebResource(read(), mime_type)
    return _cache[path]
