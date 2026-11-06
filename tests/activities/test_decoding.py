# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Punto d'ingresso unico della decodifica: riconoscimento dal contenuto ed estensione."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from ponos_coach.activities.decoding import (
    ActivityFileError,
    ActivityFileLimits,
    ActivityFileReason,
    DetectedContent,
    decode_activity_file,
    decode_stored_activity,
    detect_content,
)
from ponos_coach.activities.domain import Activity, FileFormat
from ponos_coach.activities.fit_decoder import FitLimits, decode_activity
from ponos_coach.activities.gpx_decoder import GpxLimits
from ponos_coach.activities.ports import ImportIssue
from tests.fixtures.generators import gpx_writer as gw
from tests.fixtures.generators import import_files as imp

DATA = Path(__file__).resolve().parents[1] / "fixtures" / "data"
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
    """Uguali in ogni campo; la serie di FC (colonne NumPy) si confronta per valori."""
    assert actual.heart_rate is not None or expected.heart_rate is None
    if expected.heart_rate is not None and actual.heart_rate is not None:
        assert np.array_equal(actual.heart_rate.seconds, expected.heart_rate.seconds)
        assert np.array_equal(actual.heart_rate.bpm, expected.heart_rate.bpm)
    assert replace(actual, heart_rate=None) == replace(expected, heart_rate=None)


def test_fit_and_gpx_are_recognised_from_the_content() -> None:
    assert detect_content(imp.fit_run(1)) is DetectedContent.FIT
    assert detect_content(imp.gpx_run(1)) is DetectedContent.GPX
    assert detect_content(TCX) is DetectedContent.OTHER_XML
    assert detect_content(b"") is DetectedContent.UNKNOWN
    assert detect_content(b"plain text, not a file of activity") is DetectedContent.UNKNOWN


def test_xml_with_a_bom_comments_and_declaration_is_recognised() -> None:
    body = b"\xef\xbb\xbf" + b'<?xml version="1.0"?>\n<!-- commento -->\n' + imp.gpx_run(1)[39:]

    assert detect_content(body) is DetectedContent.GPX


def test_content_with_a_doctype_is_unknown() -> None:
    assert detect_content(gw.gpx_with_empty_doctype()) is DetectedContent.UNKNOWN


def test_fit_and_gpx_files_decode_to_their_format() -> None:
    assert decode_activity_file(imp.fit_run(1), "run.fit").file_format is FileFormat.FIT
    assert decode_activity_file(imp.gpx_run(1), "run.gpx").file_format is FileFormat.GPX


def test_extension_is_case_insensitive_and_only_the_extension_counts() -> None:
    assert decode_activity_file(imp.fit_run(1), "RUN.FIT").file_format is FileFormat.FIT
    assert decode_activity_file(imp.gpx_run(1), "a.zip!sub/Run.GPX").file_format is FileFormat.GPX
    assert decode_activity_file(imp.fit_run(1), "..\\..\\x.fit").file_format is FileFormat.FIT


@pytest.mark.parametrize("name", sorted(p.name for p in (DATA / "fit").glob("*.fit")))
def test_fit_fixtures_decode_exactly_as_the_fit_reader(name: str) -> None:
    data = (DATA / "fit" / name).read_bytes()

    assert_same_activity(decode_activity_file(data, name), decode_activity(data))


@pytest.mark.parametrize("name", sorted(p.name for p in (DATA / "fit-ostili").glob("*.fit")))
def test_hostile_fit_fixtures_give_the_single_error(name: str) -> None:
    data = (DATA / "fit-ostili" / name).read_bytes()

    assert isinstance(reason_of(data, name), ActivityFileReason)


def test_t_sec_02_fit_named_gpx_and_gpx_named_fit_are_a_mismatch() -> None:
    assert reason_of(imp.fit_run(1), "run.gpx") is ActivityFileReason.EXTENSION_MISMATCH
    assert reason_of(imp.gpx_run(1), "run.fit") is ActivityFileReason.EXTENSION_MISMATCH


