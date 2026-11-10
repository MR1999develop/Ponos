# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""T-PIANO-06 end to end: realistic runs written as FIT files go through the real import,
the on-disk archive, the SQLite index and `EvaluateThresholdTest`.

Every expected value is computed by hand from the legs of the run (see `fase4_support`):
warm-up 10 minutes, a 30-minute test at 3.5 m/s (285.7 s/km, rounded to 286) and a cool-down. The
negative cases are the ones the plan asks for: interrupted or too short test, pauses, missing or
out-of-limits HR, wrong sport, a treadmill run without distance.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from ponos_coach.activities.decoding import decode_stored_activity
from ponos_coach.assessment.domain import (
    SegmentSelection,
    ThresholdRejection,
    ThresholdTestResult,
)
from ponos_coach.assessment.use_cases import EvaluateThresholdTest
from ponos_coach.load.use_cases import ExcludedActivity, ExclusionReason
from tests.collaudo.fase4_support import (
    DAY,
    ROME,
    START,
    Leg,
    ThresholdProfile,
    make_threshold_profile,
    run_fit,
    run_samples,
    standard_legs,
)
from tests.collaudo.support import stored_files
from tests.fixtures.generators import fit_writer as fw

PACE_AT_3_5_MS = 286  # 1000 / 3.5 = 285.71 s/km


@pytest.fixture
def tp(tmp_path: Path) -> Iterator[ThresholdProfile]:
    profile = make_threshold_profile(tmp_path)
    yield profile
    profile.close()


def accepted(outcome: object) -> ThresholdTestResult:
    assert isinstance(outcome, ThresholdTestResult), outcome
    return outcome


# --- The standard test, by hand ----------------------------------------------------------


def test_t_piano_06_warm_up_30_minutes_cool_down_gives_the_window_hr_and_pace_by_hand(
    tp: ThresholdProfile,
) -> None:
    result = accepted(tp.evaluate_bytes(run_fit(standard_legs())))

    # The window is the 30 minutes at 3.5 m/s: from second 600 (07:10) to 2400.
    assert result.selection is SegmentSelection.BY_DISTANCE
    assert result.segment_start_s == 600.0
    assert result.segment_start == datetime(2026, 3, 14, 7, 10, tzinfo=UTC)
    assert result.test_day == DAY
    # Last 20 minutes (seconds 1200 to 2400 of the file): 1200 samples of 165 bpm at 3.5 m/s.
    assert result.threshold_bpm == 165
    assert result.pace_s_per_km == PACE_AT_3_5_MS
    assert result.valid_hr_samples == 1200
    assert result.hr_zone_starts_bpm == pytest.approx((123.75, 140.25, 156.75, 169.95))
    assert result.pace_zone_starts_s_per_km == pytest.approx(
        (1000 / 3.5 / 0.78, 1000 / 3.5 / 0.88, 1000 / 3.5 / 0.94, 1000 / 3.5), abs=1.0
    )


def test_t_piano_06_the_first_10_minutes_of_the_window_do_not_count_for_the_hr(
    tp: ThresholdProfile,
) -> None:
    # the mean of the whole window would be (600 * 150 + 1200 * 170) / 1800 = 163.3
    legs = [Leg(600, 130, 3.0), Leg(600, 150, 3.5), Leg(1200, 170, 3.5), Leg(300, 120, 2.5)]

    result = accepted(tp.evaluate_bytes(run_fit(legs)))

    assert result.threshold_bpm == 170
    assert result.pace_s_per_km == PACE_AT_3_5_MS


def test_t_piano_06_a_mean_of_165_5_rounds_up_to_166(tp: ThresholdProfile) -> None:
    legs = [Leg(600, 130, 3.0), Leg(600, 150, 3.5), Leg(600, 165, 3.5), Leg(600, 166, 3.5),
            Leg(300, 120, 2.5)]  # fmt: skip

    result = accepted(tp.evaluate_bytes(run_fit(legs)))

    assert result.threshold_bpm == 166  # (600 * 165 + 600 * 166) / 1200 = 165.5


