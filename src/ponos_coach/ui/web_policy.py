# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Politica di rete della pagina dei grafici: solo lo schema dell'app, nient'altro.

La decisione e una funzione pura (`is_request_allowed`); `register_app_scheme` va chiamata
prima di creare la `QApplication`.
"""

from __future__ import annotations

from PySide6.QtCore import QUrl
from PySide6.QtWebEngineCore import QWebEngineUrlScheme

APP_SCHEME = "ponos"
APP_HOST = "app"
APP_PAGE_URL = f"{APP_SCHEME}://{APP_HOST}/chart.html"

_registered = False


def is_request_allowed(url: QUrl) -> bool:
    """Vero solo per `ponos://app/...`: http, https, file, data, blob e ogni altro sono vietati."""
    return url.scheme() == APP_SCHEME and url.host() == APP_HOST


def register_app_scheme() -> None:
    """Registra lo schema dell'app (una volta per processo, prima della `QApplication`)."""
    global _registered  # noqa: PLW0603 - la registrazione dello schema e per processo
    if _registered:
        return
    scheme = QWebEngineUrlScheme(APP_SCHEME.encode("ascii"))
    scheme.setSyntax(QWebEngineUrlScheme.Syntax.Host)
    scheme.setFlags(QWebEngineUrlScheme.Flag.SecureScheme)
    QWebEngineUrlScheme.registerScheme(scheme)
    _registered = True
