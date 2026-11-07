# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Network policy of the charts page: only the app's scheme, nothing else.

The decision is a pure function (`is_request_allowed`); `register_app_scheme` must be called
before creating the `QApplication`.
"""

from __future__ import annotations

from PySide6.QtCore import QUrl
from PySide6.QtWebEngineCore import QWebEngineUrlScheme

APP_SCHEME = "ponos"
APP_HOST = "app"
APP_PAGE_URL = f"{APP_SCHEME}://{APP_HOST}/chart.html"

_registered = False


def is_request_allowed(url: QUrl) -> bool:
    """True only for `ponos://app/...`: http, https, file, data, blob and every other are
    forbidden."""
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
