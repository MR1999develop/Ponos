# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Activity detail: zone rows and messages for unavailable files."""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pytest
from PySide6.QtCore import Qt

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
    DomainSeconds,
    HeartRateLimits,
    LoadSource,
    ZoneSeconds,
    zone_starts_bpm,
)
from ponos_coach.load.use_cases import ExclusionReason
from ponos_coach.ui.activity_detail import DomainKind, domain_rows, zone_rows
from ponos_coach.ui.activity_detail_dialog import DomainTableModel, ZoneTableModel
from ponos_coach.ui.errors import unavailable_detail_message

DASH = "–"  # noqa: RUF001 - the separator itself
START = datetime(2026, 5, 1, 7, 0, tzinfo=UTC)


def detail_with(
    zones: ZoneSeconds, domains: DomainSeconds | None = None, lt1: int = 142
) -> ActivityDetail:
    series = HeartRateSeries(np.array([0.0, 10.0]), np.array([120.0, 130.0]))
    limits = HeartRateLimits(50, 190)
    load = ActivityLoad(10.0, 2, 0, 0.0, LoadSource.HEART_RATE, zones)
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
        lt1_bpm=lt1,
        zone_starts_bpm=zone_starts_bpm(171),
        unordered_hr_samples=0,
        domain_seconds=domains,
    )


def test_one_row_per_zone_with_the_share_of_the_time_in_zones() -> None:
    rows = zone_rows(detail_with(ZoneSeconds((300.0, 100.0, 0.0, 0.0, 100.0))))

    assert [row.label.split()[0] for row in rows] == ["Z1", "Z2", "Z3", "Z4", "Z5"]
    assert [row.seconds for row in rows] == [300.0, 100.0, 0.0, 0.0, 100.0]
    assert [row.share for row in rows] == pytest.approx([0.6, 0.2, 0.0, 0.0, 0.2])
    assert sum(row.share for row in rows) == pytest.approx(1.0)


def test_each_zone_row_has_its_whole_bpm_range_of_the_day() -> None:
    rows = zone_rows(detail_with(ZoneSeconds.ZERO))  # threshold 171, limits 50 to 190

    assert [row.bpm for row in rows] == [
        (50, 133),
        (134, 148),
        (149, 160),
        (161, 170),
        (171, 190),
    ]


def test_the_zone_table_shows_the_bpm_range_next_to_the_zone(qtbot: object) -> None:
    del qtbot  # the model needs the application, which pytest-qt creates
    model = ZoneTableModel()
    model.set_rows(zone_rows(detail_with(ZoneSeconds((300.0, 100.0, 0.0, 0.0, 100.0)))))

    assert model.columnCount() == 5
    assert model.headerData(0, Qt.Orientation.Horizontal) == "Zone (% of threshold)"
    assert model.headerData(1, Qt.Orientation.Horizontal) == "Heart rate (bpm)"
    assert model.data(model.index(0, 1)) == f"50{chr(0x2013)}133"
    assert model.data(model.index(4, 1)) == f"171{chr(0x2013)}190"
    assert model.data(model.index(1, 1), Qt.ItemDataRole.TextAlignmentRole) == int(
        Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
    )


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


# --- The three domains ---------------------------------------------------------------------


def test_one_row_per_domain_with_the_share_of_the_time() -> None:
    rows = domain_rows(detail_with(ZoneSeconds.ZERO, DomainSeconds(300.0, 100.0, 100.0)))

    assert [row.kind for row in rows] == [
        DomainKind.BELOW_LT1,
        DomainKind.BETWEEN,
        DomainKind.ABOVE_LT2,
    ]
    assert [row.seconds for row in rows] == [300.0, 100.0, 100.0]
    assert [row.share for row in rows] == pytest.approx([0.6, 0.2, 0.2])


def test_each_domain_row_has_its_whole_bpm_range_of_the_day() -> None:
    rows = domain_rows(detail_with(ZoneSeconds.ZERO, DomainSeconds(1.0, 1.0, 1.0)))

    # limits 50 to 190, LT1 142, LT2 171: below up to 141, between 142 to 170, above 171 up
    assert [row.bpm for row in rows] == [(50, 141), (142, 170), (171, 190)]


def test_a_domain_with_no_whole_bpm_inside_the_limits_has_no_range() -> None:
    rows = domain_rows(detail_with(ZoneSeconds.ZERO, DomainSeconds(1.0, 1.0, 1.0), lt1=50))

    assert rows[0].bpm is None  # nothing below an LT1 equal to the resting rate


def test_without_heart_rate_the_domains_are_empty() -> None:
    rows = domain_rows(detail_with(ZoneSeconds.ZERO, None))

    assert [row.seconds for row in rows] == [0.0, 0.0, 0.0]
    assert [row.share for row in rows] == [0.0, 0.0, 0.0]


def test_the_domain_model_shows_name_range_time_and_share() -> None:
    model = DomainTableModel()
    model.set_rows(domain_rows(detail_with(ZoneSeconds.ZERO, DomainSeconds(300.0, 100.0, 100.0))))

    cells = [
        [model.data(model.index(row, column)) for column in range(model.columnCount())]
        for row in range(model.rowCount())
    ]

    assert cells == [
        ["Below LT1", f"50{DASH}141", "0:05:00", "60"],
        ["Between LT1 and LT2", f"142{DASH}170", "0:01:40", "20"],
        ["Above LT2", f"171{DASH}190", "0:01:40", "20"],
    ]
