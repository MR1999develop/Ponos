# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Finestra degli orari ammessi [1990-01-01, 2100-01-01) UTC (D-F1-04, R1, R11)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest
from hypothesis import given
from hypothesis import strategies as st

from ponos_coach.activities.domain import (
    MAX_ACTIVITY_INSTANT,
    MIN_ACTIVITY_INSTANT,
    Activity,
    ActivitySummary,
    FileFormat,
    Sport,
    is_plausible_instant,
    is_plausible_span,
)
from ponos_coach.activities.fit_decoder import FitDecodeError, FitDecodeReason, decode_activity
from ponos_coach.activities.gpx_decoder import GpxDecodeError, decode_gpx
from tests.builders import fit_identity
from tests.fixtures.generators import fit_writer as fw
from tests.fixtures.generators import gpx_writer as gw

LAST_SECOND = datetime(2099, 12, 31, 23, 59, 59, tzinfo=UTC)
ARABIC_INDIC_DIGITS = "٢٠٢٦-٠٣-٠٢"  # 2026-03-02


def gpx_at(start: datetime, count: int = 30) -> bytes:
    points = gw.line_points(start, count, step_s=10, lat_step_deg=0.0001)
    return gw.build_gpx(gw.GpxSpec([points]))


def fit_at(start: datetime, seconds: int = 60) -> bytes:
    first = fw.datetime_to_fit(start)
    samples = [fw.Sample(first + i, 120) for i in range(seconds)]
    spec = fw.ActivitySpec(start, fw.Sport.RUNNING, fw.SubSport.GENERIC, samples)
    return fw.build_activity(spec)


# --- dominio: on e off point ---------------------------------------------------------------


def test_the_window_is_closed_at_the_start_and_open_at_the_end() -> None:
    assert is_plausible_instant(MIN_ACTIVITY_INSTANT)
    assert not is_plausible_instant(MIN_ACTIVITY_INSTANT - timedelta(microseconds=1))
    assert is_plausible_instant(MAX_ACTIVITY_INSTANT - timedelta(microseconds=1))
    assert not is_plausible_instant(MAX_ACTIVITY_INSTANT)


def test_a_span_needs_both_ends_inside_and_survives_unrepresentable_ends() -> None:
    assert is_plausible_span(MAX_ACTIVITY_INSTANT - timedelta(seconds=10), 9.0)
    assert not is_plausible_span(MAX_ACTIVITY_INSTANT - timedelta(seconds=10), 10.0)
    assert not is_plausible_span(datetime(9999, 12, 31, tzinfo=UTC), 1e12)
    assert not is_plausible_span(MIN_ACTIVITY_INSTANT, 1e300)


@given(st.datetimes(min_value=datetime(1, 1, 2), max_value=datetime(9998, 1, 1)))
def test_the_window_agrees_with_a_naive_comparison_in_any_zone(naive: datetime) -> None:
    moment = naive.replace(tzinfo=timezone(timedelta(hours=-5)))
    utc_naive = naive + timedelta(hours=5)

    expected = datetime(1990, 1, 1) <= utc_naive < datetime(2100, 1, 1)

    assert is_plausible_instant(moment) is expected


def test_an_activity_outside_the_window_cannot_exist() -> None:
    def make(start: datetime, elapsed_s: float = 100.0) -> Activity:
        return Activity(
            fit_identity(),
            FileFormat.FIT,
            Sport.RUNNING,
            start,
            elapsed_s,
            elapsed_s,
            None,
            ActivitySummary(),
        )

    make(MIN_ACTIVITY_INSTANT)
    with pytest.raises(ValueError, match="finestra"):
        make(datetime(1, 1, 1, tzinfo=UTC))
    with pytest.raises(ValueError, match="finestra"):
        make(LAST_SECOND, 5.0)


# --- GPX -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "start",
    [datetime(1, 1, 1, tzinfo=UTC), datetime(1989, 12, 31, 23, 59, 20, tzinfo=UTC)],
    ids=["anno-1", "prima-del-1990"],
)
def test_gpx_starting_before_the_window_is_corrupt(start: datetime) -> None:
    with pytest.raises(GpxDecodeError) as caught:
        decode_gpx(gpx_at(start))

    assert caught.value.reason is FitDecodeReason.CORRUPT


def test_gpx_ending_after_the_window_is_corrupt_but_one_ending_inside_is_valid() -> None:
    with pytest.raises(GpxDecodeError):
        decode_gpx(gpx_at(LAST_SECOND - timedelta(seconds=20), count=5))  # finisce nel 2100

    assert decode_gpx(gpx_at(LAST_SECOND - timedelta(seconds=100), count=5)).start.year == 2099
    assert decode_gpx(gpx_at(MIN_ACTIVITY_INSTANT)).start == MIN_ACTIVITY_INSTANT


@pytest.mark.parametrize("stamp", ["0001-01-01T00:00:00+05:00", "9999-12-31T23:59:59-05:00"])
def test_gpx_times_that_overflow_on_conversion_are_corrupt_not_a_crash(stamp: str) -> None:
    data = gpx_at(datetime(2026, 3, 2, 7, 0, tzinfo=UTC), count=3).decode()
    first = data.index("<time>") + len("<time>")
    data = data[:first] + stamp + data[data.index("</time>", first) :]

    with pytest.raises(GpxDecodeError) as caught:
        decode_gpx(data.encode())

    assert caught.value.reason is FitDecodeReason.CORRUPT


def test_non_ascii_digits_in_a_time_are_corrupt() -> None:
    data = gpx_at(datetime(2026, 3, 2, 7, 0, tzinfo=UTC), count=3).decode()
    data = data.replace("2026-03-02", ARABIC_INDIC_DIGITS, 1)

    with pytest.raises(GpxDecodeError) as caught:
        decode_gpx(data.encode())

    assert caught.value.reason is FitDecodeReason.CORRUPT


# --- FIT -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "start",
    [datetime(2110, 1, 1, tzinfo=UTC), datetime(1989, 12, 31, 0, 0, 1, tzinfo=UTC), LAST_SECOND],
    ids=["dopo-il-2100", "prima-del-1990", "finisce-oltre-il-2100"],
)
def test_fit_outside_the_window_is_corrupt(start: datetime) -> None:
    with pytest.raises(FitDecodeError) as caught:
        decode_activity(fit_at(start))

    assert caught.value.reason is FitDecodeReason.CORRUPT


def test_fit_at_the_edges_of_the_window_is_valid() -> None:
    # Sotto 0x10000000 s (luglio 2010) un timestamp FIT non è una data (tempo di sistema):
    # il bordo basso della finestra vale per il GPX, qui basta un inizio vicino ad esso.
    early = datetime(2010, 7, 6, tzinfo=UTC)
    assert decode_activity(fit_at(early)).start == early
    assert decode_activity(fit_at(LAST_SECOND - timedelta(seconds=100))).start.year == 2099
