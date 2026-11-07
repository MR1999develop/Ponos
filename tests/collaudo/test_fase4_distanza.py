# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Phase 4 distance series, from FIT and GPX files, and proof that the load did not change.

The distance series is new in phase 4 (commit d5b7b3b is the last one without it). The load and
the cache fingerprint must be exactly what they were: the frozen numbers below were computed with
the code of d5b7b3b on the same files and are compared with the current code. The files carry a
distance, a speed, or neither, and the load must be the same for all three.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from ponos_coach.activities.decoding import ActivityFileError, decode_activity_file
from ponos_coach.activities.domain import Activity, Sport
from ponos_coach.assessment.domain import (
    SegmentSelection,
    ThresholdRejection,
    evaluate_threshold_test,
)
from ponos_coach.load.domain import (
    HeartRateLimits,
    Physiology,
    Sex,
    SportWeight,
    ThresholdHeartRate,
    compute_activity_load,
    load_fingerprint,
)
from ponos_coach.load.domain.sport import default_weight
from tests.collaudo.fase4_support import (
    DAY,
    LIMITS,
    START,
    Leg,
    run_fit,
    run_samples,
    standard_legs,
)
from tests.fixtures.generators import fit_writer as fw
from tests.fixtures.generators import gpx_writer as gw

PHYSIOLOGY = Physiology(HeartRateLimits(*LIMITS), ThresholdHeartRate(170), Sex.MALE)
LEGS = [Leg(300, 110, 2.5), Leg(300, 150, 3.0), Leg(300, 160, 3.2), Leg(300, 172, 3.6),
        Leg(300, 185, 4.0), Leg(300, 125, 2.5)]  # fmt: skip

# Frozen with the code of commit d5b7b3b (no distance series): (trimp, hrTSS, load, used samples,
# excluded seconds, seconds per zone). The same files must give the same numbers.
FROZEN_LOAD = (62.81951672843588, 36.81167026367779, 36.81167026367779, 1801, 0.0,
               (601.0, 300.0, 300.0, 0.0, 600.0))  # fmt: skip
FROZEN_FINGERPRINT = "392c930e987d90d61b7d23f8757c44235e047e4bbdfa5b76582a37a6457972b1"
FROZEN_FINGERPRINT_RUNNING_1_5 = "9179e92c37fb840f7700eb2b5b55bd84ac0333d6eb3c67e552c07b14a53db03d"


def decode(data: bytes, name: str = "x.fit") -> Activity:
    return decode_activity_file(data, name)


def load_of(activity: Activity) -> tuple[float, float, float, int, float, tuple[float, ...]]:
    result = compute_activity_load(
        activity.sport,
        activity.timer_s,
        activity.heart_rate,
        PHYSIOLOGY,
        default_weight(activity.sport),
    )
    return (
        result.trimp,
        result.hr_tss,
        result.load,
        result.used_samples,
        result.excluded_seconds,
        result.zone_seconds.values,
    )


# --- The load and the fingerprint did not change ----------------------------------------------


@pytest.mark.parametrize(
    ("with_distance", "with_speed"), [(True, False), (False, True), (False, False), (True, True)]
)
def test_the_load_is_the_one_computed_before_the_distance_series_whatever_the_file_carries(
    with_distance: bool, with_speed: bool
) -> None:
    activity = decode(run_fit(LEGS, with_distance=with_distance, with_speed=with_speed))

    trimp, hr_tss, load, used, excluded, zones = load_of(activity)

    expected = FROZEN_LOAD
    assert (trimp, hr_tss, load) == pytest.approx(expected[:3], rel=1e-12)
    assert (used, excluded, zones) == expected[3:]


def test_the_bike_load_is_the_one_computed_before() -> None:
    activity = decode(run_fit(LEGS, sport=fw.Sport.CYCLING, sub_sport=fw.SubSport.ROAD))

    trimp, hr_tss, load, *_ = load_of(activity)

    assert (trimp, hr_tss, load) == pytest.approx(
        (62.81951672843588, 40.22841618421735, 32.182732947373886), rel=1e-12
    )