def test_t_piano_06_a_mean_just_under_the_half_rounds_down(tp: ThresholdProfile) -> None:
    legs = [Leg(600, 130, 3.0), Leg(600, 150, 3.5), Leg(601, 165, 3.5), Leg(599, 166, 3.5),
            Leg(300, 120, 2.5)]  # fmt: skip

    result = accepted(tp.evaluate_bytes(run_fit(legs)))

    assert result.threshold_bpm == 165  # (601 * 165 + 599 * 166) / 1200 = 165.4992


def test_t_piano_06_a_faster_part_than_the_test_outside_the_30_minutes_does_not_move_the_window(
    tp: ThresholdProfile,
) -> None:
    # 5 minutes of fast cool-down strides are shorter than the window and sit after the test
    legs = [Leg(600, 130, 3.0), Leg(1800, 165, 3.5), Leg(300, 140, 3.0)]
    result = accepted(tp.evaluate_bytes(run_fit(legs)))

    # 30 minutes at 3.5 m/s beat any window that takes in a slower stretch
    assert result.segment_start_s == 600.0


# --- Exactly 30:00 and the borders of the length ------------------------------------------


def test_t_piano_06_a_run_of_exactly_30_minutes_is_a_valid_test(tp: ThresholdProfile) -> None:
    result = accepted(tp.evaluate_bytes(run_fit([Leg(1800, 165, 3.5)])))

    assert result.segment_start_s == 0.0
    assert (result.threshold_bpm, result.pace_s_per_km) == (165, PACE_AT_3_5_MS)


def test_t_piano_06_a_run_one_second_under_30_minutes_is_too_short(tp: ThresholdProfile) -> None:
    assert tp.evaluate_bytes(run_fit([Leg(1799, 165, 3.5)])) is ThresholdRejection.TOO_SHORT


def test_t_piano_06_a_30_minute_run_with_its_last_record_at_29_59_still_has_a_pace(
    tp: ThresholdProfile,
) -> None:
    data = run_fit([Leg(1800, 165, 3.5)], last_second=1799)

    result = accepted(tp.evaluate_bytes(data))

    assert result.threshold_bpm == 165  # the HR does not suffer
    assert result.pace_s_per_km == PACE_AT_3_5_MS  # the pace does


def test_t_piano_06_a_run_of_29_59_of_records_inside_a_30_minute_elapsed_time_keeps_the_hr(
    tp: ThresholdProfile,
) -> None:
    """D-F4-01: the distance series counts one interval past its last record, so the segment is
    chosen by distance too."""
    result = accepted(tp.evaluate_bytes(run_fit([Leg(1800, 165, 3.5)], last_second=1799)))

    assert result.threshold_bpm == 165
    assert result.selection is SegmentSelection.BY_DISTANCE


def test_t_piano_06_records_that_stop_long_before_the_elapsed_time_are_too_short(
    tp: ThresholdProfile,
) -> None:
    data = run_fit([Leg(1800, 165, 3.5)], elapsed_s=1800, last_second=1500)

    assert tp.evaluate_bytes(data) is ThresholdRejection.TOO_SHORT


# --- Pauses --------------------------------------------------------------------------------


def dropped_gap(after_s: int, gap_s: int) -> set[int]:
    """Records missing so that consecutive records are `gap_s` apart after second `after_s`."""
    return set(range(after_s + 1, after_s + gap_s))


def test_t_piano_06_a_pause_of_exactly_60_seconds_in_the_window_is_accepted(
    tp: ThresholdProfile,
) -> None:
    data = run_fit(standard_legs(), dropped=dropped_gap(1500, 60))

    assert isinstance(tp.evaluate_bytes(data), ThresholdTestResult)


def test_t_piano_06_a_pause_of_61_seconds_in_the_window_is_rejected(tp: ThresholdProfile) -> None:
    data = run_fit(standard_legs(), dropped=dropped_gap(1500, 61))

    assert tp.evaluate_bytes(data) is ThresholdRejection.TOO_MANY_PAUSES


def test_t_piano_06_many_short_pauses_add_up(tp: ThresholdProfile) -> None:
    dropped: set[int] = set()
    for after in (700, 1000, 1300, 1600):  # four gaps of 16 s = 64 s
        dropped |= dropped_gap(after, 16)

    assert tp.evaluate_bytes(run_fit(standard_legs(), dropped=dropped)) is (
        ThresholdRejection.TOO_MANY_PAUSES
    )


