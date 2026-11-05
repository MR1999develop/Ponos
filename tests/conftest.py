# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Configurazione comune dei test: profili hypothesis e marker."""

import os

# Qt senza schermo e senza GPU: va deciso prima che Qt venga caricato.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu")

from hypothesis import HealthCheck, settings

from ponos_coach.ui.bootstrap import prepare_qt

# Schema URL dell'app e attributi del motore web: prima della QApplication di pytest-qt.
prepare_qt()

# Nessuna scadenza per esempio: i test devono essere deterministici in CI.
settings.register_profile(
    "ci",
    max_examples=100,
    deadline=None,
    derandomize=True,
    suppress_health_check=[HealthCheck.too_slow],
)
settings.register_profile("collaudo", max_examples=5000, deadline=None)
settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "ci"))