def test_the_load_with_gaps_is_the_one_computed_before() -> None:
    data = run_fit(LEGS, serial=5, dropped=set(range(400, 430)) | set(range(900, 905)))

    trimp, hr_tss, load, used, excluded, zones = load_of(decode(data))

    assert (trimp, hr_tss, load) == pytest.approx(
        (61.83044987934624, 36.23208601003227, 36.23208601003227), rel=1e-12
    )
    assert (used, excluded, zones) == (1765, 31.0, (601.0, 269.0, 305.0, 0.0, 595.0))


def test_the_heart_rate_series_does_not_depend_on_the_distance() -> None:
    with_distance = decode(run_fit(LEGS))
    without = decode(run_fit(LEGS, with_distance=False))
    assert with_distance.heart_rate is not None
    assert without.heart_rate is not None

    assert np.array_equal(with_distance.heart_rate.seconds, without.heart_rate.seconds)
    assert np.array_equal(with_distance.heart_rate.bpm, without.heart_rate.bpm)
    assert with_distance.summary == without.summary
    assert (with_distance.elapsed_s, with_distance.timer_s) == (without.elapsed_s, without.timer_s)
    assert with_distance.discarded_hr_samples == without.discarded_hr_samples


def test_the_load_cache_fingerprint_is_the_one_of_before() -> None:
    assert load_fingerprint(PHYSIOLOGY, {}).hex == FROZEN_FINGERPRINT
    assert (
        load_fingerprint(PHYSIOLOGY, {Sport.RUNNING: SportWeight(1.5)}).hex
        == FROZEN_FINGERPRINT_RUNNING_1_5
    )


# --- FIT: distance and speed -----------------------------------------------------------------


def samples_at(times: list[int], **columns: list[float | None]) -> bytes:
    first = fw.datetime_to_fit(START)
    samples = [
        fw.Sample(
            first + t,
            150,
            distance_m=columns["distance"][i] if "distance" in columns else None,
            speed_ms=columns["speed"][i] if "speed" in columns else None,
        )
        for i, t in enumerate(times)
    ]
    spec = fw.ActivitySpec(
        START, fw.Sport.RUNNING, fw.SubSport.GENERIC, samples,
        total_elapsed_ms=(max(times) + 1) * 1000,
    )  # fmt: skip
    return fw.build_activity(spec)


def test_the_distance_series_has_the_seconds_and_metres_of_the_records() -> None:
    activity = decode(samples_at([0, 1, 2, 5, 9], distance=[0.0, 3.5, 7.0, 17.5, 31.5]))

    assert activity.distance is not None
    assert activity.distance.seconds.tolist() == [0.0, 1.0, 2.0, 5.0, 9.0]
    assert activity.distance.meters.tolist() == [0.0, 3.5, 7.0, 17.5, 31.5]


def test_a_cumulative_distance_that_does_not_start_at_zero_is_kept_as_it_is() -> None:
    activity = decode(samples_at([0, 1, 2], distance=[1000.0, 1003.0, 1006.0]))

    assert activity.distance is not None
    assert activity.distance.meters.tolist() == [1000.0, 1003.0, 1006.0]


def test_the_speed_alone_is_integrated_with_the_trapezoid_rule() -> None:
    speeds = [float(t) for t in range(11)]  # 0, 1, ..., 10 m/s: metres are t * t / 2
    activity = decode(samples_at(list(range(11)), speed=list(speeds)))

    assert activity.distance is not None
    assert activity.distance.meters.tolist() == [t * t / 2 for t in range(11)]


def test_when_both_are_present_the_distance_wins_over_the_speed() -> None:
    activity = decode(samples_at([0, 1, 2], distance=[0.0, 10.0, 20.0], speed=[60.0, 60.0, 60.0]))

    assert activity.distance is not None
    assert activity.distance.meters.tolist() == [0.0, 10.0, 20.0]


