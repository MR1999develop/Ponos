# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Interface test configuration."""

from __future__ import annotations

import threading
from collections.abc import Iterator

import pytest

_JOIN_WAIT_S = 30.0


@pytest.fixture(autouse=True)
def no_garmin_threads_left() -> Iterator[None]:
    """No Garmin account thread keeps running between one test and the next.

    It is up to the closed window (or the gate opened by the test) to make them finish: here we wait
    and, if one is still alive, the test fails instead of letting it run into the next.
    """
    yield
    leftovers = [t for t in threading.enumerate() if t.name.startswith("ponos-garmin-")]
    for thread in leftovers:
        thread.join(_JOIN_WAIT_S)
    assert not [t for t in leftovers if t.is_alive()], "an account thread did not finish"


@pytest.fixture
def gate() -> Iterator[threading.Event]:
    """A gate that always opens at the end of the test: no thread stays stuck."""
    event = threading.Event()
    yield event
    event.set()
