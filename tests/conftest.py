# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Common test configuration: hypothesis profiles and markers."""

import gc
import os
from collections.abc import Iterator

import pytest

# Qt without screen and without GPU: must be decided before Qt is loaded.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu")

from hypothesis import HealthCheck, settings

from ponos_coach.ui.bootstrap import prepare_qt
from ponos_coach.ui.data_thread import DataThread
from ponos_coach.ui.main_thread_gc import collect_due

# App URL scheme and web engine attributes: before pytest-qt's QApplication.
prepare_qt()
_THRESHOLDS = gc.get_threshold()  # of the automatic collector, before any test changes them

# No per-example deadline: tests must be deterministic in CI.
settings.register_profile(
    "ci",
    max_examples=30,  # every check; the long campaign (`collaudo`) has 5000
    deadline=None,
    derandomize=True,
    suppress_health_check=[HealthCheck.too_slow],
)
settings.register_profile("collaudo", max_examples=5000, deadline=None)
settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "ci"))


@pytest.fixture(autouse=True)
def stop_data_threads() -> Iterator[None]:
    """No data thread keeps running between one test and the next, nor at process end.

    The window closes in two phases (without waiting): `qtbot` closes and destroys the windows
    immediately, so the thread may still be midway through closing the profile. Destroying a
    running `QThread` aborts the process.
    """
    yield
    assert DataThread.stop_all(30_000), "a data thread did not stop"


@pytest.fixture(autouse=True)
def collect_cycles_on_the_main_thread() -> Iterator[None]:
    """As in the application, the automatic garbage collector is off and cycles are collected
    on the main thread only (`ui/main_thread_gc.py`): a data thread that triggered a collection
    could destroy there a Qt object of a closed window of an earlier test, and Qt aborts.

    The tests do not always run the event loop long enough for the collector's timer, so after
    each test what is due is collected here, on the main thread, with the usual thresholds
    (a full collection after every test would double the time of the suite).
    """
    gc.disable()
    yield
    collect_due(_THRESHOLDS)