@pytest.mark.parametrize(("gap", "added"), [(9, 45.0), (10, 50.0), (11, 0.0), (300, 0.0)])
def test_speed_integration_adds_no_distance_across_a_gap_over_10_seconds(
    gap: int, added: float
) -> None:
    times = [0, 1, 2, 3, 4, 4 + gap]
    activity = decode(samples_at(times, speed=[5.0] * len(times)))

    assert activity.distance is not None
    assert activity.distance.meters.tolist() == [0.0, 5.0, 10.0, 15.0, 20.0, 20.0 + added]


def test_a_cumulative_distance_is_taken_as_the_device_gave_it_across_a_gap() -> None:
    activity = decode(samples_at([0, 1, 2, 600], distance=[0.0, 3.0, 6.0, 1800.0]))

    assert activity.distance is not None
    assert activity.distance.meters.tolist() == [0.0, 3.0, 6.0, 1800.0]


def test_the_invalid_value_of_the_speed_is_no_speed() -> None:
    activity = decode(samples_at([0, 1, 2], speed=[None, None, None], distance=[None] * 3))

    assert activity.distance is None


def test_a_distance_on_a_few_records_only_uses_the_seconds_of_those_records() -> None:
    activity = decode(samples_at([0, 1, 2, 3, 4], distance=[None, 5.0, None, 15.0, 20.0]))

    assert activity.distance is not None
    assert activity.distance.seconds.tolist() == [1.0, 3.0, 4.0]
    assert activity.heart_rate is not None
    assert set(activity.distance.seconds) <= set(activity.heart_rate.seconds)  # the same axis


# --- Distances that cannot be trusted --------------------------------------------------------


def test_a_distance_that_goes_back_gives_no_series_but_a_valid_activity() -> None:
    activity = decode(samples_at([0, 1, 2, 3], distance=[0.0, 10.0, 9.99, 20.0]))

    assert activity.distance is None
    assert activity.heart_rate is not None  # the activity stays valid
    assert activity.elapsed_s == 4.0


def test_a_distance_that_stays_still_is_a_series() -> None:
    activity = decode(samples_at([0, 1, 2, 3], distance=[5.0, 5.0, 5.0, 5.0]))

    assert activity.distance is not None
    assert activity.distance.meters.tolist() == [5.0] * 4


def test_a_distance_reset_in_the_middle_of_a_run_means_no_pace_and_a_choice_by_hr() -> None:
    first = standard_legs()
    samples = []
    for s in run_samples(first):
        t = s.timestamp - fw.datetime_to_fit(START)
        offset = 0.0 if t < 1000 else -3000.0
        samples.append(
            fw.Sample(s.timestamp, s.heart_rate, distance_m=(s.distance_m or 0) + offset)
        )
    data = fw.build_activity(
        fw.ActivitySpec(
            START, fw.Sport.RUNNING, fw.SubSport.GENERIC, samples, total_elapsed_ms=2_700_000
        )
    )

    activity = decode(data)
    outcome = evaluate_threshold_test(activity, HeartRateLimits(*LIMITS), DAY)

    assert activity.distance is None
    assert outcome.selection is SegmentSelection.BY_HEART_RATE  # type: ignore[union-attr]
    assert outcome.pace_s_per_km is None  # type: ignore[union-attr]
    assert outcome.threshold_bpm == 165  # type: ignore[union-attr]


def test_a_huge_distance_jump_is_still_a_valid_series_and_never_overflows() -> None:
    top = 42_949_672.94  # the largest value of the 32-bit centimetre field (0xFFFFFFFF: invalid)
    activity = decode(samples_at([0, 1, 2], distance=[0.0, 100.0, top]))

    assert activity.distance is not None
    assert np.isfinite(activity.distance.meters).all()
    assert activity.distance.meters[-1] == pytest.approx(top)


def test_a_distance_jump_with_the_invalid_marker_is_no_distance() -> None:
    first = fw.datetime_to_fit(START)
    raw = [fw.Sample(first + t, 150, distance_m=None) for t in range(5)]
    data = fw.build_activity(fw.ActivitySpec(START, fw.Sport.RUNNING, fw.SubSport.GENERIC, raw))

    assert decode(data).distance is None


