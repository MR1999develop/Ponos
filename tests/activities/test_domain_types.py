# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Identità, riepilogo, record, regola dei duplicati e invarianti di `Activity`."""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta, timezone
from pathlib import PurePosixPath

import pytest
from hypothesis import given
from hypothesis import strategies as st

from ponos_coach.activities.domain import (
    MAX_POLYLINE_CHARS,
    Activity,
    ActivityOrigin,
    ActivityRecord,
    ActivitySummary,
    ArchivedFile,
    FileFormat,
    FileId,
    FitIdentity,
    GeoPoint,
    GpxIdentity,
    Sport,
    format_of,
    identity_from_key,
    identity_key,
    is_possible_duplicate,
)
from tests.builders import IMPORTED_AT, T0, archived_file, fit_identity, gpx_identity, make_record

SHA = "ab" * 32

utc_datetimes = st.datetimes(
    min_value=datetime(1990, 1, 1), max_value=datetime(2100, 1, 1), timezones=st.just(UTC)
)
file_ids = st.builds(
    FileId,
    st.integers(0, 0xFFFF),
    st.integers(0, 0xFFFF),
    st.integers(0, 0xFFFFFFFF),
    utc_datetimes,
)


# --- Identità -------------------------------------------------------------------------------


def test_file_id_limits_on_and_off_point() -> None:
    FileId(0xFFFF, 0xFFFF, 0xFFFFFFFF, T0)
    for args in [(0x10000, 0, 0), (0, 0x10000, 0), (0, 0, 0x100000000), (-1, 0, 0)]:
        with pytest.raises(ValueError, match="uint"):
            FileId(*args, T0)


def test_file_id_needs_a_utc_time() -> None:
    with pytest.raises(ValueError, match="UTC"):
        FileId(1, 1, 1, datetime(2026, 1, 1))
    with pytest.raises(ValueError, match="UTC"):
        FileId(1, 1, 1, datetime(2026, 1, 1, tzinfo=timezone(timedelta(hours=2))))


@pytest.mark.parametrize("sha", ["", "A" * 64, "a" * 63, "a" * 65, "g" * 64, " " + "a" * 63])
def test_gpx_identity_needs_a_lowercase_sha256(sha: str) -> None:
    with pytest.raises(ValueError, match="SHA-256"):
        GpxIdentity(sha)


def test_format_follows_the_identity() -> None:
    assert format_of(fit_identity()) is FileFormat.FIT
    assert format_of(gpx_identity()) is FileFormat.GPX
    assert [f.value for f in FileFormat] == ["fit", "gpx"]


def test_keys_have_the_documented_shape() -> None:
    assert identity_key(FitIdentity(FileId(255, 1, 5000001, T0))) == (
        "fit:255:1:5000001:2026-03-02T07:00:00+00:00"
    )
    assert identity_key(GpxIdentity(SHA)) == f"gpx:{SHA}"


@given(file_ids)
def test_fit_key_round_trips(file_id: FileId) -> None:
    identity = FitIdentity(file_id)

    assert identity_from_key(identity_key(identity)) == identity


def test_gpx_key_round_trips_and_keys_of_different_identities_differ() -> None:
    assert identity_from_key(identity_key(gpx_identity(3))) == gpx_identity(3)
    keys = {identity_key(i) for i in (fit_identity(1), fit_identity(2), gpx_identity(1))}
    assert len(keys) == 3


@pytest.mark.parametrize(
    "key",
    [
        "",
        "fit:",
        "gpx:",
        "gpx:XYZ",
        "zip:1",
        "fit:1:2:3",
        "fit:a:2:3:2026-03-02T07:00:00+00:00",
        "fit:255:01:5:2026-03-02T07:00:00+00:00",  # non canonica: zero iniziale
        "fit:255:1:5:2026-03-02T09:00:00+02:00",  # non canonica: fuso diverso da UTC
        "fit:255:1:5:ieri",
        "fit:70000:1:5:2026-03-02T07:00:00+00:00",
    ],
)
def test_foreign_keys_are_refused(key: str) -> None:
    with pytest.raises(ValueError, match=r"."):
        identity_from_key(key)


# --- Riepilogo ------------------------------------------------------------------------------


def test_summary_may_be_entirely_empty() -> None:
    summary = ActivitySummary()

    assert summary.distance_m is None
    assert summary.track_polyline is None


@pytest.mark.parametrize("value", [-0.001, math.nan, math.inf, -math.inf])
def test_summary_numbers_must_be_finite_and_not_negative(value: float) -> None:
    with pytest.raises(ValueError, match="distanza"):
        ActivitySummary(distance_m=value)
    with pytest.raises(ValueError, match="dislivello"):
        ActivitySummary(ascent_m=value)


def test_summary_accepts_zero_and_a_valid_polyline() -> None:
    summary = ActivitySummary(0.0, 0.0, GeoPoint(1.0, 2.0), "_p~iF~ps|U")

    assert summary.track_polyline == "_p~iF~ps|U"


@pytest.mark.parametrize("track", ["", "ab cd", "ab\ncd", "è", "a" * (MAX_POLYLINE_CHARS + 1)])
def test_summary_refuses_a_track_that_is_not_a_polyline(track: str) -> None:
    with pytest.raises(ValueError, match="traccia"):
        ActivitySummary(track_polyline=track)


# --- Archivio e record ----------------------------------------------------------------------


def test_archived_file_accepts_a_generated_relative_path() -> None:
    archived = ArchivedFile(PurePosixPath("2026") / f"{SHA}.fit", SHA, 0)

    assert archived.size_bytes == 0


