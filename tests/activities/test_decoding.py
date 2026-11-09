# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Single decoding entry point: only FIT files, recognized by the extension."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from ponos_coach.activities.decoding import (
    ActivityFileError,
    ActivityFileLimits,
    ActivityFileReason,
    decode_activity_file,
    decode_stored_activity,
)
from ponos_coach.activities.domain import Activity, FileFormat
from ponos_coach.activities.fit_decoder import FitLimits, decode_activity
from ponos_coach.activities.ports import ImportIssue
from tests.fixtures.generators import import_files as imp

DATA = Path(__file__).resolve().parents[1] / "fixtures" / "data"
GPX = (
    b'<?xml version="1.0" encoding="UTF-8"?><gpx version="1.1" creator="test">'
    b'<trk><trkseg><trkpt lat="1.0" lon="1.0"><time>2026-03-02T07:00:00Z</time></trkpt>'
    b"</trkseg></trk></gpx>"
)
TCX = (
    b'<?xml version="1.0"?><TrainingCenterDatabase '
    b'xmlns="http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2"/>'
)


def reason_of(
    data: bytes, name: str, limits: ActivityFileLimits | None = None
) -> ActivityFileReason:
    with pytest.raises(ActivityFileError) as caught:
        decode_activity_file(data, name, limits)
    return caught.value.reason


def assert_same_activity(actual: Activity, expected: Activity) -> None:
    """Equal in every field; the HR and distance series (NumPy columns) are compared by values."""
    assert actual.heart_rate is not None or expected.heart_rate is None
    if expected.heart_rate is not None and actual.heart_rate is not None:
        assert np.array_equal(actual.heart_rate.seconds, expected.heart_rate.seconds)
        assert np.array_equal(actual.heart_rate.bpm, expected.heart_rate.bpm)
    assert (actual.distance is None) == (expected.distance is None)
    if expected.distance is not None and actual.distance is not None:
        assert np.array_equal(actual.distance.seconds, expected.distance.seconds)
        assert np.array_equal(actual.distance.meters, expected.distance.meters)
    assert replace(actual, heart_rate=None, distance=None) == replace(
        expected, heart_rate=None, distance=None
    )


def test_a_fit_file_decodes_to_the_fit_format() -> None:
    assert decode_activity_file(imp.fit_run(1), "run.fit").file_format is FileFormat.FIT


def test_extension_is_case_insensitive_and_only_the_extension_counts() -> None:
    assert decode_activity_file(imp.fit_run(1), "RUN.FIT").file_format is FileFormat.FIT
    assert decode_activity_file(imp.fit_run(1), "..\\..\\x.fit").file_format is FileFormat.FIT


@pytest.mark.parametrize("name", sorted(p.name for p in (DATA / "fit").glob("*.fit")))
def test_fit_fixtures_decode_exactly_as_the_fit_reader(name: str) -> None:
    data = (DATA / "fit" / name).read_bytes()

    assert_same_activity(decode_activity_file(data, name), decode_activity(data))


@pytest.mark.parametrize("name", sorted(p.name for p in (DATA / "fit-ostili").glob("*.fit")))
def test_hostile_fit_fixtures_give_the_single_error(name: str) -> None:
    data = (DATA / "fit-ostili" / name).read_bytes()

    assert isinstance(reason_of(data, name), ActivityFileReason)


@pytest.mark.parametrize("name", ["run.gpx", "run.GPX", "run.tcx"])
def test_a_gpx_or_other_xml_file_is_no_longer_supported(name: str) -> None:
    assert reason_of(GPX, name) is ActivityFileReason.UNSUPPORTED_FORMAT
    assert reason_of(TCX, name) is ActivityFileReason.UNSUPPORTED_FORMAT


def test_t_sec_02_other_content_under_the_fit_extension_is_corrupt() -> None:
    for data in (b"MZ\x90\x00 not an activity", b"\x00" * 64, GPX, TCX):
        assert reason_of(data, "run.fit") is ActivityFileReason.CORRUPT


@pytest.mark.parametrize("name", ["notes.txt", "run", "run.fit.bak", "run.zip", ".fit.", ""])
def test_unknown_extension_is_unsupported(name: str) -> None:
    assert reason_of(imp.fit_run(1), name) is ActivityFileReason.UNSUPPORTED_FORMAT


def test_empty_file() -> None:
    assert reason_of(b"", "run.fit") is ActivityFileReason.EMPTY
    assert reason_of(b"", "run.txt") is ActivityFileReason.EMPTY


def test_reasons_of_the_reader_are_passed_on() -> None:
    fit = imp.fit_run(1)
    assert reason_of(fit[:-1] + b"\x00", "a.fit") is ActivityFileReason.CORRUPT
    assert reason_of(fit, "a.fit", ActivityFileLimits(FitLimits(max_bytes=10))) is (
        ActivityFileReason.TOO_LARGE
    )
    assert reason_of(fit, "a.fit", ActivityFileLimits(FitLimits(max_records=5))) is (
        ActivityFileReason.TOO_MANY_RECORDS
    )
    assert reason_of(fit, "a.fit", ActivityFileLimits(FitLimits(max_duration_s=60.0))) is (
        ActivityFileReason.TOO_LONG
    )


def test_every_reason_of_the_decoder_is_an_import_issue() -> None:
    assert {reason.value for reason in ActivityFileReason} <= {i.value for i in ImportIssue}


# --- Already archived file -----------------------------------------------------------------


def test_stored_file_decodes_like_the_named_one() -> None:
    data = imp.fit_run(1)

    assert_same_activity(
        decode_stored_activity(data, FileFormat.FIT), decode_activity_file(data, "a.fit")
    )


def test_stored_file_that_is_not_an_activity_raises_the_single_error() -> None:
    for data, reason in (
        (b"", ActivityFileReason.EMPTY),
        (b"not a file", ActivityFileReason.CORRUPT),
    ):
        with pytest.raises(ActivityFileError) as caught:
            decode_stored_activity(data, FileFormat.FIT)
        assert caught.value.reason is reason
