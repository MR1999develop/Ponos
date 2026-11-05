# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Confine FIT: file validi, file ostili, limiti, mappatura degli sport."""

from __future__ import annotations

import contextlib
import math
import struct
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from ponos_coach.activities.domain import (
    MAX_TRACK_POINTS,
    FileFormat,
    FitIdentity,
    Sport,
    decode_polyline,
)
from ponos_coach.activities.fit_decoder import (
    FitDecodeError,
    FitDecodeReason,
    FitLimits,
    decode_activity,
    map_sport,
)
from tests.fixtures.generators import fit_writer as fw

DATA = Path(__file__).resolve().parents[1] / "fixtures" / "data"
HOSTILE_DIR = DATA / "fit-ostili"
RUN_START = datetime(2026, 3, 2, 7, 0, tzinfo=UTC)


def valid_bytes(name: str = "run_constant_160.fit") -> bytes:
    return (DATA / "fit" / name).read_bytes()


def small_activity() -> fw.ActivitySpec:
    start = datetime(2026, 3, 2, 7, 0, tzinfo=UTC)
    samples = [fw.Sample(fw.datetime_to_fit(start) + i, 120 + i) for i in range(5)]
    return fw.ActivitySpec(start, fw.Sport.RUNNING, fw.SubSport.GENERIC, samples)


def reason_of(data: bytes, limits: FitLimits | None = None) -> FitDecodeReason:
    with pytest.raises(FitDecodeError) as caught:
        decode_activity(data, limits)
    return caught.value.reason


# --- File validi -------------------------------------------------------------


def test_valid_run_becomes_an_activity() -> None:
    activity = decode_activity(valid_bytes())

    assert activity.sport is Sport.RUNNING
    assert activity.start == RUN_START
    assert activity.elapsed_s == 3600.0
    assert activity.file_format is FileFormat.FIT
    assert isinstance(activity.identity, FitIdentity)
    assert activity.identity.file_id.serial_number == 5000001
    assert activity.identity.file_id.manufacturer == fw.MANUFACTURER_DEVELOPMENT
    assert activity.identity.file_id.time_created == RUN_START
    assert activity.heart_rate is not None
    assert len(activity.heart_rate) == 3600
    assert activity.heart_rate.seconds[-1] == 3599.0
    assert set(activity.heart_rate.bpm) == {160.0}


def test_copy_has_the_same_identity() -> None:
    assert (
        decode_activity(valid_bytes()).identity
        == decode_activity(valid_bytes("copy_of_run_constant_160.fit")).identity
    )


def test_activity_without_heart_rate_has_no_series() -> None:
    activity = decode_activity(valid_bytes("walk_no_hr.fit"))

    assert activity.sport is Sport.WALKING
    assert activity.heart_rate is None


def test_invalid_heart_rate_samples_are_omitted_not_repaired() -> None:
    activity = decode_activity(valid_bytes("run_hr_gaps.fit"))

    assert activity.heart_rate is not None
    assert len(activity.heart_rate) == 1800 - 12 - 15
    assert activity.elapsed_s == 1800.0


def test_out_of_range_values_are_kept_in_the_series() -> None:
    activity = decode_activity(valid_bytes("run_hr_out_of_range.fit"))

    assert activity.heart_rate is not None
    assert 0.0 in activity.heart_rate.bpm
    assert 250.0 in activity.heart_rate.bpm


def test_one_second_and_twenty_four_hours_are_accepted() -> None:
    assert decode_activity(valid_bytes("run_one_second.fit")).elapsed_s == 1.0
    assert decode_activity(valid_bytes("hike_24h.fit")).elapsed_s == 86_400.0


def test_timer_time_is_the_moving_time_and_elapsed_includes_the_pauses() -> None:
    spec = replace(small_activity(), total_elapsed_ms=3_600_000, total_timer_ms=2_400_000)

    activity = decode_activity(fw.build_activity(spec))

    assert (activity.elapsed_s, activity.timer_s) == (3600.0, 2400.0)


def test_missing_timer_time_falls_back_to_elapsed_time() -> None:
    spec = replace(small_activity(), total_elapsed_ms=90_000, total_timer_ms=0xFFFFFFFF)

    activity = decode_activity(fw.build_activity(spec))

    assert (activity.elapsed_s, activity.timer_s) == (90.0, 90.0)


def test_non_positive_timer_time_is_a_corrupt_file() -> None:
    spec = replace(small_activity(), total_timer_ms=0)

    assert reason_of(fw.build_activity(spec)) is FitDecodeReason.CORRUPT