def test_t_sec_02_other_content_under_a_known_extension_is_corrupt() -> None:
    for name in ("run.fit", "run.gpx"):
        assert reason_of(b"MZ\x90\x00 not an activity", name) is ActivityFileReason.CORRUPT
        assert reason_of(b"\x00" * 64, name) is ActivityFileReason.CORRUPT


def test_tcx_is_refused_with_its_reason_under_any_name() -> None:
    for name in ("run.tcx", "run.gpx", "run.fit"):
        assert reason_of(TCX, name) is ActivityFileReason.UNSUPPORTED_FORMAT


@pytest.mark.parametrize("name", ["notes.txt", "run", "run.fit.bak", "run.zip", ".fit.", ""])
def test_unknown_extension_is_unsupported(name: str) -> None:
    assert reason_of(imp.fit_run(1), name) is ActivityFileReason.UNSUPPORTED_FORMAT


def test_empty_file() -> None:
    assert reason_of(b"", "run.fit") is ActivityFileReason.EMPTY
    assert reason_of(b"", "run.txt") is ActivityFileReason.EMPTY


def test_reasons_of_the_readers_are_passed_on() -> None:
    no_times = gw.build_gpx(gw.GpxSpec([[gw.GpxPoint(1.0, 1.0), gw.GpxPoint(1.0, 1.1)]]))
    assert reason_of(no_times, "a.gpx") is ActivityFileReason.NOT_AN_ACTIVITY

    fit = imp.fit_run(1)
    assert reason_of(fit[:-1] + b"\x00", "a.fit") is ActivityFileReason.CORRUPT
    assert reason_of(fit, "a.fit", ActivityFileLimits(FitLimits(max_bytes=10))) is (
        ActivityFileReason.TOO_LARGE
    )
    assert reason_of(fit, "a.fit", ActivityFileLimits(FitLimits(max_records=5))) is (
        ActivityFileReason.TOO_MANY_RECORDS
    )
    assert reason_of(imp.gpx_run(1), "a.gpx", ActivityFileLimits(gpx=GpxLimits(max_points=5))) is (
        ActivityFileReason.TOO_MANY_RECORDS
    )
    assert reason_of(fit, "a.fit", ActivityFileLimits(FitLimits(max_duration_s=60.0))) is (
        ActivityFileReason.TOO_LONG
    )


def test_every_reason_of_the_decoder_is_an_import_issue() -> None:
    assert {reason.value for reason in ActivityFileReason} <= {i.value for i in ImportIssue}


# --- File già archiviato di formato noto ---------------------------------------------------


@pytest.mark.parametrize(
    ("data", "name", "file_format"),
    [
        (imp.fit_run(1), "a.fit", FileFormat.FIT),
        (
            gw.build_gpx(gw.GpxSpec([gw.line_points(imp.run_start(1), 30, step_s=10)])),
            "a.gpx",
            FileFormat.GPX,
        ),
    ],
)
def test_stored_file_decodes_like_the_named_one(
    data: bytes, name: str, file_format: FileFormat
) -> None:
    assert_same_activity(
        decode_stored_activity(data, file_format), decode_activity_file(data, name)
    )


@pytest.mark.parametrize("file_format", [FileFormat.FIT, FileFormat.GPX])
def test_stored_file_that_is_not_an_activity_raises_the_single_error(
    file_format: FileFormat,
) -> None:
    for data, reason in (
        (b"", ActivityFileReason.EMPTY),
        (b"non e un file", ActivityFileReason.CORRUPT),
    ):
        with pytest.raises(ActivityFileError) as caught:
            decode_stored_activity(data, file_format)
        assert caught.value.reason is reason


def test_stored_gpx_with_too_many_points_raises_the_single_error() -> None:
    data = gw.build_gpx(gw.GpxSpec([gw.line_points(imp.run_start(1), 5, step_s=10)]))
    limits = ActivityFileLimits(gpx=GpxLimits(max_points=2))

    with pytest.raises(ActivityFileError) as caught:
        decode_stored_activity(data, FileFormat.GPX, limits)

    assert caught.value.reason is ActivityFileReason.TOO_MANY_RECORDS
