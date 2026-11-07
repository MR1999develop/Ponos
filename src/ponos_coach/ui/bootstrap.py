# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Qt preparation to do once, before creating the `QApplication`."""

from __future__ import annotations

from PySide6.QtCore import QCoreApplication, Qt
from PySide6.QtWebEngineWidgets import QWebEngineView  # noqa: F401 - must be imported before the app

from ponos_coach.ui.web_policy import register_app_scheme

_prepared = False


def prepare_qt() -> None:
    """Register the app's URL scheme and the attributes the web engine requires."""
    global _prepared  # noqa: PLW0603 - preparation is per process
    if _prepared:
        return
    register_app_scheme()
    QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
    _prepared = True
