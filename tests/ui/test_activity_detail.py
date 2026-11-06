# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Dettaglio di un'attività: righe delle zone e messaggi dei file non disponibili."""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pytest

from ponos_coach.activities.domain import (
    ActivitySummary,
    FileId,
    FitIdentity,
    HeartRateSeries,
    Sport,
)
from ponos_coach.load.activity_detail import ActivityDetail, HeartRateTrace
from ponos_coach.load.domain import (
    ActivityLoad,
    HeartRateLimits,
    LoadSource,
    ZoneSeconds,
    zone_starts_bpm,
)
from ponos_coach.load.use_cases import ExclusionReason
from ponos_coach.ui.activity_detail import zone_rows
from ponos_coach.ui.errors import unavailable_detail_message

START = datetime(2026, 5, 1, 7, 0, tzinfo=UTC)


def detail_with(zones: ZoneSeconds) -> ActivityDetail:
    series = HeartRateSeries(np.array([0.0, 10.0]), np.array([120.0, 130.0]))
    limits = HeartRateLimits(50, 190)
    load = ActivityLoad(10.0, 10.0, 10.0, 2, 0, 0.0, LoadSource.HEART_RATE, zones)
    return ActivityDetail(
        identity=FitIdentity(FileId(1, 1, 1, START)),
        sport=Sport.RUNNING,
        start=START,
        local_day=START.date(),
        elapsed_s=20.0,
        timer_s=20.0,
        summary=ActivitySummary(),
        load=load,
        trace=HeartRateTrace.of(series, limits),
        limits=limits,
        threshold_bpm=171,
        zone_starts_bpm=zone_starts_bpm(171),
        unordered_hr_samples=0,
    )


def test_one_row_per_zone_with_the_share_of_the_time_in_zones() -> None:
    rows = zone_rows(detail_with(ZoneSeconds((300.0, 100.0, 0.0, 0.0, 100.0))))

    assert [row.label.split()[0] for row in rows] == ["Z1", "Z2", "Z3", "Z4", "Z5"]
    assert [row.seconds for row in rows] == [300.0, 100.0, 0.0, 0.0, 100.0]
    assert [row.share for row in rows] == pytest.approx([0.6, 0.2, 0.0, 0.0, 0.2])
    assert sum(row.share for row in rows) == pytest.approx(1.0)


def test_without_time_in_zones_every_share_is_zero_not_a_division_error() -> None:
    rows = zone_rows(detail_with(ZoneSeconds.ZERO))

    assert [row.share for row in rows] == [0.0] * 5


@pytest.mark.parametrize("reason", list(ExclusionReason))
def test_every_unavailable_file_has_a_message_with_what_and_what_to_do(
    reason: ExclusionReason,
) -> None:
    message = unavailable_detail_message(reason)

    assert message.title == "The activity detail is not available"
    assert message.what
    assert message.remedy
    assert message.what.endswith(".")
    assert "Traceback" not in message.what + message.remedy


def test_the_messages_do_not_depend_on_a_path_or_a_file_name() -> None:
    texts = {
        unavailable_detail_message(reason).what + unavailable_detail_message(reason).remedy
        for reason in ExclusionReason
    }

    assert not any("\\" in text or "/" in text or ".fit" in text for text in texts)