def test_t_piano_06_gaps_up_to_10_seconds_are_not_pauses_and_their_hr_is_carried_forward(
    tp: ThresholdProfile,
) -> None:
    dropped: set[int] = set()
    for after in range(700, 2300, 100):  # sixteen gaps of 10 s between records
        dropped |= dropped_gap(after, 10)

    result = accepted(tp.evaluate_bytes(run_fit(standard_legs(), dropped=dropped)))

    assert result.threshold_bpm == 165


def test_t_piano_06_a_long_pause_in_the_warm_up_does_not_count(tp: ThresholdProfile) -> None:
    data = run_fit(standard_legs(), dropped=dropped_gap(100, 200))

    result = accepted(tp.evaluate_bytes(data))

    assert result.segment_start_s == 600.0


def test_t_piano_06_a_long_pause_in_the_cool_down_does_not_count(tp: ThresholdProfile) -> None:
    data = run_fit(standard_legs(), dropped=dropped_gap(2500, 200))

    assert isinstance(tp.evaluate_bytes(data), ThresholdTestResult)


# --- Missing and out-of-limits HR ------------------------------------------------------------


def test_t_piano_06_hr_dropouts_of_9_seconds_are_bridged_and_the_mean_is_unchanged(
    tp: ThresholdProfile,
) -> None:
    legs = [Leg(600, 130, 3.0), Leg(1800, 165, 3.5), Leg(300, 120, 2.5)]
    # records without HR are written as invalid (0xFF) and dropped from the series
    missing = {t for after in range(1250, 2350, 100) for t in range(after + 1, after + 10)}
    samples = run_samples_without_hr(legs, missing)

    result = accepted(tp.evaluate_bytes(samples))

    assert result.threshold_bpm == 165
    assert result.valid_hr_samples == 1200 - len(missing)


def run_samples_without_hr(legs: list[Leg], missing: set[int]) -> bytes:
    samples = [
        fw.Sample(s.timestamp, None if s.timestamp - fw.datetime_to_fit(START) in missing
                  else s.heart_rate, distance_m=s.distance_m)
        for s in run_samples(legs)
    ]  # fmt: skip
    spec = fw.ActivitySpec(
        START, fw.Sport.RUNNING, fw.SubSport.GENERIC, samples, total_elapsed_ms=2700_000
    )
    return fw.build_activity(spec)


def test_t_piano_06_hr_missing_for_ten_dropouts_of_ten_seconds_still_reaches_90_percent(
    tp: ThresholdProfile,
) -> None:
    legs = [Leg(600, 130, 3.0), Leg(1800, 165, 3.5), Leg(300, 120, 2.5)]
    # a gap of 11 s between HR samples invalidates the sample before it (11 s): 10 x 11 = 110 s
    missing = {t for after in range(1250, 2250, 100) for t in range(after + 1, after + 11)}

    result = tp.evaluate_bytes(run_samples_without_hr(legs, missing))

    assert isinstance(result, ThresholdTestResult)
    assert result.threshold_bpm == 165


def test_t_piano_06_hr_missing_for_too_long_is_rejected_for_too_few_samples(
    tp: ThresholdProfile,
) -> None:
    legs = [Leg(600, 130, 3.0), Leg(1800, 165, 3.5), Leg(300, 120, 2.5)]
    missing = set(range(1300, 1500))  # 200 s without HR in the last 20 minutes

    result = tp.evaluate_bytes(run_samples_without_hr(legs, missing))

    assert result is ThresholdRejection.TOO_FEW_HEART_RATE_SAMPLES


def test_t_piano_06_a_cadence_lock_spike_above_the_max_hr_is_discarded_not_averaged(
    tp: ThresholdProfile,
) -> None:
    # last 20 minutes: 60 s at 220 (above the 190 limit), 540 s at 160, 600 s at 171
    legs = [Leg(600, 130, 3.0), Leg(600, 150, 3.5), Leg(60, 220, 3.5), Leg(540, 160, 3.5),
            Leg(600, 171, 3.5), Leg(300, 120, 2.5)]  # fmt: skip

    result = accepted(tp.evaluate_bytes(run_fit(legs)))

    assert result.threshold_bpm == 166  # (540 * 160 + 600 * 171) / 1140 = 165.79
    assert result.valid_hr_samples == 1140


