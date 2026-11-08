# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""The questionnaire and the records: valid by construction, one rule at a time."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta

import pytest

from ponos_coach.injury.domain import (
    CheckIn,
    Illness,
    IllnessKind,
    Injury,
    InjuryId,
    InvalidChangeError,
    PainScore,
    WarningSign,
    defaults,
)
from tests.injury.builders import DAY0, checkin, illness, injury, pain, questionnaire


@pytest.mark.parametrize("value", [-1, 11, 100])
def test_pain_outside_zero_to_ten_is_refused(value: int) -> None:
    with pytest.raises(ValueError, match="0 to 10"):
        PainScore(value)


@pytest.mark.parametrize("value", [0, 3, 10])
def test_pain_inside_zero_to_ten_is_kept(value: int) -> None:
    assert PainScore(value).value == value


def test_a_pain_score_is_a_whole_number_not_a_bool_or_a_float() -> None:
    with pytest.raises(ValueError, match="whole number"):
        PainScore(True)  # a bool is an int in Python, and not a pain
    with pytest.raises(ValueError, match="whole number"):
        PainScore(2.5)  # type: ignore[arg-type]


@pytest.mark.parametrize("bad", ["", "a b", "a/b", "x" * 65, "é"])
def test_an_id_is_one_to_sixty_four_safe_characters(bad: str) -> None:
    with pytest.raises(ValueError, match="injury id"):
        InjuryId(bad)


def test_rest_pain_at_the_limit_is_a_warning_sign_and_below_it_is_not() -> None:
    limit = defaults.STRONG_REST_PAIN
    assert WarningSign.STRONG_REST_PAIN in questionnaire(rest=limit).alarms()
    assert WarningSign.STRONG_REST_PAIN not in questionnaire(rest=limit - 1).alarms()


def test_ticked_signs_are_alarms_too() -> None:
    q = questionnaire(signs=[WarningSign.NIGHT_PAIN])
    assert q.alarms() == {WarningSign.NIGHT_PAIN}


def test_doctor_notes_are_free_text_with_a_limit_and_no_control_characters() -> None:
    assert questionnaire(notes="line one\nline\ttwo").doctor_notes == "line one\nline\ttwo"
    with pytest.raises(ValueError, match="characters"):
        questionnaire(notes="x" * (defaults.MAX_NOTES_CHARS + 1))
    with pytest.raises(ValueError, match="control"):
        questionnaire(notes="bad\x00text")


def test_the_onset_cannot_precede_1900() -> None:
    q = questionnaire()
    with pytest.raises(ValueError, match="1900"):
        replace(q, onset_day=date(1899, 12, 31))


def test_an_injury_cannot_be_opened_before_it_began() -> None:
    with pytest.raises(ValueError, match="before it began"):
        injury(opened_on=DAY0 - timedelta(days=11))


def test_check_ins_are_one_a_day_ascending_from_the_opening() -> None:
    with pytest.raises(ValueError, match="one a day"):
        injury(checkins=[checkin(2), checkin(1)])
    with pytest.raises(ValueError, match="one a day"):
        injury(checkins=[checkin(1), checkin(1)])
    with pytest.raises(ValueError, match="one a day"):
        injury(checkins=[checkin(-1)])


def test_a_check_in_replaces_the_one_of_the_same_day() -> None:
    first = injury().with_checkin(checkin(1, during=2))
    again = first.with_checkin(checkin(1, during=3))
    assert [c.pain_during for c in again.checkins] == [pain(3)]


def test_check_ins_arrive_in_any_order_and_are_kept_ascending() -> None:
    result = injury().with_checkin(checkin(3)).with_checkin(checkin(1))
    assert [c.day for c in result.checkins] == [DAY0 + timedelta(days=1), DAY0 + timedelta(days=3)]


def test_a_check_in_before_the_opening_is_refused() -> None:
    with pytest.raises(InvalidChangeError):
        injury().with_checkin(checkin(-1))


def test_a_closed_injury_takes_no_more_check_ins_or_reviews() -> None:
    closed = injury().closed(DAY0 + timedelta(days=5))
    with pytest.raises(InvalidChangeError):
        closed.with_checkin(checkin(6))
    with pytest.raises(InvalidChangeError):
        closed.with_review(DAY0 + timedelta(days=6))
    with pytest.raises(InvalidChangeError):
        closed.closed(DAY0 + timedelta(days=7))


def test_an_injury_cannot_be_closed_before_its_last_check_in() -> None:
    with pytest.raises(InvalidChangeError):
        injury(checkins=[checkin(4)]).closed(DAY0 + timedelta(days=2))


def test_the_number_of_check_ins_has_a_limit() -> None:
    full = injury(checkins=[checkin(i) for i in range(defaults.MAX_CHECKINS)])
    with pytest.raises(InvalidChangeError):
        full.with_checkin(checkin(defaults.MAX_CHECKINS))
    full.with_checkin(checkin(0, during=2))  # replacing one is always possible


def test_signs_reported_before_a_review_are_cleared_by_it_and_later_ones_are_not() -> None:
    q = questionnaire(signs=[WarningSign.SWELLING_OR_DEFORMITY])
    base = injury(q=q, checkins=[checkin(1, signs=[WarningSign.NIGHT_PAIN])])
    assert base.active_alarms() == {WarningSign.SWELLING_OR_DEFORMITY, WarningSign.NIGHT_PAIN}
    reviewed = base.with_review(DAY0 + timedelta(days=1))
    assert reviewed.active_alarms() == frozenset()
    later = reviewed.with_checkin(checkin(2, signs=[WarningSign.FEVER]))
    assert later.active_alarms() == {WarningSign.FEVER}


def test_a_review_cannot_go_back_in_time() -> None:
    reviewed = injury().with_review(DAY0 + timedelta(days=3))
    with pytest.raises(InvalidChangeError):
        reviewed.with_review(DAY0 + timedelta(days=2))
    with pytest.raises(InvalidChangeError):
        injury().with_review(DAY0 - timedelta(days=1))


def test_an_illness_ends_once_and_not_before_it_began() -> None:
    sick = illness(IllnessKind.NECK_UP)
    assert sick.is_open
    over = sick.recovered(DAY0 + timedelta(days=4))
    assert not over.is_open
    with pytest.raises(InvalidChangeError):
        over.recovered(DAY0 + timedelta(days=5))
    with pytest.raises(InvalidChangeError):
        sick.recovered(DAY0 - timedelta(days=1))
    with pytest.raises(ValueError, match="recovery"):
        Illness(InjuryId("x"), IllnessKind.NECK_UP, DAY0, DAY0 - timedelta(days=1))


def test_a_check_in_with_no_pain_at_all_is_pain_free() -> None:
    assert CheckIn(DAY0, None, pain(0)).pain_free
    assert CheckIn(DAY0, pain(0), pain(0)).pain_free
    assert not CheckIn(DAY0, pain(1), pain(0)).pain_free
    assert not CheckIn(DAY0, None, pain(1)).pain_free


def test_an_injury_is_a_value_with_no_hidden_state() -> None:
    assert injury() == injury()
    assert Injury(InjuryId("i1"), DAY0, questionnaire()) == injury()
