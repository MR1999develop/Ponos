# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Form "New profile": LT1 of both groups, other-sports LT2 and threshold pace."""

from __future__ import annotations

import pytest

from ponos_coach.load.domain import ThresholdHeartRate
from ponos_coach.profiles.domain import ThresholdPace
from ponos_coach.profiles.use_cases import NewProfileRequest
from ponos_coach.ui.new_profile_form import Field, NewProfileForm, Problem
from tests.ui.test_new_profile_form import TODAY, make_form


def built(form: NewProfileForm) -> NewProfileRequest:
    request = form.build_request(TODAY)
    assert isinstance(request, NewProfileRequest)
    return request


def test_empty_optional_fields_give_the_defaults_and_no_pace() -> None:
    form = make_form()

    request = built(form)

    assert request.physiology.other_threshold is None
    assert request.physiology.run_lt1 is None
    assert request.physiology.other_lt1 is None
    assert request.threshold_pace is None
    assert form.threshold_default(Field.OTHER_THRESHOLD) == 171  # the running LT2
    assert form.threshold_default(Field.RUN_LT1) == 142  # 83% of 171 = 141.93
    assert form.threshold_default(Field.OTHER_LT1) == 142


def test_the_defaults_follow_the_rates_entered() -> None:
    form = make_form()
    form.set_threshold(100)
    assert form.threshold_default(Field.RUN_LT1) == 83
    assert form.threshold_default(Field.OTHER_LT1) == 83
    form.min_bpm = 98
    assert form.threshold_default(Field.RUN_LT1) == 99  # kept above the resting rate
    form.min_bpm = 180  # rates that do not fit: still a number to show
    assert form.threshold_default(Field.OTHER_THRESHOLD) == 100


def test_the_default_other_lt1_follows_the_other_lt2_typed() -> None:
    form = make_form()
    form.other_text = "150"

    assert form.threshold_default(Field.OTHER_LT1) == 125  # 124.5: half rounds up
    assert form.threshold_default(Field.RUN_LT1) == 142  # the running one does not follow it
    form.other_text = "abc"  # not a number: back to the running LT2
    assert form.threshold_default(Field.OTHER_LT1) == 142


def test_typed_thresholds_and_pace_go_into_the_request() -> None:
    form = make_form()
    form.run_lt1_text = " 140 "
    form.other_text = "168"
    form.other_lt1_text = "130"
    form.pace_text = "5,30"

    request = built(form)

    assert request.physiology.run_lt1 == ThresholdHeartRate(140)
    assert request.physiology.other_threshold == ThresholdHeartRate(168)
    assert request.physiology.other_lt1 == ThresholdHeartRate(130)
    assert request.threshold_pace == ThresholdPace(330)


@pytest.mark.parametrize(
    ("run_lt1", "other", "other_lt1", "expected"),
    [
        ("abc", "", "", Problem.RUN_LT1_NOT_A_THRESHOLD),
        ("39", "", "", Problem.RUN_LT1_NOT_A_THRESHOLD),  # outside the threshold range
        ("50", "", "", Problem.RUN_LT1_NOT_ABOVE_MIN),  # equal to the resting rate
        ("171", "", "", Problem.RUN_LT1_NOT_BELOW_LT2),  # equal to LT2 (on point)
        ("172", "", "", Problem.RUN_LT1_NOT_BELOW_LT2),
        ("", "x", "", Problem.OTHER_NOT_A_THRESHOLD),
        ("", "50", "", Problem.OTHER_NOT_ABOVE_MIN),
        ("", "200", "", Problem.OTHER_ABOVE_MAX),
        ("", "", "50", Problem.OTHER_LT1_NOT_ABOVE_MIN),
        ("", "", "171", Problem.OTHER_LT1_NOT_BELOW_LT2),  # the other LT2 is empty: LT2 of running
        ("", "150", "150", Problem.OTHER_LT1_NOT_BELOW_LT2),  # equal to the other LT2
    ],
)
def test_an_invalid_optional_value_is_a_problem_on_its_field_and_blocks_the_request(
    run_lt1: str, other: str, other_lt1: str, expected: Problem
) -> None:
    form = make_form()
    form.run_lt1_text = run_lt1
    form.other_text = other
    form.other_lt1_text = other_lt1

    assert form.problems(TODAY) == (expected,)
    assert form.build_request(TODAY) == (expected,)


@pytest.mark.parametrize(
    ("lt1", "other", "accepted"), [("149", "150", True), ("151", "150", False)]
)
def test_the_other_lt1_is_compared_with_the_other_lt2_typed(
    lt1: str, other: str, accepted: bool
) -> None:
    form = make_form()
    form.other_text = other
    form.other_lt1_text = lt1

    assert (form.problems(TODAY) == ()) is accepted


@pytest.mark.parametrize("text", ["fast", "5:75", "0:10", "99:00", "5:3x"])
def test_an_unreadable_or_out_of_range_pace_is_a_problem_and_blocks_the_request(
    text: str,
) -> None:
    form = make_form()
    form.pace_text = text

    assert form.problems(TODAY) == (Problem.PACE_INVALID,)
    assert form.build_request(TODAY) == (Problem.PACE_INVALID,)
    assert Problem.PACE_INVALID.field is Field.THRESHOLD_PACE


def test_a_blank_pace_is_no_pace_and_not_a_problem() -> None:
    form = make_form()
    form.pace_text = "   "

    assert form.problems(TODAY) == ()
    assert built(form).threshold_pace is None