@pytest.mark.parametrize(
    "path",
    [
        "",
        "/etc/passwd",
        "../x.fit",
        "2026/../x.fit",
        "C:/x.fit",
        "2026\\x.fit",
        "2026/.hidden",
        "2026/x.",
        "2026/nome con spazi.fit",
        "2026/è.fit",
        "a/b/c/d/e/f/g/h/i.fit",
        "2026/" + "a" * 129,
    ],
)
def test_archived_file_refuses_unsafe_paths(path: str) -> None:
    with pytest.raises(ValueError, match="percorso"):
        ArchivedFile(PurePosixPath(path), SHA, 10)


def test_archived_file_refuses_bad_hash_or_size() -> None:
    with pytest.raises(ValueError, match="SHA-256"):
        ArchivedFile(PurePosixPath("2026/x.fit"), "xyz", 10)
    with pytest.raises(ValueError, match="dimensione"):
        ArchivedFile(PurePosixPath("2026/x.fit"), SHA, -1)


def test_record_defaults_are_consistent() -> None:
    record = make_record()

    assert record.file_format is FileFormat.FIT
    assert record.timer_s == record.elapsed_s


def test_record_format_must_match_the_identity() -> None:
    record = make_record(gpx_identity())
    assert record.file_format is FileFormat.GPX
    with pytest.raises(ValueError, match="formato"):
        ActivityRecord(
            fit_identity(), FileFormat.GPX, Sport.RUNNING, T0, 10.0, 10.0,
            ActivitySummary(), archived_file(fit_identity()), ActivityOrigin.IMPORT, IMPORTED_AT,
        )  # fmt: skip


def test_record_times_must_be_utc_and_durations_sane() -> None:
    with pytest.raises(ValueError, match="UTC"):
        make_record(start=datetime(2026, 3, 2, 7, 0))
    for elapsed in (0.0, -1.0, math.nan, math.inf):
        with pytest.raises(ValueError, match="durate"):
            make_record(elapsed_s=elapsed)
    with pytest.raises(ValueError, match="movimento"):
        make_record(elapsed_s=100.0, timer_s=100.001)
    make_record(elapsed_s=100.0, timer_s=100.0)


def test_garmin_origin_needs_a_positive_garmin_id() -> None:
    make_record(origin=ActivityOrigin.GARMIN, garmin_activity_id=1)
    with pytest.raises(ValueError, match="id Garmin"):
        make_record(origin=ActivityOrigin.GARMIN)
    with pytest.raises(ValueError, match="positivo"):
        make_record(garmin_activity_id=0)
    make_record(origin=ActivityOrigin.IMPORT, garmin_activity_id=77)  # abbinata dopo


def make_activity(**overrides: object) -> Activity:
    fields: dict[str, object] = {
        "identity": fit_identity(),
        "file_format": FileFormat.FIT,
        "sport": Sport.RUNNING,
        "start": T0,
        "elapsed_s": 100.0,
        "timer_s": 90.0,
        "heart_rate": None,
        "summary": ActivitySummary(),
    }
    fields.update(overrides)
    return Activity(**fields)  # type: ignore[arg-type]


def test_activity_invariants() -> None:
    assert make_activity().timer_s == 90.0
    make_activity(timer_s=100.0)
    with pytest.raises(ValueError, match="movimento"):
        make_activity(timer_s=100.001)
    with pytest.raises(ValueError, match="formato"):
        make_activity(file_format=FileFormat.GPX)
    with pytest.raises(ValueError, match="UTC"):
        make_activity(start=datetime(2026, 3, 2))
    with pytest.raises(ValueError, match="scartati"):
        make_activity(discarded_hr_samples=-1)


def test_record_from_activity_copies_the_shared_fields() -> None:
    activity = make_activity(summary=ActivitySummary(distance_m=5000.0))
    archived = archived_file(activity.identity)

    record = ActivityRecord.from_activity(activity, archived, ActivityOrigin.IMPORT, IMPORTED_AT)

    assert record.identity == activity.identity
    assert (record.sport, record.start, record.elapsed_s, record.timer_s) == (
        activity.sport, activity.start, activity.elapsed_s, activity.timer_s,
    )  # fmt: skip
    assert record.summary == activity.summary
    assert record.archived == archived
    assert record.garmin_activity_id is None


# --- Possibili duplicati --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("delta_start_s", "other_elapsed", "expected"),
    [
        (0, 1000.0, True),
        (60, 1000.0, True),  # on point
        (-60, 1000.0, True),
        (61, 1000.0, False),  # off point
        (-61, 1000.0, False),
        (0, 1050.0, True),  # 50 su 1050 non supera il 5% del maggiore: 52,5
        (0, 1052.0, True),
        (0, 1053.0, False),  # 53 su 1053 supera 52,65
        (0, 950.0, True),  # 50 su 1000: esatto
        (0, 949.0, False),
        (0, 1.0, False),
    ],
)
def test_duplicate_rule_boundaries(
    delta_start_s: int, other_elapsed: float, expected: bool
) -> None:
    start_b = T0 + timedelta(seconds=delta_start_s)

    assert is_possible_duplicate(T0, 1000.0, start_b, other_elapsed) is expected


@given(
    st.integers(-200, 200),
    st.floats(1.0, 100_000.0),
    st.floats(1.0, 100_000.0),
)
def test_duplicate_rule_is_symmetric(delta_s: int, a: float, b: float) -> None:
    other = T0 + timedelta(seconds=delta_s)

    assert is_possible_duplicate(T0, a, other, b) == is_possible_duplicate(other, b, T0, a)