def test_t_piano_06_hr_out_of_limits_for_more_than_10_percent_is_rejected(
    tp: ThresholdProfile,
) -> None:
    legs = [Leg(600, 130, 3.0), Leg(600, 150, 3.5), Leg(121, 220, 3.5), Leg(1079, 165, 3.5),
            Leg(300, 120, 2.5)]  # fmt: skip

    assert tp.evaluate_bytes(run_fit(legs)) is ThresholdRejection.TOO_FEW_HEART_RATE_SAMPLES


def test_t_piano_06_hr_out_of_limits_for_exactly_10_percent_is_accepted(
    tp: ThresholdProfile,
) -> None:
    legs = [Leg(600, 130, 3.0), Leg(600, 150, 3.5), Leg(120, 220, 3.5), Leg(1080, 165, 3.5),
            Leg(300, 120, 2.5)]  # fmt: skip

    assert accepted(tp.evaluate_bytes(run_fit(legs))).threshold_bpm == 165


def test_t_piano_06_hr_below_the_min_limit_is_discarded_too(tp: ThresholdProfile) -> None:
    legs = [Leg(600, 130, 3.0), Leg(600, 150, 3.5), Leg(60, 40, 3.5), Leg(1140, 165, 3.5),
            Leg(300, 120, 2.5)]  # fmt: skip

    assert accepted(tp.evaluate_bytes(run_fit(legs))).threshold_bpm == 165


def test_t_piano_06_all_the_hr_out_of_limits_is_rejected(tp: ThresholdProfile) -> None:
    legs = [Leg(600, 130, 3.0), Leg(1800, 200, 3.5), Leg(300, 120, 2.5)]

    assert tp.evaluate_bytes(run_fit(legs)) is ThresholdRejection.TOO_FEW_HEART_RATE_SAMPLES


def test_t_piano_06_a_run_with_no_hr_at_all_cannot_be_a_test(tp: ThresholdProfile) -> None:
    legs = [Leg(600, None, 3.0), Leg(1800, None, 3.5), Leg(300, None, 2.5)]

    assert tp.evaluate_bytes(run_fit(legs)) is ThresholdRejection.TOO_FEW_HEART_RATE_SAMPLES


@pytest.mark.parametrize(("hr", "expected"), [(189, 189), (190, None), (51, 51), (50, None)])
def test_t_piano_06_the_threshold_must_be_strictly_inside_the_limits(
    tp: ThresholdProfile, hr: int, expected: int | None
) -> None:
    outcome = tp.evaluate_bytes(run_fit(standard_legs(hr=hr)))

    if expected is None:
        assert outcome is ThresholdRejection.THRESHOLD_OUT_OF_RANGE
    else:
        assert accepted(outcome).threshold_bpm == expected


# --- Sport -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("sport", "sub_sport"),
    [
        (fw.Sport.CYCLING, fw.SubSport.ROAD),
        (fw.Sport.WALKING, fw.SubSport.GENERIC),
        (fw.Sport.HIKING, fw.SubSport.GENERIC),
        (fw.Sport.TRAINING, fw.SubSport.STRENGTH_TRAINING),
    ],
)
def test_t_piano_06_only_a_run_can_be_the_test(
    tp: ThresholdProfile, sport: fw.Sport, sub_sport: fw.SubSport
) -> None:
    data = run_fit(standard_legs(), sport=sport, sub_sport=sub_sport)

    assert tp.evaluate_bytes(data) is ThresholdRejection.WRONG_SPORT


@pytest.mark.parametrize("sub_sport", [fw.SubSport.TRAIL, fw.SubSport.TREADMILL])
def test_t_piano_06_trail_and_treadmill_runs_are_runs(
    tp: ThresholdProfile, sub_sport: fw.SubSport
) -> None:
    result = tp.evaluate_bytes(run_fit(standard_legs(), sub_sport=sub_sport))

    assert accepted(result).threshold_bpm == 165


# --- Distance sources ----------------------------------------------------------------------------


