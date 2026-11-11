# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Distinct FIT activities for the import tests (generated on the fly, not versioned).

`fit_run(n)` gives a different run for each number (different start, serial number and
positions).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from . import fit_writer as fw
from .tracks import SEA_LAT_DEG, SEA_LON_DEG

FIRST_START = datetime(2026, 5, 1, 7, 0, tzinfo=UTC)
RUN_SECONDS = 1800
STEP_S = 10  # one sample every 10 s: small files


def run_start(number: int) -> datetime:
    """Start of run number `number`: one per day, at the same time."""
    return FIRST_START + timedelta(days=number)


def _heart_rate(index: int) -> int:
    return 130 + index % 20


def fit_run(number: int, *, seconds: int = RUN_SECONDS) -> bytes:
    """FIT of a run of `seconds` seconds, one sample every 10 s, with position and HR."""
    start = run_start(number)
    first = fw.datetime_to_fit(start)
    samples = [
        fw.Sample(
            first + i * STEP_S,
            _heart_rate(i),
            lat_deg=SEA_LAT_DEG + i * 0.0001,
            lon_deg=SEA_LON_DEG,
            altitude_m=10.0,
        )
        for i in range(seconds // STEP_S + 1)
    ]
    spec = fw.ActivitySpec(
        start,
        fw.Sport.RUNNING,
        fw.SubSport.GENERIC,
        samples,
        serial_number=2_000_000 + number,
        total_elapsed_ms=seconds * 1000,
        total_timer_ms=seconds * 1000,
    )
    return fw.build_activity(spec)
