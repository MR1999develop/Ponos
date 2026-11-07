# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""The state of the plan as the window sees it, without a window: the notices with their remedies,
the verdicts on target times, the projection and the proposal for the retest."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, replace
from datetime import date

import pytest
from PySide6.QtCore import QLocale

from ponos_coach.goals.domain import FitnessGoal, GoalId, SessionType
from ponos_coach.planning.domain import (
    CloseARaces,
    CtlTargetNotReached,
    GoalBeyondHorizon,
    LongDayUnavailable,
    MissingIntenseSessions,
    MissingLongSession,
    Phase,
    PlanRefusal,
    RecoveryInserted,
    RefusalReason,
    RetestBeforePlan,
    RetestInUnloadWeek,
    RetestNotPlaceable,
    TargetClass,
    TargetTimeUnreachable,
)
from ponos_coach.profiles.rebuild_plan import (
    NotBuiltReason,
    PlanBuilt,
    PlanNotBuilt,
    PlanRefused,
)
from ponos_coach.ui.plan_presentation import (
    NoPlanYet,
    NoticeAction,
    PlanAvailable,
    PlanNeedsData,
    PlanRefusedNow,
    Severity,
    assessments_of,
    notices_of,
    plan_of,
    plan_state_of,
    projection_of,
    retest_proposal_of,
)
from ponos_coach.ui.plan_texts import (
    goal_label,
    label_lookup,
    notice_texts,
    phase_text,
    retest_proposal_text,
    session_type_text,
    target_class_explanation,
    target_class_text,
)
from tests.ui.plan_support import MARATHON_DAY, marathon, plan_for


@pytest.fixture(autouse=True)
def english() -> Iterator[None]:
    previous = QLocale()
    QLocale.setDefault(QLocale(QLocale.Language.English, QLocale.Country.UnitedStates))
    yield
    QLocale.setDefault(previous)


def label_of(goal_id: str) -> str:
    return f"goal {goal_id}"


def test_the_result_of_a_rebuild_becomes_a_state() -> None:
    plan = plan_for()
    refusal = PlanRefusal(RefusalReason.NO_GOALS)

    assert plan_state_of(PlanBuilt(plan, saved=True)) == PlanAvailable(plan)
    assert plan_state_of(PlanRefused(refusal, saved=False)) == PlanRefusedNow(refusal)
    assert plan_state_of(PlanNotBuilt(NotBuiltReason.MISSING_PHYSIOLOGY)) == PlanNeedsData(
        NotBuiltReason.MISSING_PHYSIOLOGY
    )
    assert plan_of(PlanAvailable(plan)) is plan
    assert plan_of(PlanRefusedNow(refusal)) is None


def test_before_any_rebuild_there_is_nothing_to_say() -> None:
    assert notices_of(NoPlanYet()) == ()
    assert assessments_of(NoPlanYet()) == {}
    assert retest_proposal_of(NoPlanYet()) is None
    assert projection_of(NoPlanYet(), [], lambda _race: "") is None


@pytest.mark.parametrize(
    ("reason", "action"),
    [
        (RefusalReason.NO_GOALS, NoticeAction.ADD_RACE),
        (RefusalReason.GOAL_NOT_IN_FUTURE, NoticeAction.OPEN_GOALS),
        (RefusalReason.GOALS_ON_SAME_DAY, NoticeAction.OPEN_GOALS),
        (RefusalReason.NO_AVAILABILITY, NoticeAction.TRAINING_SETTINGS),
        (RefusalReason.NO_SESSION_TYPES, NoticeAction.TRAINING_SETTINGS),
    ],
)
def test_every_refusal_is_a_no_plan_notice_with_a_remedy_and_the_command_that_helps(
    reason: RefusalReason, action: NoticeAction
) -> None:
    state = PlanRefusedNow(PlanRefusal(reason, ("a", "b")))

    (notice,) = notices_of(state)
    headline, remedy = notice_texts(notice, label_of)

    assert notice.severity is Severity.NO_PLAN
    assert notice.action is action
    assert headline
    assert remedy


def test_a_goal_in_the_past_is_named_in_the_refusal() -> None:
    state = PlanRefusedNow(PlanRefusal(RefusalReason.GOAL_NOT_IN_FUTURE, ("old", "older")))

    (notice,) = notices_of(state)
    headline, remedy = notice_texts(notice, label_of)

    assert "goal old; goal older" in headline
    assert "date" in remedy


def test_missing_heart_rate_limits_say_where_to_enter_them() -> None:
    (notice,) = notices_of(PlanNeedsData(NotBuiltReason.MISSING_PHYSIOLOGY))
    headline, remedy = notice_texts(notice, label_of)

    assert notice.severity is Severity.NO_PLAN
    assert notice.action is NoticeAction.EDIT_PROFILE
    assert "heart-rate" in headline
    assert "Edit profile" in remedy


def test_a_plan_without_the_long_session_says_so_and_points_to_the_settings() -> None:
    plan = plan_for(
        marathon(),
        types=frozenset({SessionType.EASY, SessionType.THRESHOLD, SessionType.RECOVERY}),
    )

    notices = notices_of(PlanAvailable(plan))
    (long_notice,) = [n for n in notices if isinstance(n.subject, MissingLongSession)]
    headline, remedy = notice_texts(long_notice, label_of)

    assert long_notice.severity is Severity.WARNING
    assert long_notice.action is NoticeAction.TRAINING_SETTINGS
    assert headline == "No long session is chosen for goal marathon: your endurance will lag."
    assert remedy == "Choose the long session in Training settings."


