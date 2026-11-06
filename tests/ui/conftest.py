# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Configurazione dei test dell'interfaccia."""

from __future__ import annotations

import threading
from collections.abc import Iterator

import pytest

_JOIN_WAIT_S = 30.0


@pytest.fixture(autouse=True)
def no_garmin_threads_left() -> Iterator[None]:
    """Nessun thread dell'account Garmin resta in esecuzione tra un test e l'altro.

    Tocca alla finestra chiusa (o al cancello aperto dal test) farli finire: qui si attende
    e, se uno è ancora vivo, il test fallisce invece di lasciarlo girare nel successivo.
    """
    yield
    leftovers = [t for t in threading.enumerate() if t.name.startswith("ponos-garmin-")]
    for thread in leftovers:
        thread.join(_JOIN_WAIT_S)
    assert not [t for t in leftovers if t.is_alive()], "un thread dell'account non è finito"


@pytest.fixture
def gate() -> Iterator[threading.Event]:
    """Un cancello che a fine test si apre sempre: nessun thread resta fermo."""
    event = threading.Event()
    yield event
    event.set()
