# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Phase 4 distance series, from FIT files, and proof that the load did not change.

The distance series is new in phase 4 (commit d5b7b3b is the last one without it). The load and
the cache fingerprint must be exactly what they were: the frozen numbers below were computed with
the code of d5b7b3b on the same files and are compared with the current code. The files carry a
distance, a speed, or neither, and the load must be the same for all three.
"""

from __future__ import annotations

import numpy as np
import pytest

from ponos_coach.activities.decoding import decode_activity_file
from ponos_coach.activities.domain import Activity
from ponos_coach.assessment.domain import (
    SegmentSelection,
    ThresholdRejection,
    evaluate_threshold_test,
)
from ponos_coach.load.domain import (
    HeartRateLimits,
    Physiology,
    Sex,
    ThresholdHeartRate,
    compute_activity_load,
    load_fingerprint,
)
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

PHYSIOLOGY = Physiology(HeartRateLimits(*LIMITS), ThresholdHeartRate(170), Sex.MALE)
LEGS = [Leg(300, 110, 2.5), Leg(300, 150, 3.0), Leg(300, 160, 3.2), Leg(300, 172, 3.6),
        Leg(300, 185, 4.0), Leg(300, 125, 2.5)]  # fmt: skip

# Frozen with the code of commit d5b7b3b (no distance series): (TRIMPeq of a run, used samples,
# excluded seconds, seconds per zone); the number is the hrTSS of that time, the same
# formula. The same files must give the same numbers: the load did not
# change when the zones of the user came (75/85/95/103%, 2026-11-10), only the seconds per zone
# and the fingerprint of the saved loads, which therefore recompute.
FROZEN_LOAD = (36.81167026367779, 1801, 0.0, (601.0, 0.0, 600.0, 300.0, 300.0))
# Model 5 (the bike has no threshold of its own any more) with the zones 75/85/95/103%: the hash of
# {"max_bpm":190,"min_bpm":50,"model":5,"sex":"male","threshold_bpm":170,"zones":[75,85,95,103]}
# worked out outside the code.
FROZEN_FINGERPRINT = "457c20329fa8cd6ec9e61a74e551b62b64c1ebf1b9a5d439801c113270675979"


BIKE_TRIMP_EQ = 40.193518072841776


def decode(data: bytes, name: str = "x.fit") -> Activity:
    return decode_activity_file(data, name)


def load_of(activity: Activity) -> tuple[float, int, float, tuple[float, ...]]:
    result = compute_activity_load(
        activity.sport, activity.timer_s, activity.heart_rate, PHYSIOLOGY
    )
    return (
        result.trimp_eq,
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

    trimp_eq, used, excluded, zones = load_of(activity)

    expected = FROZEN_LOAD
    assert trimp_eq == pytest.approx(expected[0], rel=1e-12)
    assert (used, excluded, zones) == expected[1:]


def test_the_bike_load_with_the_other_sports_lt2_four_beats_lower_is_the_one_computed_before() -> (
    None
):
    activity = decode(run_fit(LEGS, sport=fw.Sport.CYCLING, sub_sport=fw.SubSport.ROAD))
    other_166 = Physiology(
        PHYSIOLOGY.limits,
        PHYSIOLOGY.threshold,
        PHYSIOLOGY.sex,
        other_threshold=ThresholdHeartRate(166),  # what the default bike threshold was: 170 - 4
    )

    result = compute_activity_load(activity.sport, activity.timer_s, activity.heart_rate, other_166)

    # TRIMPeq of the bike: Banister on the running-equivalent intensity (it was hrTSS 40.23 with
    # the bike weight 0.8: 32.18); frozen before the two groups, when 166 was the default
    assert result.trimp_eq == pytest.approx(BIKE_TRIMP_EQ, rel=1e-12)


def test_the_bike_without_an_other_sports_lt2_is_valued_like_a_run_since_the_two_groups() -> None:
    bike = decode(run_fit(LEGS, sport=fw.Sport.CYCLING, sub_sport=fw.SubSport.ROAD))
    run = decode(run_fit(LEGS))

    bike_trimp_eq, *_ = load_of(bike)
    run_trimp_eq, *_ = load_of(run)

    assert bike_trimp_eq == pytest.approx(FROZEN_LOAD[0], rel=1e-12)  # the run's frozen number
    assert bike_trimp_eq == run_trimp_eq
    assert bike_trimp_eq < BIKE_TRIMP_EQ  # before: threshold 166, so more load for the same ride


def test_the_load_with_gaps_is_the_one_computed_before() -> None:
    data = run_fit(LEGS, serial=5, dropped=set(range(400, 430)) | set(range(900, 905)))

    trimp_eq, used, excluded, zones = load_of(decode(data))

    assert trimp_eq == pytest.approx(36.23208601003227, rel=1e-12)
    assert (used, excluded, zones) == (1765, 31.0, (601.0, 0.0, 574.0, 295.0, 300.0))


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


def test_the_load_cache_fingerprint_is_the_one_of_the_trimp_eq_model() -> None:
    assert load_fingerprint(PHYSIOLOGY).hex == FROZEN_FINGERPRINT


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


def test_standard_test_evaluation_rejects_a_series_with_one_sample_only() -> None:
    activity = decode(samples_at([0], distance=[0.0]))

    assert activity.distance is not None
    assert evaluate_threshold_test(activity, HeartRateLimits(*LIMITS), DAY) is (
        ThresholdRejection.TOO_SHORT
    )
