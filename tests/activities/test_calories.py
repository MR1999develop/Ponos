# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Calories of an activity: FIT `session.total_calories` into the summary (T-NUT-03)."""

from __future__ import annotations

import math
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from ponos_coach.activities.domain import MAX_ACTIVITY_KCAL, ActivitySummary
from ponos_coach.activities.fit_decoder import decode_activity
from tests.fixtures.generators import fit_writer as fw

START = datetime(2026, 3, 2, 7, 0, tzinfo=UTC)


def spec(**changes: object) -> fw.ActivitySpec:
    first = fw.datetime_to_fit(START)
    samples = [fw.Sample(first + i, 120 + i) for i in range(5)]
    base = fw.ActivitySpec(START, fw.Sport.RUNNING, fw.SubSport.GENERIC, samples)
    return replace(base, **changes)  # type: ignore[arg-type]


def calories_of(activity_spec: fw.ActivitySpec) -> float | None:
    return decode_activity(fw.build_activity(activity_spec)).summary.calories_kcal


def test_the_session_total_calories_end_up_in_the_summary() -> None:
    assert calories_of(spec(total_calories_kcal=612)) == 612.0


def test_a_file_without_calories_has_none() -> None:
    assert calories_of(spec()) is None


@pytest.mark.parametrize("raw", [0, 0xFFFF])
def test_zero_and_the_fit_invalid_value_mean_not_computed(raw: int) -> None:
    assert calories_of(spec(total_calories_kcal=raw)) is None


def test_the_largest_valid_value_that_fits_the_cap_is_kept() -> None:
    assert calories_of(spec(total_calories_kcal=0xFFFE)) is None  # 65534 > 50000: not credible
    assert calories_of(spec(total_calories_kcal=50_000)) == 50_000.0


def test_other_summary_fields_are_untouched_by_calories() -> None:
    activity = decode_activity(
        fw.build_activity(spec(total_calories_kcal=500, total_distance_m=1234.5, total_ascent_m=7))
    )

    assert activity.summary.calories_kcal == 500.0
    assert activity.summary.distance_m == pytest.approx(1234.5)
    assert activity.summary.ascent_m == 7.0


def test_a_summary_keeps_valid_calories_and_defaults_to_none() -> None:
    assert ActivitySummary(calories_kcal=1.0).calories_kcal == 1.0
    assert ActivitySummary(calories_kcal=MAX_ACTIVITY_KCAL).calories_kcal == MAX_ACTIVITY_KCAL
    assert ActivitySummary().calories_kcal is None


@pytest.mark.parametrize(
    "kcal", [0.0, -1.0, MAX_ACTIVITY_KCAL + 0.1, math.inf, -math.inf, math.nan]
)
def test_a_summary_refuses_calories_that_are_not_credible(kcal: float) -> None:
    with pytest.raises(ValueError, match="calories"):
        ActivitySummary(calories_kcal=kcal)