def test_t_piano_06_a_treadmill_run_without_distance_gives_the_hr_and_no_pace(
    tp: ThresholdProfile,
) -> None:
    data = run_fit(standard_legs(), sub_sport=fw.SubSport.TREADMILL, with_distance=False)

    result = accepted(tp.evaluate_bytes(data))

    assert result.selection is SegmentSelection.BY_HEART_RATE
    assert result.segment_start_s == 600.0  # the 30 minutes with the highest HR
    assert result.threshold_bpm == 165
    assert result.pace_s_per_km is None
    assert result.pace_zone_starts_s_per_km is None


def test_t_piano_06_a_file_with_speed_only_gets_its_distance_from_the_speed(
    tp: ThresholdProfile,
) -> None:
    data = run_fit(standard_legs(), with_distance=False, with_speed=True)

    result = accepted(tp.evaluate_bytes(data))

    assert result.selection is SegmentSelection.BY_DISTANCE
    assert result.pace_s_per_km == PACE_AT_3_5_MS
    assert result.segment_start_s == pytest.approx(600.0, abs=1.0)


def test_t_piano_06_a_standing_still_run_has_no_pace_and_is_chosen_by_hr(
    tp: ThresholdProfile,
) -> None:
    legs = [Leg(600, 130, 0.0), Leg(1800, 165, 0.0), Leg(300, 120, 0.0)]

    result = accepted(tp.evaluate_bytes(run_fit(legs)))

    assert result.selection is SegmentSelection.BY_HEART_RATE
    assert result.pace_s_per_km is None


def test_t_piano_06_a_walking_pace_is_not_a_running_pace(tp: ThresholdProfile) -> None:
    legs = [Leg(600, 130, 1.0), Leg(1800, 165, 1.0), Leg(300, 120, 1.0)]  # 1000 s/km: valid
    slow = [Leg(600, 130, 0.2), Leg(1800, 165, 0.2), Leg(300, 120, 0.2)]  # 5000 s/km: not a pace

    assert accepted(tp.evaluate_bytes(run_fit(legs), "a.fit")).pace_s_per_km == 1000
    assert accepted(tp.evaluate_bytes(run_fit(slow, serial=9), "b.fit")).pace_s_per_km is None


def test_t_piano_06_a_gps_glitch_that_makes_the_pace_absurd_gives_no_pace_but_a_test(
    tp: ThresholdProfile,
) -> None:
    legs = [Leg(600, 130, 3.0), Leg(1800, 165, 20.0), Leg(300, 120, 2.5)]  # 50 s/km

    result = accepted(tp.evaluate_bytes(run_fit(legs)))

    assert result.threshold_bpm == 165
    assert result.pace_s_per_km is None


# --- A file that cannot be read -------------------------------------------------------------------


def test_t_piano_06_an_altered_original_is_not_a_test_and_says_why(tp: ThresholdProfile) -> None:
    record = tp.import_bytes(run_fit(standard_legs()))
    [stored] = stored_files(tp.folder)
    content = bytearray(stored.read_bytes())
    content[40] ^= 0xFF
    stored.write_bytes(bytes(content))

    outcome = tp.evaluate(record.identity)

    assert isinstance(outcome, ExcludedActivity)
    assert tp.entries()[0].valid_from == date(2026, 1, 1)  # nothing was saved or changed
    assert tp.list_tests() == ()


def test_t_piano_06_evaluating_changes_nothing_in_the_profile(tp: ThresholdProfile) -> None:
    record = tp.import_bytes(run_fit(standard_legs()))
    before = (tp.entries(), tp.list_tests())

    tp.evaluate(record.identity)
    tp.evaluate(record.identity)

    assert (tp.entries(), tp.list_tests()) == before


# --- Defects found by the campaign -------------------------------------------------------------


def with_distance_jump(at_s: int, jump_m: float) -> bytes:
    """The standard run whose cumulative distance jumps by `jump_m` at second `at_s`."""
    samples = [
        fw.Sample(
            s.timestamp,
            s.heart_rate,
            distance_m=(s.distance_m or 0.0)
            + (jump_m if s.timestamp - fw.datetime_to_fit(START) >= at_s else 0.0),
        )
        for s in run_samples(standard_legs())
    ]
    spec = fw.ActivitySpec(
        START, fw.Sport.RUNNING, fw.SubSport.GENERIC, samples, total_elapsed_ms=2_700_000
    )
    return fw.build_activity(spec)


