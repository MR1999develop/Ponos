# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""T-PERF for the threshold test: the evaluation of very long activities.

Long tests: only in the `collaudo` session. Each measurement is repeated and reported with p50, p95
and maximum (never the mean alone) in the output of the test (`-s`). The assertions are loose caps
(machine noise makes a tight comparison unstable) plus a scaling check: the evaluation is
vectorized on breakpoints, so 4 times the data must cost far less than 16 times the time (a
quadratic search would not pass).
"""

from __future__ import annotations

import statistics
import time
from collections.abc import Callable, Sequence

import numpy as np
import pytest

from ponos_coach.activities.decoding import decode_activity_file
from ponos_coach.assessment.domain import ThresholdTestResult, evaluate_threshold_test
from ponos_coach.load.domain import HeartRateLimits
from tests.assessment.builders import Leg as AssessmentLeg
from tests.assessment.builders import synthetic_run
from tests.collaudo.fase4_support import DAY, LIMITS, Leg, run_fit

pytestmark = pytest.mark.collaudo

REPEATS = 15
SIX_HOURS_S = 6 * 3600
BUDGET_EVALUATE_6H_S = 0.5  # the pure computation over 21,600 records
BUDGET_DECODE_6H_S = 5.0  # decoding the FIT of 6 hours (21,600 records)
BUDGET_EVALUATE_MAX_RECORDS_S = 10.0  # one million records, the import cap
LIMITS_BPM = HeartRateLimits(*LIMITS)


def measure(function: Callable[[], object], repeats: int = REPEATS) -> list[float]:
    times = []
    for _ in range(repeats):
        started = time.perf_counter()
        function()
        times.append(time.perf_counter() - started)
    return times


def report(label: str, times: Sequence[float]) -> tuple[float, float, float]:
    ordered = sorted(times)
    p50 = statistics.median(ordered)
    p95 = ordered[min(len(ordered) - 1, round(0.95 * (len(ordered) - 1)))]
    line = (
        f"[T-PERF threshold] {label}: p50 {p50 * 1000:.1f} ms, p95 {p95 * 1000:.1f} ms,"
        f" max {ordered[-1] * 1000:.1f} ms (n={len(ordered)})"
    )
    print(line)  # noqa: T201 - the measurements are the output of the campaign
    return p50, p95, ordered[-1]


def long_run(seconds: int) -> list[Leg]:
    """An easy run with a hard 30-minute stretch in the middle, `seconds` long."""
    easy = (seconds - 1800) // 2
    return [Leg(easy, 135, 2.8), Leg(1800, 165, 3.5), Leg(seconds - 1800 - easy, 130, 2.6)]


def as_synthetic(legs: Sequence[Leg]) -> list[AssessmentLeg]:
    return [AssessmentLeg(leg.seconds, leg.bpm, leg.speed_ms) for leg in legs]


def test_t_perf_threshold_evaluating_a_6_hour_activity_at_1_hz_is_fast() -> None:
    activity = synthetic_run(as_synthetic(long_run(SIX_HOURS_S)))
    assert isinstance(evaluate_threshold_test(activity, LIMITS_BPM, DAY), ThresholdTestResult)

    p50, _, worst = report(
        "evaluate 6 h, 1 Hz", measure(lambda: evaluate_threshold_test(activity, LIMITS_BPM, DAY))
    )

    assert worst < BUDGET_EVALUATE_6H_S, f"p50 {p50:.3f} s, max {worst:.3f} s"


def test_t_perf_threshold_decoding_a_6_hour_fit_is_within_budget() -> None:
    data = run_fit(long_run(SIX_HOURS_S))
    print(f"[T-PERF threshold] FIT of 6 h: {len(data) / 1e6:.2f} MB, {SIX_HOURS_S + 1} records")  # noqa: T201

    p50, _, worst = report(
        "decode 6 h FIT", measure(lambda: decode_activity_file(data, "x.fit"), 7)
    )

    assert worst < BUDGET_DECODE_6H_S, f"p50 {p50:.3f} s"
    activity = decode_activity_file(data, "x.fit")
    assert activity.distance is not None and len(activity.distance) == SIX_HOURS_S + 1


def test_t_perf_threshold_decode_and_evaluate_a_6_hour_fit_together() -> None:
    data = run_fit(long_run(SIX_HOURS_S))

    def both() -> object:
        return evaluate_threshold_test(decode_activity_file(data, "x.fit"), LIMITS_BPM, DAY)

    assert isinstance(both(), ThresholdTestResult)
    _, _, worst = report("decode + evaluate 6 h", measure(both, 7))

    assert worst < BUDGET_DECODE_6H_S + BUDGET_EVALUATE_6H_S


def test_t_perf_threshold_the_cost_grows_far_less_than_quadratically() -> None:
    small = synthetic_run(as_synthetic(long_run(SIX_HOURS_S)))
    large = synthetic_run(as_synthetic(long_run(4 * SIX_HOURS_S)))  # 24 hours, 86,400 records
    p50_small, _, _ = report(
        "evaluate 6 h", measure(lambda: evaluate_threshold_test(small, LIMITS_BPM, DAY))
    )
    p50_large, _, _ = report(
        "evaluate 24 h", measure(lambda: evaluate_threshold_test(large, LIMITS_BPM, DAY))
    )

    ratio = p50_large / p50_small
    print(f"[T-PERF threshold] 4x the data costs {ratio:.1f}x the time")  # noqa: T201
    assert ratio < 10.0  # linear would be 4; the quadratic search would be 16


def test_t_perf_threshold_one_million_records_the_import_cap_is_evaluated_in_seconds() -> None:
    seconds = 999_000
    legs = [Leg(seconds // 2, 135, 2.8), Leg(1800, 165, 3.5), Leg(seconds // 2 - 1800, 130, 2.6)]
    activity = synthetic_run(as_synthetic(legs))
    assert activity.distance is not None and len(activity.distance) > 990_000

    times = measure(lambda: evaluate_threshold_test(activity, LIMITS_BPM, DAY), 3)
    _, _, worst = report("evaluate 1e6 records", times)

    assert worst < BUDGET_EVALUATE_MAX_RECORDS_S
    result = evaluate_threshold_test(activity, LIMITS_BPM, DAY)
    assert isinstance(result, ThresholdTestResult)
    assert result.segment_start_s == pytest.approx(seconds // 2, abs=1)
    assert np.isfinite(result.segment_start_s)
