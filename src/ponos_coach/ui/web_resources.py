# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Local resources served to the charts page: closed list, no path from outside."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from importlib import resources


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


_HTML = "text/html; charset=utf-8"
_JS = "text/javascript; charset=utf-8"
_LOADERS: dict[str, tuple[Callable[[], bytes], str]] = {
    "/chart.html": (_read_chart_html, _HTML),
    "/chart.js": (_read_chart_js, _JS),
    "/plotly.min.js": (_read_plotly_js, _JS),
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
