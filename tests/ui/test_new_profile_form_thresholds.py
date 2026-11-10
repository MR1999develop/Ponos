# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Form "New profile": cycling threshold, other-activities threshold and threshold pace."""

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

    assert request.physiology.bike_threshold is None
    assert request.physiology.other_threshold is None
    assert request.threshold_pace is None
    assert form.threshold_default(Field.BIKE_THRESHOLD) == 167  # running 171 - 4
    assert form.threshold_default(Field.OTHER_THRESHOLD) == 171


def test_the_defaults_follow_the_rates_entered() -> None:
    form = make_form()
    form.set_threshold(100)
    assert form.threshold_default(Field.BIKE_THRESHOLD) == 96
    form.min_bpm = 98
    assert form.threshold_default(Field.BIKE_THRESHOLD) == 99  # kept above the resting rate
    form.min_bpm = 180  # rates that do not fit: still a number to show
    assert form.threshold_default(Field.OTHER_THRESHOLD) == 100


def test_typed_thresholds_and_pace_go_into_the_request() -> None:
    form = make_form()
    form.bike_text = " 160 "
    form.other_text = "168"
    form.pace_text = "5,30"

    request = built(form)

    assert request.physiology.bike_threshold == ThresholdHeartRate(160)
    assert request.physiology.other_threshold == ThresholdHeartRate(168)
    assert request.threshold_pace == ThresholdPace(330)


@pytest.mark.parametrize(
    ("bike", "other", "expected"),
    [
        ("abc", "", Problem.BIKE_NOT_A_THRESHOLD),
        ("39", "", Problem.BIKE_NOT_A_THRESHOLD),  # outside the threshold range
        ("251", "", Problem.BIKE_NOT_A_THRESHOLD),
        ("50", "", Problem.BIKE_NOT_ABOVE_MIN),  # equal to the resting rate
        ("191", "", Problem.BIKE_ABOVE_MAX),
        ("", "x", Problem.OTHER_NOT_A_THRESHOLD),
        ("", "50", Problem.OTHER_NOT_ABOVE_MIN),
        ("", "200", Problem.OTHER_ABOVE_MAX),
    ],
)
def test_an_invalid_optional_threshold_is_a_problem_on_its_field_and_blocks_the_request(
    bike: str, other: str, expected: Problem
) -> None:
    form = make_form()
    form.bike_text = bike
    form.other_text = other

    assert form.problems(TODAY) == (expected,)
    assert form.build_request(TODAY) == (expected,)


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