def test_repeated_and_backward_timestamps_do_not_break_the_distance_axis() -> None:
    times = [0, 1, 1, 2, 2, 5, 4, 6]
    activity = decode(samples_at(times, distance=[0.0, 1.0, 1.5, 2.0, 2.5, 5.0, 4.5, 6.0]))

    assert activity.distance is not None
    seconds = activity.distance.seconds
    assert seconds.tolist() == [0.0, 1.0, 2.0, 5.0, 6.0]  # the first of a repetition counts
    assert (np.diff(seconds) > 0).all()
    assert (np.diff(activity.distance.meters) >= 0).all()


# --- GPX -------------------------------------------------------------------------------------

BASE = datetime(2026, 3, 14, 7, 0, tzinfo=UTC)


def gpx_points(positions: list[tuple[float, float]], step_s: int = 1) -> list[gw.GpxPoint]:
    return [
        gw.GpxPoint(lat, lon, BASE + timedelta(seconds=i * step_s), None, 150)
        for i, (lat, lon) in enumerate(positions)
    ]


def test_a_gpx_distance_is_the_haversine_along_the_track() -> None:
    # 0.001 degrees of latitude is 111.195 m on the mean-radius sphere, whatever the longitude
    positions = [(-30.0 + i * 0.001, -20.0) for i in range(5)]
    activity = decode(gw.build_gpx(gw.GpxSpec([gpx_points(positions)], "running")), "x.gpx")

    assert activity.distance is not None
    assert activity.distance.meters.tolist() == pytest.approx(
        [i * 111.19508 for i in range(5)], abs=0.01
    )


def test_a_gpx_with_two_segments_adds_no_step_between_them() -> None:
    first = gpx_points([(-30.0, -20.0), (-30.0, -19.999)])
    second = [
        gw.GpxPoint(
            10.0, 50.0, BASE + timedelta(seconds=60), None, 150
        ),  # far away, a minute later
        gw.GpxPoint(10.0, 50.001, BASE + timedelta(seconds=61), None, 150),
    ]

    activity = decode(gw.build_gpx(gw.GpxSpec([first, second], "running")), "x.gpx")

    assert activity.distance is not None
    step = activity.distance.meters[1]
    assert activity.distance.meters.tolist() == pytest.approx([0.0, step, step, 2 * step], rel=0.2)
    assert activity.distance.meters[-1] < 500.0  # not the thousands of km between the segments


@pytest.mark.parametrize("bad", ["nan", "inf", "-inf", "1e999", "NaN"])
def test_hostile_gpx_coordinates_are_refused_never_turned_into_a_distance(bad: str) -> None:
    data = gw.build_gpx(gw.GpxSpec([gpx_points([(-30.0, -20.0), (-30.0, -19.99)])], "running"))
    hostile = data.replace(b'lon="-19.9900000"', f'lon="{bad}"'.encode())
    assert hostile != data

    with pytest.raises(ActivityFileError):
        decode(hostile, "x.gpx")


def test_a_gpx_jump_across_the_planet_is_finite_and_monotone() -> None:
    positions = [(0.0, 0.0), (0.0, 180.0), (0.0, -179.0), (89.9, 0.0)]
    activity = decode(gw.build_gpx(gw.GpxSpec([gpx_points(positions)], "running")), "x.gpx")

    assert activity.distance is not None
    assert np.isfinite(activity.distance.meters).all()
    assert (np.diff(activity.distance.meters) >= 0).all()


def test_a_gpx_point_with_the_time_of_the_previous_one_is_left_out_of_the_distance() -> None:
    points = gpx_points([(-30.0, -20.0), (-30.0, -19.999), (-30.0, -19.998)])
    points[2] = gw.GpxPoint(-30.0, -19.998, points[1].time, None, 150)

    activity = decode(gw.build_gpx(gw.GpxSpec([points], "running")), "x.gpx")

    assert activity.distance is not None
    assert (np.diff(activity.distance.seconds) > 0).all()


def test_standard_test_evaluation_rejects_a_series_with_one_sample_only() -> None:
    activity = decode(samples_at([0], distance=[0.0]))

    assert activity.distance is not None
    assert evaluate_threshold_test(activity, HeartRateLimits(*LIMITS), DAY) is (
        ThresholdRejection.TOO_SHORT
    )
