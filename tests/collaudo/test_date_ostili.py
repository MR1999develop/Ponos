# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""T-SEC-02 e T-PERF-02: date ostili in file formalmente validi non devono gonfiare le risorse."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ponos_coach.activities.use_cases import ImportStatus
from tests.collaudo.support import RealProfile, create_profile
from tests.fixtures.generators import gpx_writer as gw

MAX_PLAUSIBLE_DAYS = 366 * 100  # nessun atleta ha un secolo di storia


@pytest.fixture
def profile(tmp_path: Path) -> Iterator[RealProfile]:
    opened = create_profile(tmp_path)
    yield opened
    opened.close()


def gpx_starting_at(start: datetime) -> bytes:
    points = gw.line_points(start, 60, step_s=10, lat_step_deg=0.0001)
    return gw.build_gpx(gw.GpxSpec([points]))


def test_a_plausible_old_clock_gives_a_bounded_series(tmp_path: Path, profile: RealProfile) -> None:
    (tmp_path / "vecchio.gpx").write_bytes(gpx_starting_at(datetime(1995, 6, 1, tzinfo=UTC)))

    summary = profile.import_files([tmp_path / "vecchio.gpx"])

    assert summary.count(ImportStatus.IMPORTED) == 1
    assert len(profile.overview().pmc) < MAX_PLAUSIBLE_DAYS


@pytest.mark.xfail(
    strict=True,
    reason="D-F1-04: un GPX datato 0001-01-01 si importa e il PMC copre 740.000 giorni; la "
    "figura impiega circa 20 s nel thread della UI e la pagina riceve circa 70 MB",
)
def test_d_f1_04_a_gpx_dated_year_1_is_refused_or_does_not_make_the_series_span_centuries(
    tmp_path: Path, profile: RealProfile
) -> None:
    (tmp_path / "anno1.gpx").write_bytes(gpx_starting_at(datetime(1, 1, 1, tzinfo=UTC)))

    summary = profile.import_files([tmp_path / "anno1.gpx"])

    if summary.count(ImportStatus.IMPORTED) == 0:
        return  # rifiutato: va bene
    assert len(profile.overview().pmc) < MAX_PLAUSIBLE_DAYS
