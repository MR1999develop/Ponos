# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Preparazione di Qt da fare una volta, prima di creare la `QApplication`."""

from __future__ import annotations

from PySide6.QtCore import QCoreApplication, Qt
from PySide6.QtWebEngineWidgets import QWebEngineView  # noqa: F401 - da importare prima dell'app

from ponos_coach.ui.web_policy import register_app_scheme

_prepared = False


def prepare_qt() -> None:
    """Registra lo schema URL dell'app e gli attributi richiesti dal motore web."""
    global _prepared  # noqa: PLW0603 - la preparazione e per processo
    if _prepared:
        return
    register_app_scheme()
    QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
    _prepared = True
