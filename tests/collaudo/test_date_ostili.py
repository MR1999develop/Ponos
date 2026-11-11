# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""T-SEC-02 and T-PERF-02: hostile dates in formally valid files must not inflate resources."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ponos_coach.activities.use_cases import ImportStatus
from tests.collaudo.support import RealProfile, create_profile
from tests.fixtures.generators import fit_writer as fw

MAX_PLAUSIBLE_DAYS = 366 * 100  # no athlete has a century of history


@pytest.fixture
def profile(tmp_path: Path) -> Iterator[RealProfile]:
    opened = create_profile(tmp_path)
    yield opened
    opened.close()


def fit_starting_at(start: datetime) -> bytes:
    first = fw.datetime_to_fit(start)
    samples = [fw.Sample(first + i * 10, 130, lat_deg=0.0001 * i, lon_deg=0.0) for i in range(60)]
    spec = fw.ActivitySpec(start, fw.Sport.RUNNING, fw.SubSport.GENERIC, samples)
    return fw.build_activity(spec)


def test_a_plausible_old_clock_gives_a_bounded_series(tmp_path: Path, profile: RealProfile) -> None:
    (tmp_path / "vecchio.fit").write_bytes(fit_starting_at(datetime(2010, 7, 6, tzinfo=UTC)))

    summary = profile.import_files([tmp_path / "vecchio.fit"])

    assert summary.count(ImportStatus.IMPORTED) == 1
    assert len(profile.overview().pmc) < MAX_PLAUSIBLE_DAYS


def test_d_f1_04_a_fit_with_a_system_time_clock_is_refused_or_gives_a_bounded_series(
    tmp_path: Path, profile: RealProfile
) -> None:
    (tmp_path / "anno1995.fit").write_bytes(fit_starting_at(datetime(1995, 6, 1, tzinfo=UTC)))

    summary = profile.import_files([tmp_path / "anno1995.fit"])

    if summary.count(ImportStatus.IMPORTED) == 0:
        return  # refused: fine
    assert len(profile.overview().pmc) < MAX_PLAUSIBLE_DAYS
