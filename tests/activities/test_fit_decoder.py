# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Confine FIT: file validi, file ostili, limiti, mappatura degli sport."""

from __future__ import annotations

import contextlib
import struct
from datetime import UTC, datetime
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from ponos_coach.activities.fit_decoder import (
    FitDecodeError,
    FitDecodeReason,
    FitLimits,
    decode_activity,
    map_sport,
)
from ponos_coach.load.domain import Sport
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
    assert activity.duration_s == 3600.0
    assert activity.file_id.serial_number == 5000001
    assert activity.file_id.manufacturer == fw.MANUFACTURER_DEVELOPMENT
    assert activity.file_id.time_created == RUN_START
    assert activity.heart_rate is not None
    assert len(activity.heart_rate) == 3600
    assert activity.heart_rate.seconds[-1] == 3599.0
    assert set(activity.heart_rate.bpm) == {160.0}


def test_copy_has_the_same_identity() -> None:
    assert (
        decode_activity(valid_bytes()).file_id
        == decode_activity(valid_bytes("copy_of_run_constant_160.fit")).file_id
    )


def test_activity_without_heart_rate_has_no_series() -> None:
    activity = decode_activity(valid_bytes("walk_no_hr.fit"))

    assert activity.sport is Sport.WALKING
    assert activity.heart_rate is None


def test_invalid_heart_rate_samples_are_omitted_not_repaired() -> None:
    activity = decode_activity(valid_bytes("run_hr_gaps.fit"))

    assert activity.heart_rate is not None
    assert len(activity.heart_rate) == 1800 - 12 - 15
    assert activity.duration_s == 1800.0


def test_out_of_range_values_are_kept_in_the_series() -> None:
    activity = decode_activity(valid_bytes("run_hr_out_of_range.fit"))

    assert activity.heart_rate is not None
    assert 0.0 in activity.heart_rate.bpm
    assert 250.0 in activity.heart_rate.bpm


def test_one_second_and_twenty_four_hours_are_accepted() -> None:
    assert decode_activity(valid_bytes("run_one_second.fit")).duration_s == 1.0
    assert decode_activity(valid_bytes("hike_24h.fit")).duration_s == 86_400.0


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


def test_records_with_repeated_timestamps_are_corrupt() -> None:
    spec = small_activity()
    samples = [spec.samples[0], spec.samples[0], *spec.samples[1:]]
    broken = fw.ActivitySpec(spec.start, spec.sport, spec.sub_sport, samples)

    assert reason_of(fw.build_activity(broken)) is FitDecodeReason.CORRUPT


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

    assert decode_activity(data, FitLimits(max_bytes=len(data))).duration_s == 1.0
    assert reason_of(data, FitLimits(max_bytes=len(data) - 1)) is FitDecodeReason.TOO_LARGE


def test_default_limits_are_the_project_defaults() -> None:
    limits = FitLimits()

    assert limits.max_bytes == 50 * 1024 * 1024
    assert limits.max_records == 1_000_000
    assert limits.max_duration_s == 7 * 86_400


def test_too_many_records() -> None:
    assert reason_of(valid_bytes(), FitLimits(max_records=3599)) is FitDecodeReason.TOO_MANY_RECORDS
    assert decode_activity(valid_bytes(), FitLimits(max_records=3600)).duration_s == 3600.0


def test_too_long() -> None:
    limits = FitLimits(max_duration_s=86_399.0)

    assert reason_of(valid_bytes("hike_24h.fit"), limits) is FitDecodeReason.TOO_LONG
    at_limit = decode_activity(valid_bytes("hike_24h.fit"), FitLimits(max_duration_s=86_400.0))
    assert at_limit.duration_s == 86_400.0


def test_total_messages_are_capped_even_when_they_are_not_records() -> None:
    start = datetime(2026, 3, 2, 7, 0, tzinfo=UTC)
    one_record = fw.ActivitySpec(
        start, fw.Sport.RUNNING, fw.SubSport.GENERIC, [fw.Sample(fw.datetime_to_fit(start), 120)]
    )
    extra_laps = (b"\x02" + struct.pack("<IIII", 0, 0, 1000, 1000)) * 10  # lap con definizione 2
    data = fw.wrap_body(fw.build_body(one_record) + extra_laps)

    assert decode_activity(data).duration_s == 1.0
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
    assert decode_activity(valid).duration_s == 5.0
    assert struct.unpack_from("<I", valid, 4)[0] == len(valid) - fw.HEADER_SIZE - 2
