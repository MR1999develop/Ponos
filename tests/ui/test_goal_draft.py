# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""The goal form's rules, without a window: the target time, the problems of each field and the
goal a valid draft makes."""

from __future__ import annotations

from datetime import date

import pytest

from ponos_coach.activities.domain import Sport
from ponos_coach.goals.domain import FitnessGoal, GoalId, Priority, RaceGoal
from ponos_coach.ui.goal_draft import (
    MARATHON_M,
    GoalDraft,
    GoalField,
    GoalFormKind,
    GoalIssue,
    GoalProblem,
    NoTargetTime,
    TargetTime,
    TimeNotUnderstood,
    default_draft,
    draft_of,
    goal_of,
    issues_of,
    parse_ascent,
    parse_target_time,
    target_time_text,
)

TODAY = date(2026, 6, 30)
TOMORROW = date(2026, 7, 1)
RACE_DAY = date(2026, 9, 20)


def race(goal_id: str = "a", day: date = RACE_DAY, target: int | None = None) -> RaceGoal:
    return RaceGoal(GoalId(goal_id), day, Priority.A, Sport.RUNNING, MARATHON_M, target)


@pytest.mark.parametrize(
    ("text", "seconds"),
    [
        ("3:30:00", 12_600),
        ("1:45:30", 6_330),
        ("  1:45:00 ", 6_300),
        ("45:00", 2_700),
        ("90:15", 5_415),
        ("0:20:00", 1_200),
        ("1:00:00", 3_600),
    ],
)
def test_a_target_time_is_read_as_hours_minutes_seconds_or_minutes_seconds(
    text: str, seconds: int
) -> None:
    assert parse_target_time(text) == TargetTime(seconds)


@pytest.mark.parametrize("text", ["", "   "])
def test_a_blank_target_time_means_no_target(text: str) -> None:
    assert parse_target_time(text) == NoTargetTime()


@pytest.mark.parametrize(
    "text",
    [
        "abc",
        "1:75:00",
        "1:30:75",
        "30:61",
        "1:2:3:4",
        "7",
        "1:-5:00",
        "1::00",
        "\u0661:\u0663\u0660:\u0660\u0660",
    ],
)
def test_a_text_that_is_not_a_time_is_not_understood(text: str) -> None:
    assert isinstance(parse_target_time(text), TimeNotUnderstood)


def test_a_target_time_is_written_back_as_hours_minutes_seconds() -> None:
    assert target_time_text(12_600) == "3:30:00"
    assert target_time_text(75) == "0:01:15"
    assert target_time_text(None) == ""
    assert parse_target_time(target_time_text(5_432)) == TargetTime(5_432)


def test_a_valid_race_has_no_problems() -> None:
    draft = GoalDraft(GoalFormKind.RACE, RACE_DAY, target_time_text="3:30:00")

    assert issues_of(draft, [], TODAY) == ()


@pytest.mark.parametrize("day", [TODAY, date(2026, 6, 1), date(1999, 1, 1)])
def test_a_goal_must_be_on_a_day_after_today(day: date) -> None:
    draft = GoalDraft(GoalFormKind.RACE, day)

    assert issues_of(draft, [], TODAY) == (
        GoalIssue(GoalField.DATE, GoalProblem.DATE_NOT_AFTER_TODAY),
    )


def test_tomorrow_is_the_first_day_a_goal_can_be_on() -> None:
    assert issues_of(GoalDraft(GoalFormKind.FORM, TOMORROW), [], TODAY) == ()


def test_a_second_race_on_the_same_day_is_a_problem_of_the_date() -> None:
    draft = GoalDraft(GoalFormKind.RACE, RACE_DAY)

    assert issues_of(draft, [race()], TODAY) == (
        GoalIssue(GoalField.DATE, GoalProblem.RACE_DAY_TAKEN),
    )


def test_the_race_being_changed_does_not_count_as_another_race() -> None:
    draft = GoalDraft(GoalFormKind.RACE, RACE_DAY)

    assert issues_of(draft, [race("a")], TODAY, GoalId("a")) == ()
    assert issues_of(draft, [race("a"), race("b", date(2026, 10, 1))], TODAY, GoalId("b")) != ()


def test_a_form_goal_may_share_a_day_with_a_race() -> None:
    draft = GoalDraft(GoalFormKind.FORM, RACE_DAY)

    assert issues_of(draft, [race()], TODAY) == ()


@pytest.mark.parametrize(
    ("text", "problem"),
    [
        ("quick", GoalProblem.TIME_NOT_UNDERSTOOD),
        ("0:00:30", GoalProblem.TIME_OUT_OF_RANGE),
        ("200:00:00", GoalProblem.TIME_OUT_OF_RANGE),
    ],
)
def test_a_target_time_that_is_wrong_is_a_problem_of_that_field(
    text: str, problem: GoalProblem
) -> None:
    draft = GoalDraft(GoalFormKind.RACE, RACE_DAY, target_time_text=text)

    assert issues_of(draft, [], TODAY) == (GoalIssue(GoalField.TARGET_TIME, problem),)


@pytest.mark.parametrize("metres", [399, 500_001, 0])
def test_a_distance_outside_the_limits_is_a_problem_of_the_distance(metres: int) -> None:
    draft = GoalDraft(GoalFormKind.RACE, RACE_DAY, distance_m=metres)

    assert issues_of(draft, [], TODAY) == (
        GoalIssue(GoalField.DISTANCE, GoalProblem.DISTANCE_OUT_OF_RANGE),
    )


@pytest.mark.parametrize("ctl", [-1.0, 200.5, float("nan")])
def test_a_ctl_outside_zero_and_two_hundred_is_a_problem(ctl: float) -> None:
    draft = GoalDraft(GoalFormKind.FORM, RACE_DAY, ctl=ctl)

    assert issues_of(draft, [], TODAY) == (GoalIssue(GoalField.CTL, GoalProblem.CTL_OUT_OF_RANGE),)


def test_the_problems_come_in_the_order_of_the_fields() -> None:
    draft = GoalDraft(GoalFormKind.RACE, TODAY, distance_m=100, target_time_text="x")

    assert [issue.field for issue in issues_of(draft, [], TODAY)] == [
        GoalField.DATE,
        GoalField.DISTANCE,
        GoalField.TARGET_TIME,
    ]


def test_a_valid_draft_makes_the_domain_goal() -> None:
    draft = GoalDraft(GoalFormKind.RACE, RACE_DAY, Priority.B, Sport.CYCLING, 100_000, "3:30:00")

    goal = goal_of(draft, [], TODAY, GoalId("x"))

    assert goal == RaceGoal(GoalId("x"), RACE_DAY, Priority.B, Sport.CYCLING, 100_000, 12_600)


def test_a_race_without_a_target_time_has_none() -> None:
    goal = goal_of(GoalDraft(GoalFormKind.RACE, RACE_DAY), [], TODAY, GoalId("x"))

    assert isinstance(goal, RaceGoal)
    assert goal.target_time_s is None


def test_a_form_goal_draft_makes_a_fitness_goal() -> None:
    goal = goal_of(GoalDraft(GoalFormKind.FORM, RACE_DAY, ctl=65.0), [], TODAY, GoalId("x"))

    assert goal == FitnessGoal(GoalId("x"), RACE_DAY, 65.0)


def test_a_draft_with_a_problem_makes_no_goal() -> None:
    assert goal_of(GoalDraft(GoalFormKind.RACE, TODAY), [], TODAY, GoalId("x")) is None


@pytest.mark.parametrize(
    "goal",
    [race(target=12_600), race(target=None), FitnessGoal(GoalId("f"), RACE_DAY, 55.0)],
)
def test_a_saved_goal_fills_the_form_and_the_form_gives_the_goal_back(goal: RaceGoal) -> None:
    draft = draft_of(goal)

    assert goal_of(draft, [], TODAY, goal.goal_id) == goal


def test_a_new_goal_starts_as_an_a_race_of_the_common_marathon() -> None:
    draft = default_draft(GoalFormKind.RACE, RACE_DAY)

    assert (draft.priority, draft.distance_m, draft.target_time_text) == (Priority.A, 42_195, "")


# --- The elevation gain -----------------------------------------------------------------------
@pytest.mark.parametrize(
    ("text", "metres"),
    [
        ("", 0),
        ("  ", 0),
        ("0", 0),
        ("1200", 1200),
        (" 1200 ", 1200),
        ("1200 m", 1200),
        ("1200m", 1200),
        ("1 200", 1200),
        ("1.200", 1200),
        ("1,200", 1200),
        ("30,000", 30_000),
        ("800", 800),
    ],
)
def test_an_elevation_gain_is_read_tolerantly_and_blank_means_a_flat_race(
    text: str, metres: int
) -> None:
    assert parse_ascent(text) == metres


@pytest.mark.parametrize("text", ["abc", "-5", "+5", "12.5", "1,5", "1.2000", "1 2", "٣٠٠", "1e3"])
def test_an_elevation_gain_that_is_not_whole_metres_is_not_understood(text: str) -> None:
    assert parse_ascent(text) is None


def _with_ascent(text: str, distance_m: int = MARATHON_M) -> GoalDraft:
    return GoalDraft(GoalFormKind.RACE, RACE_DAY, distance_m=distance_m, ascent_text=text)


@pytest.mark.parametrize(
    ("text", "problem"),
    [
        ("steep", GoalProblem.ASCENT_NOT_UNDERSTOOD),
        ("30001", GoalProblem.ASCENT_OUT_OF_RANGE),
        ("21098", GoalProblem.ASCENT_TOO_STEEP),  # more than 50% of a marathon
    ],
)
def test_an_elevation_gain_with_a_problem_is_a_problem_of_that_field(
    text: str, problem: GoalProblem
) -> None:
    assert issues_of(_with_ascent(text), [], TODAY) == (GoalIssue(GoalField.ASCENT, problem),)


@pytest.mark.parametrize(
    ("text", "distance_m", "problem"),
    [
        ("30000", 80_000, None),  # the largest
        ("30001", 80_000, GoalProblem.ASCENT_OUT_OF_RANGE),  # one over
        ("2500", 5_000, None),  # 50%: the steepest allowed
        ("2501", 5_000, GoalProblem.ASCENT_TOO_STEEP),  # one over
        ("0", 400, None),
    ],
)
def test_the_limits_of_the_elevation_gain_have_their_on_and_off_points(
    text: str, distance_m: int, problem: GoalProblem | None
) -> None:
    issues = issues_of(_with_ascent(text, distance_m), [], TODAY)

    assert issues == (() if problem is None else (GoalIssue(GoalField.ASCENT, problem),))


def test_the_elevation_gain_goes_into_the_goal_and_back_into_the_form() -> None:
    draft = _with_ascent("1 200", 42_195)
    goal = goal_of(draft, [], TODAY, GoalId("a"))

    assert isinstance(goal, RaceGoal)
    assert goal.ascent_m == 1_200
    assert draft_of(goal).ascent_text == "1200"
    assert draft_of(race()).ascent_text == ""
    flat = goal_of(_with_ascent(""), [], TODAY, GoalId("a"))
    assert isinstance(flat, RaceGoal)
    assert flat.ascent_m == 0
