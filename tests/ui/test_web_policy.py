# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""T-SEC-06 (parte pura): solo lo schema dell'app e ammesso; risorse in elenco chiuso."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QUrl

from ponos_coach.ui.web_policy import APP_PAGE_URL, is_request_allowed
from ponos_coach.ui.web_resources import resource_for_path


@pytest.mark.parametrize(
    "url",
    [APP_PAGE_URL, "ponos://app/plotly.min.js", "PONOS://app/chart.js"],
)
def test_app_scheme_is_allowed(url: str) -> None:
    assert is_request_allowed(QUrl(url))


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com/x.js",
        "https://example.com/",
        "http://127.0.0.1:8000/",
        "file:///C:/Windows/win.ini",
        "data:text/html,<script>alert(1)</script>",
        "blob:ponos://app/1234",
        "ftp://example.com/",
        "ws://example.com/",
        "ponos://evil/chart.js",
        "ponos:chart.js",
        "javascript:alert(1)",
        "about:blank",
        "",
    ],
)
def test_everything_else_is_blocked(url: str) -> None:
    assert not is_request_allowed(QUrl(url))


def test_only_the_three_known_resources_are_served() -> None:
    for path in ("/chart.html", "/chart.js", "/plotly.min.js"):
        resource = resource_for_path(path)
        assert resource is not None
        assert resource.content

    for path in ("/", "/../chart.js", "/etc/passwd", "/chart.html/", "/CHART.HTML", ""):
        assert resource_for_path(path) is None


def test_plotly_comes_from_the_installed_package_and_page_has_no_remote_reference() -> None:
    page = resource_for_path("/chart.html")
    plotly_js = resource_for_path("/plotly.min.js")

    assert page is not None
    assert plotly_js is not None
    assert len(plotly_js.content) > 1_000_000
    text = page.content.decode("utf-8")
    assert "http://" not in text
    assert "https://" not in text
    assert "script-src ponos://app" in text