def hr_samples(offsets: list[int]) -> fw.ActivitySpec:
    spec = small_activity()
    first = fw.datetime_to_fit(spec.start)
    return replace(spec, samples=[fw.Sample(first + o, 100 + o) for o in offsets])


def test_repeated_heart_rate_timestamps_are_discarded_keeping_the_first() -> None:
    spec = replace(hr_samples([0, 1, 1, 2, 2, 2, 3]), total_elapsed_ms=4000, total_timer_ms=4000)

    activity = decode_activity(fw.build_activity(spec))

    assert activity.heart_rate is not None
    assert list(activity.heart_rate.seconds) == [0.0, 1.0, 2.0, 3.0]
    assert activity.discarded_hr_samples == 3


def test_backwards_heart_rate_timestamps_are_discarded_not_corrupt() -> None:
    spec = replace(hr_samples([0, 1, 5, 3, 4, 6, 2]), total_elapsed_ms=7000, total_timer_ms=7000)

    activity = decode_activity(fw.build_activity(spec))

    assert activity.heart_rate is not None
    assert list(activity.heart_rate.seconds) == [0.0, 1.0, 5.0, 6.0]
    assert list(activity.heart_rate.bpm) == [100.0, 101.0, 105.0, 106.0]
    assert activity.discarded_hr_samples == 3


def test_clean_heart_rate_has_no_discarded_samples() -> None:
    assert decode_activity(valid_bytes()).discarded_hr_samples == 0