def test_a_plan_without_quality_sessions_says_so() -> None:
    plan = plan_for(
        marathon(), types=frozenset({SessionType.EASY, SessionType.LONG, SessionType.RECOVERY})
    )

    (notice,) = [
        n for n in notices_of(PlanAvailable(plan)) if isinstance(n.subject, MissingIntenseSessions)
    ]
    headline, remedy = notice_texts(notice, label_of)

    assert "no quality work" in headline
    assert "threshold or VO2max" in remedy


def test_a_good_plan_has_no_notice() -> None:
    assert notices_of(PlanAvailable(plan_for())) == ()


@pytest.mark.parametrize(
    ("warning", "action"),
    [
        (MissingLongSession("a"), NoticeAction.TRAINING_SETTINGS),
        (MissingIntenseSessions("a"), NoticeAction.TRAINING_SETTINGS),
        (LongDayUnavailable(), NoticeAction.TRAINING_SETTINGS),
        (CloseARaces("a", "b", 14), NoticeAction.OPEN_GOALS),
        (CtlTargetNotReached("a", 41.0, 70.0), NoticeAction.OPEN_GOALS),
        (TargetTimeUnreachable("a"), NoticeAction.OPEN_GOALS),
        (RecoveryInserted(-35.0, 90.0, 60.0, 4), NoticeAction.NONE),
        (GoalBeyondHorizon("a"), NoticeAction.OPEN_GOALS),
    ],
)
def test_every_warning_has_a_sentence_a_remedy_and_the_command_that_helps(
    warning: object, action: NoticeAction
) -> None:
    plan = plan_for()
    state = PlanAvailable(replace(plan, warnings=(warning,)))  # type: ignore[arg-type]

    (notice,) = notices_of(state)
    headline, remedy = notice_texts(notice, label_of)

    assert notice.severity is Severity.WARNING
    assert notice.action is action
    assert headline
    assert remedy


def test_the_number_of_days_in_a_warning_is_the_one_of_the_plan() -> None:
    plan = plan_for()

    def headline(days: int) -> str:
        state = PlanAvailable(replace(plan, warnings=(CloseARaces("a", "b", days),)))
        return notice_texts(notices_of(state)[0], label_of)[0]

    assert "(days between them: 1)" in headline(1)
    assert "(days between them: 14)" in headline(14)


def test_a_warning_this_window_does_not_know_still_gets_a_generic_sentence() -> None:
    @dataclass(frozen=True)
    class Surprise:
        pass

    plan = plan_for()
    state = PlanAvailable(replace(plan, warnings=(Surprise(),)))  # type: ignore[arg-type]

    (notice,) = notices_of(state)

    assert notice.action is NoticeAction.NONE
    assert notice_texts(notice, label_of)[0] == "The plan has a warning."


def test_the_verdict_on_a_target_time_is_found_by_goal_id() -> None:
    plan = plan_for(marathon(target_s=3 * 3600 + 20 * 60))

    verdicts = assessments_of(PlanAvailable(plan))

    assert set(verdicts) == {"marathon"}
    assert verdicts["marathon"].classification in set(TargetClass)


@pytest.mark.parametrize("target_class", list(TargetClass))
def test_every_verdict_has_a_name_and_an_explanation_that_start_the_same_way(
    target_class: TargetClass,
) -> None:
    assert target_class_explanation(target_class).startswith(target_class_text(target_class))


def test_without_a_threshold_pace_the_target_time_cannot_be_estimated() -> None:
    plan = plan_for(marathon(target_s=3 * 3600 + 20 * 60), pace=None)

    verdict = assessments_of(PlanAvailable(plan))["marathon"]

    assert verdict.classification is TargetClass.NOT_ESTIMABLE
    assert target_class_text(verdict.classification) == "Cannot be estimated"


def test_the_projection_has_the_plan_points_and_only_the_races_it_reaches() -> None:
    plan = plan_for()
    inside = marathon()
    beyond = marathon("late", date(2030, 1, 1))
    form = FitnessGoal(GoalId("f"), MARATHON_DAY, 60.0)

    projection = projection_of(
        PlanAvailable(plan), [inside, beyond, form], lambda race: f"{race.distance_m} m"
    )

    assert projection is not None
    assert projection.points == plan.projection
    assert [(r.day, r.priority.value, r.name) for r in projection.races] == [
        (MARATHON_DAY, "A", "42195 m")
    ]


def test_a_refusal_has_no_projection() -> None:
    state = PlanRefusedNow(PlanRefusal(RefusalReason.NO_GOALS))

    assert projection_of(state, [], lambda _race: "") is None


def test_the_retest_the_plan_proposes_is_said_in_a_sentence_for_each_case() -> None:
    assert retest_proposal_text(RetestBeforePlan()) == (
        "Plan: do the threshold test before the plan starts"
    )
    week = retest_proposal_text(RetestInUnloadWeek(date(2026, 4, 6)))
    assert week.startswith("Plan: do the threshold test in the easy week from ")
    assert "4/6/26" in week
    assert retest_proposal_text(RetestNotPlaceable()) == (
        "Plan: no easy week fits the next threshold test"
    )


def test_a_plan_carries_the_retest_proposal() -> None:
    plan = plan_for()

    assert retest_proposal_of(PlanAvailable(plan)) == plan.retest_proposal


def test_category_names_are_all_given_and_distinct() -> None:
    assert len({phase_text(phase) for phase in Phase}) == len(Phase)
    assert len({session_type_text(kind) for kind in SessionType}) == len(SessionType)


def test_a_goal_is_named_in_one_phrase_with_its_distance_priority_and_day() -> None:
    assert goal_label(marathon()) == "Race A, Marathon, 6/8/26"
    assert goal_label(FitnessGoal(GoalId("f"), date(2026, 9, 1), 55.0)) == (
        "Form goal, CTL 55, 9/1/26"
    )
    assert label_lookup({})("nobody") == "a goal"