@pytest.mark.parametrize("at_s", [300, 2500])
def test_t_piano_06_a_gps_distance_jump_outside_the_test_does_not_move_the_window(
    tp: ThresholdProfile, at_s: int
) -> None:
    result = accepted(tp.evaluate_bytes(with_distance_jump(at_s, 20_000.0)))

    assert result.segment_start_s == 600.0
    assert result.threshold_bpm == 165


def test_t_piano_06_a_gps_distance_jump_inside_the_test_keeps_the_hr_and_drops_the_pace(
    tp: ThresholdProfile,
) -> None:
    result = accepted(tp.evaluate_bytes(with_distance_jump(1500, 20_000.0)))

    assert result.threshold_bpm == 165
    assert result.pace_s_per_km is None  # honest: the pace of those 20 minutes is not known


def test_t_piano_06_the_segment_start_is_the_instant_of_the_record_where_it_starts(
    tp: ThresholdProfile,
) -> None:
    session_start = START - timedelta(seconds=5)  # the first record is 5 s after the session start
    spec = fw.ActivitySpec(
        session_start,
        fw.Sport.RUNNING,
        fw.SubSport.GENERIC,
        run_samples(standard_legs()),  # records from START: second 600 is at 07:10:00
        total_elapsed_ms=2_705_000,
    )

    result = accepted(tp.evaluate_bytes(fw.build_activity(spec)))

    assert result.segment_start_s == 600.0
    assert result.segment_start == datetime(2026, 3, 14, 7, 10, tzinfo=UTC)


# D-F4-03 (found and fixed during the campaign): `ProfileSession` did not expose the history.
def test_the_open_profile_session_exposes_the_threshold_test_history(
    tp: ThresholdProfile,
) -> None:
    assert tp.profile.session.threshold_tests is tp.history


# --- Limits and determinism ------------------------------------------------------------------


def test_t_piano_06_a_file_over_the_byte_cap_is_not_read_and_says_so(tp: ThresholdProfile) -> None:
    record = tp.import_bytes(run_fit(standard_legs()))
    capped = EvaluateThresholdTest(
        tp.profile.session.activities,
        tp.profile.archive,
        tp.profile.session.physiology,
        decode_stored_activity,
        timezone=ROME,
        max_file_bytes=1000,
    )

    outcome = capped(record.identity)

    assert isinstance(outcome, ExcludedActivity)
    assert outcome.reason is ExclusionReason.FILE_TOO_LARGE


@pytest.mark.parametrize("cap", [0, -1])
def test_t_piano_06_a_cap_that_is_not_positive_is_refused_at_construction(
    tp: ThresholdProfile, cap: int
) -> None:
    with pytest.raises(ValueError, match="positive"):
        EvaluateThresholdTest(
            tp.profile.session.activities,
            tp.profile.archive,
            tp.profile.session.physiology,
            decode_stored_activity,
            timezone=ROME,
            max_file_bytes=cap,
        )


def test_t_piano_06_the_same_runs_give_the_same_results_whatever_the_import_order(
    tmp_path: Path,
) -> None:
    runs = {
        serial: run_fit(
            standard_legs(hr=160 + serial), START + timedelta(days=serial), serial=serial
        )
        for serial in (1, 2, 3)
    }
    outcomes = []
    for order in ((1, 2, 3), (3, 1, 2)):
        profile = make_threshold_profile(tmp_path / "-".join(map(str, order)))
        try:
            found = {}
            for serial in order:
                result = profile.evaluate_bytes(runs[serial], f"r{serial}.fit")
                assert isinstance(result, ThresholdTestResult)
                found[serial] = (
                    result.threshold_bpm,
                    result.pace_s_per_km,
                    result.segment_start_s,
                    result.hr_zone_starts_bpm,
                    result.pace_zone_starts_s_per_km,
                )
            outcomes.append(found)
        finally:
            profile.close()

    assert outcomes[0] == outcomes[1]
    assert [outcomes[0][s][0] for s in (1, 2, 3)] == [161, 162, 163]