def test_a_programming_error_is_not_swallowed(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(*_args: object) -> None:
        raise RuntimeError("bug nostro")

    monkeypatch.setattr("ponos_coach.activities.fit_decoder._build", broken)

    with pytest.raises(RuntimeError, match="bug nostro"):
        decode_activity(valid_bytes())


# --- File ostili -------------------------------------------------------------

HOSTILE_FILES = sorted(HOSTILE_DIR.glob("*.fit"))


def test_hostile_fixtures_are_present() -> None:
    assert len(HOSTILE_FILES) == 13


@pytest.mark.parametrize("path", HOSTILE_FILES, ids=lambda p: p.name)
def test_every_hostile_file_is_rejected_with_a_single_error_type(path: Path) -> None:
    expected = FitDecodeReason.EMPTY if path.name == "empty.fit" else FitDecodeReason.CORRUPT

    assert reason_of(path.read_bytes()) is expected


def test_empty_input_is_empty() -> None:
    assert reason_of(b"") is FitDecodeReason.EMPTY


def test_garbage_is_corrupt() -> None:
    assert reason_of(b"questo non e un file FIT" * 10) is FitDecodeReason.CORRUPT


def test_file_that_is_not_an_activity() -> None:
    body = bytearray(fw.build_body(small_activity()))
    body[22] = 5  # tipo del file_id: 4 = attività
    assert reason_of(fw.wrap_body(bytes(body))) is FitDecodeReason.NOT_AN_ACTIVITY


def test_file_with_only_file_id_is_not_an_activity() -> None:
    body = fw.build_body(small_activity())[:35]  # definizione + dati del file_id
    assert reason_of(fw.wrap_body(body)) is FitDecodeReason.NOT_AN_ACTIVITY


@given(st.data())
def test_mutated_valid_file_gives_an_activity_or_the_single_error(data: st.DataObject) -> None:
    original = fw.build_activity(small_activity())
    index = data.draw(st.integers(0, len(original) - 1))
    value = data.draw(st.integers(0, 255))
    cut = data.draw(st.integers(1, len(original)))
    mutated = bytearray(original)
    mutated[index] = value

    try:
        decode_activity(bytes(mutated[:cut]))
    except FitDecodeError as error:
        assert isinstance(error.reason, FitDecodeReason)


@given(st.binary(max_size=300))
def test_random_bytes_never_raise_other_exceptions(blob: bytes) -> None:
    with contextlib.suppress(FitDecodeError):
        decode_activity(blob)


# --- Limiti ------------------------------------------------------------------


def test_size_is_checked_before_decoding() -> None:
    # Byte non FIT: se si provasse a decodificarli il motivo sarebbe CORRUPT.
    assert reason_of(b"x" * 11, FitLimits(max_bytes=10)) is FitDecodeReason.TOO_LARGE


def test_size_at_the_limit_is_accepted() -> None:
    data = valid_bytes("run_one_second.fit")

    assert decode_activity(data, FitLimits(max_bytes=len(data))).elapsed_s == 1.0
    assert reason_of(data, FitLimits(max_bytes=len(data) - 1)) is FitDecodeReason.TOO_LARGE


def test_default_limits_are_the_project_defaults() -> None:
    limits = FitLimits()

    assert limits.max_bytes == 50 * 1024 * 1024
    assert limits.max_records == 1_000_000
    assert limits.max_duration_s == 7 * 86_400


def test_too_many_records() -> None:
    assert reason_of(valid_bytes(), FitLimits(max_records=3599)) is FitDecodeReason.TOO_MANY_RECORDS
    assert decode_activity(valid_bytes(), FitLimits(max_records=3600)).elapsed_s == 3600.0


def test_too_long() -> None:
    limits = FitLimits(max_duration_s=86_399.0)

    assert reason_of(valid_bytes("hike_24h.fit"), limits) is FitDecodeReason.TOO_LONG
    at_limit = decode_activity(valid_bytes("hike_24h.fit"), FitLimits(max_duration_s=86_400.0))
    assert at_limit.elapsed_s == 86_400.0


def test_total_messages_are_capped_even_when_they_are_not_records() -> None:
    start = datetime(2026, 3, 2, 7, 0, tzinfo=UTC)
    one_record = fw.ActivitySpec(
        start, fw.Sport.RUNNING, fw.SubSport.GENERIC, [fw.Sample(fw.datetime_to_fit(start), 120)]
    )
    extra_laps = (b"\x02" + struct.pack("<IIII", 0, 0, 1000, 1000)) * 10  # lap con definizione 2
    data = fw.wrap_body(fw.build_body(one_record) + extra_laps)

    assert decode_activity(data).elapsed_s == 1.0
    assert reason_of(data, FitLimits(max_records=2)) is FitDecodeReason.TOO_MANY_RECORDS


@pytest.mark.parametrize(
    ("max_bytes", "max_records", "max_duration"),
    [(0, 1, 1.0), (1, 0, 1.0), (1, 1, 0.0), (1, 1, float("inf")), (-1, 1, 1.0)],
)
def test_invalid_limits_are_rejected(max_bytes: int, max_records: int, max_duration: float) -> None:
    with pytest.raises(ValueError, match=r"limiti|durata"):
        FitLimits(max_bytes, max_records, max_duration)


# --- Mappatura degli sport ---------------------------------------------------


@pytest.mark.parametrize(
    ("sport", "sub_sport", "expected"),
    [
        (1, 0, Sport.RUNNING),
        (1, 1, Sport.RUNNING),
        (1, 3, Sport.TRAIL),
        (2, 7, Sport.CYCLING),
        (10, 20, Sport.STRENGTH),
        (10, 0, Sport.OTHER),
        (11, 0, Sport.WALKING),
        (17, 3, Sport.HIKING),
        (5, 0, Sport.OTHER),
        (None, None, Sport.OTHER),
        (1, None, Sport.RUNNING),
    ],
)
def test_sport_mapping(sport: int | None, sub_sport: int | None, expected: Sport) -> None:
    assert map_sport(sport, sub_sport) is expected


def test_header_with_correct_sizes_is_what_the_hostile_files_break() -> None:
    # Sanità del generatore: il file valido da cui derivano gli ostili si decodifica.
    valid = fw.build_activity(small_activity())
    assert decode_activity(valid).elapsed_s == 5.0
    assert struct.unpack_from("<I", valid, 4)[0] == len(valid) - fw.HEADER_SIZE - 2


# --- Riepilogo: distanza, dislivello, partenza e traccia ------------------------------------

SEA = (-30.0, -20.0)  # coordinate inventate, in mare aperto


def gps_activity(start_position: tuple[float, float] | None = None) -> fw.ActivitySpec:
    """Attività di 5 campioni con posizioni lungo un parallelo, a 3 m/s."""
    spec = small_activity()
    samples = [
        replace(s, lat_deg=SEA[0], lon_deg=SEA[1] + i * 3.0 / 96_300.0, distance_m=i * 3.0)
        for i, s in enumerate(spec.samples)
    ]
    return replace(spec, samples=samples, start_position=start_position)


def test_run_with_gps_has_distance_ascent_start_and_track() -> None:
    activity = decode_activity(valid_bytes("run_gps_loop.fit"))
    summary = activity.summary

    assert summary.distance_m == pytest.approx(3597.0)
    assert summary.ascent_m == 60.0
    assert summary.start_point is not None
    assert summary.start_point.lat_deg == pytest.approx(-30.0, abs=1e-6)
    assert summary.start_point.lon_deg == pytest.approx(-19.9969, abs=1e-4)
    assert summary.track_polyline is not None
    track = decode_polyline(summary.track_polyline)
    assert 10 < len(track) < 1200  # semplificata: molto meno dei 1200 record
    assert track[0].lat_deg == pytest.approx(summary.start_point.lat_deg, abs=1e-5)


def test_indoor_bike_has_a_distance_but_no_position() -> None:
    activity = decode_activity(valid_bytes("bike_indoor_no_gps.fit"))

    assert activity.sport is Sport.CYCLING
    assert activity.summary.distance_m == pytest.approx(14_392.0)
    assert activity.summary.ascent_m is None
    assert activity.summary.start_point is None
    assert activity.summary.track_polyline is None


def test_files_without_summary_fields_have_an_empty_summary() -> None:
    summary = decode_activity(valid_bytes()).summary

    assert (summary.distance_m, summary.ascent_m) == (None, None)
    assert (summary.start_point, summary.track_polyline) == (None, None)


def test_start_point_is_the_first_valid_record_when_the_session_has_none() -> None:
    activity = decode_activity(valid_bytes("run_gps_invalid_positions.fit"))

    assert activity.summary.start_point is not None
    # Le prime 30 posizioni sono non valide: la partenza è il record 30, a 75 m dall'origine.
    assert activity.summary.start_point.lon_deg == pytest.approx(-20.0 + 75.0 / 96_300.0, abs=1e-5)
    assert activity.summary.track_polyline is not None
    assert len(decode_polyline(activity.summary.track_polyline)) == 2  # linea retta


def test_session_start_position_wins_over_the_records() -> None:
    spec = gps_activity(start_position=(10.5, 20.25))

    start = decode_activity(fw.build_activity(spec)).summary.start_point

    assert start is not None
    assert (start.lat_deg, start.lon_deg) == pytest.approx((10.5, 20.25), abs=1e-6)


def test_semicircles_convert_to_degrees_at_the_extremes() -> None:
    spec = gps_activity(start_position=(-90.0, -180.0))

    start = decode_activity(fw.build_activity(spec)).summary.start_point

    assert start is not None
    assert (start.lat_deg, start.lon_deg) == (-90.0, -180.0)


def test_positions_outside_the_globe_are_ignored_not_repaired() -> None:
    spec = small_activity()
    samples = [replace(s, lat_deg=91.0, lon_deg=10.0) for s in spec.samples]  # 91° di latitudine

    summary = decode_activity(fw.build_activity(replace(spec, samples=samples))).summary

    assert summary.start_point is None
    assert summary.track_polyline is None


def test_a_record_with_only_one_valid_coordinate_is_ignored() -> None:
    spec = small_activity()
    samples = [replace(s, lat_deg=10.0, lon_deg=None) for s in spec.samples]

    summary = decode_activity(fw.build_activity(replace(spec, samples=samples))).summary

    assert summary.start_point is None


def test_invalid_session_distance_and_ascent_are_ignored() -> None:
    # 0xFFFFFFFF (distanza, scala 100) e 0xFFFF (dislivello) sono i valori "non validi" FIT.
    spec = replace(small_activity(), total_distance_m=42_949_672.95, total_ascent_m=0xFFFF)

    summary = decode_activity(fw.build_activity(spec)).summary

    assert (summary.distance_m, summary.ascent_m) == (None, None)


def test_a_long_track_is_capped_at_the_project_limit() -> None:
    start = datetime(2026, 3, 2, 7, 0, tzinfo=UTC)
    first = fw.datetime_to_fit(start)
    n = 5000
    samples = [
        fw.Sample(first + i, 120, 45.0 + 0.0003 * math.sin(i * 0.7), 9.0 + i * 1e-5)
        for i in range(n)
    ]
    spec = fw.ActivitySpec(start, fw.Sport.RUNNING, fw.SubSport.GENERIC, samples)

    polyline = decode_activity(fw.build_activity(spec)).summary.track_polyline

    assert polyline is not None
    assert len(decode_polyline(polyline)) <= MAX_TRACK_POINTS


# --- Regola del tempo in movimento ----------------------------------------------------------


def test_timer_up_to_one_second_above_elapsed_is_brought_to_elapsed() -> None:
    spec = replace(small_activity(), total_elapsed_ms=60_000, total_timer_ms=61_000)

    activity = decode_activity(fw.build_activity(spec))

    assert (activity.elapsed_s, activity.timer_s) == (60.0, 60.0)


def test_timer_more_than_one_second_above_elapsed_is_a_corrupt_file() -> None:
    spec = replace(small_activity(), total_elapsed_ms=60_000, total_timer_ms=61_001)

    assert reason_of(fw.build_activity(spec)) is FitDecodeReason.CORRUPT


def test_timer_below_elapsed_is_kept() -> None:
    spec = replace(small_activity(), total_elapsed_ms=60_000, total_timer_ms=59_999)

    assert decode_activity(fw.build_activity(spec)).timer_s == 59.999
