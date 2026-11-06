# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Configurazione comune dei test: profili hypothesis e marker."""

import os
from collections.abc import Iterator

import pytest

# Qt senza schermo e senza GPU: va deciso prima che Qt venga caricato.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu")

from hypothesis import HealthCheck, settings

from ponos_coach.ui.bootstrap import prepare_qt
from ponos_coach.ui.data_thread import DataThread

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


@pytest.fixture(autouse=True)
def stop_data_threads() -> Iterator[None]:
    """Nessun thread dei dati resta in esecuzione tra un test e l'altro né a fine processo.

    La finestra chiude in due tempi (senza attendere): `qtbot` chiude e distrugge le finestre
    subito, quindi il thread può essere ancora a metà della chiusura del profilo. Distruggere un
    `QThread` in esecuzione abortisce il processo.
    """
    yield
    assert DataThread.stop_all(30_000), "un thread dei dati non si è fermato"
