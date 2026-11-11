# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""What the window knows about the injuries, without a window: summaries, notices, status bar facts,
the days of the calendar, and the drafts of the forms. Neutral data, no real person."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta
from typing import Any

import pytest

from ponos_coach.injury.domain import (
    BodyZone,
    HaltReason,
    Illness,
    IllnessKind,
    Injury,
    InjuryId,
    Overdue,
    Problem,
    Step,
    Verdict,
    WarningSign,
    checkin_due,
    follow,
    overdue,
)
from ponos_coach.injury.use_cases import InjuryView
from ponos_coach.planning.domain import (
    LongDayUnavailable,
    NoSessionPossible,
    PlanAdapted,
    PlanHalted,
)
from ponos_coach.ui.injury_draft import (
    NO_ANSWER,
    CheckInDraft,
    QuestionnaireDraft,
    alarms_of,
    checkin_of,
    draft_of_checkin,
    illness_of,
    injury_of,
    notes_problem,
    questionnaire_of,
)
from ponos_coach.ui.injury_presentation import (
    NoticeKind,
    ProblemKind,
    ProblemPhase,
    ProblemSpan,
    StatusFacts,
    asks_for_doctor,
    halted_ids,
    health_warnings_of,
    notices_of,
    spans_of,
    status_facts_of,
    summaries_of,
    summarize,
)
from tests.injury.builders import DAY0, checkin, climb, good_days, illness, injury, questionnaire


def view(problem: Problem, today: date) -> InjuryView:
    """A record as `ListInjuries` gives it."""
    if isinstance(problem, Illness):
        return InjuryView(problem, None, False, None)
    state = follow(problem)
    return InjuryView(problem, state, checkin_due(problem, today), overdue(problem, state, today))


# --- Summaries ----------------------------------------------------------------------------------


def test_an_injury_on_its_first_step_says_how_many_good_days_it_needs() -> None:
    item = summarize(view(injury("a", checkins=good_days(1, 2)), DAY0 + timedelta(days=3)))

    assert (item.kind, item.phase, item.step) == (
        ProblemKind.INJURY,
        ProblemPhase.ACTIVE,
        Step.ACTIVATION,
    )
    assert (item.good_days, item.good_days_needed) == (2, 3)
    assert item.zone is BodyZone.SHIN
    assert item.checkin_due and not item.checkin_done
    assert item.can_check_in and not item.can_review


def test_the_walking_step_asks_for_the_days_with_no_pain_before_running() -> None:
    q = questionnaire(morning=0)
    base = injury("a", q)
    days = climb(base, Step.WALKING)
    item = summarize(view(injury("a", q, days), DAY0 + timedelta(days=len(days) + 1)))

    assert item.step is Step.WALKING
    assert item.pain_free_needed == 5
    assert item.pain_free_days <= item.pain_free_needed


def test_a_warning_sign_halts_the_record_and_a_review_is_possible() -> None:
    item = summarize(view(injury("a", questionnaire(signs={WarningSign.NIGHT_PAIN})), DAY0))

    assert item.phase is ProblemPhase.HALTED
    assert item.alarms == (WarningSign.NIGHT_PAIN,)
    assert item.can_review and item.can_check_in


def test_alarms_come_in_the_order_of_the_vocabulary() -> None:
    signs = {WarningSign.CHEST_PAIN, WarningSign.NIGHT_PAIN, WarningSign.FEVER}
    item = summarize(view(injury("a", questionnaire(signs=signs)), DAY0))

    assert item.alarms == (WarningSign.NIGHT_PAIN, WarningSign.FEVER, WarningSign.CHEST_PAIN)


def test_the_last_step_done_can_be_closed_and_asks_for_no_more_days() -> None:
    base = injury("a", questionnaire(morning=0))
    days = climb(base, Step.INTENSITY)
    done = injury(
        "a", base.questionnaire, [*days, *good_days(len(days) + 1, 6, morning=0, during=0)]
    )

    item = summarize(view(done, DAY0 + timedelta(days=len(days) + 8)))

    assert item.step is Step.INTENSITY
    assert item.good_days_needed == 0
    assert item.ready_to_close


def test_a_closed_injury_has_no_check_in_and_cannot_be_checked_in() -> None:
    closed = injury("a").closed(DAY0)

    item = summarize(view(closed, DAY0 + timedelta(days=1)))

    assert item.phase is ProblemPhase.CLOSED
    assert not item.checkin_due and not item.checkin_done and not item.is_open
    assert not item.can_check_in
    assert item.ended_on == DAY0


def test_an_illness_with_fever_stops_the_plan_and_one_above_the_neck_does_not() -> None:
    systemic = summarize(view(illness(IllnessKind.SYSTEMIC), DAY0))
    neck = summarize(view(illness(IllnessKind.NECK_UP, "ill2"), DAY0))

    assert systemic.kind is ProblemKind.ILLNESS
    assert systemic.stops_plan and not neck.stops_plan
    assert not systemic.can_check_in
    assert halted_ids([systemic, neck]) == {"ill1"}


def test_the_last_verdict_tells_what_the_last_check_in_meant() -> None:
    base = injury("a")
    up = summarize(view(injury("a", checkins=good_days(1, 3)), DAY0 + timedelta(days=4)))
    back = summarize(
        view(injury("a", checkins=[*good_days(1, 3), checkin(4, 7, 0)]), DAY0 + timedelta(days=5))
    )

    assert up.last_verdict is Verdict.UP
    assert back.last_verdict is Verdict.BACK
    assert summarize(view(base, DAY0)).last_verdict is None


def test_a_slow_recovery_is_flagged() -> None:
    item = summarize(view(injury("a"), DAY0 + timedelta(days=25)))

    assert item.overdue is Overdue.STEP


# --- Notices --------------------------------------------------------------------------------------


def test_notices_put_the_doctor_first_then_slow_recoveries_then_the_plan() -> None:
    halted = injury("h", questionnaire(signs={WarningSign.NIGHT_PAIN}))
    slow = injury("s")
    today = DAY0 + timedelta(days=25)
    summaries = summaries_of(
        [view(halted, today), view(slow, today), view(illness(IllnessKind.SYSTEMIC), today)]
    )
    warnings = health_warnings_of([PlanHalted(HaltReason.ZONE_SIGN, ("h",))])

    notices = notices_of(summaries, warnings)

    assert [n.kind for n in notices] == [
        NoticeKind.WARNING_SIGN,
        NoticeKind.ILLNESS,
        NoticeKind.OVERDUE,
        NoticeKind.PLAN,
    ]
    assert [asks_for_doctor(n) for n in notices] == [True, True, True, False]


def test_no_records_no_notices() -> None:
    assert notices_of((), ()) == ()


def test_only_the_warnings_that_come_from_injuries_are_kept() -> None:
    kept = health_warnings_of(
        [
            LongDayUnavailable(),
            PlanAdapted(("a",)),
            NoSessionPossible(("a",)),
            PlanHalted(HaltReason.ILLNESS, ("i",)),
        ]
    )

    assert [type(w) for w in kept] == [PlanAdapted, NoSessionPossible, PlanHalted]


# --- Status bar -----------------------------------------------------------------------------------


def test_the_status_facts_count_the_missing_check_ins_and_the_doctor() -> None:
    today = DAY0 + timedelta(days=3)
    items = summaries_of(
        [
            view(injury("a", checkins=good_days(1, 2)), today),
            view(injury("b", questionnaire(signs={WarningSign.FEVER})), today),
            view(injury("c").closed(DAY0), today),
        ]
    )

    assert status_facts_of(items) == StatusFacts(see_doctor=True, checkins_due=2)
    assert status_facts_of(()).is_empty


# --- The days of the calendar -------------------------------------------------------------------


def test_an_open_record_reaches_today_and_not_the_future_and_a_closed_one_ends_when_it_does() -> (
    None
):
    today = date(2026, 6, 30)
    open_span = ProblemSpan(ProblemKind.INJURY, BodyZone.KNEE, None, date(2026, 6, 25), None)
    closed_span = replace(open_span, end=date(2026, 6, 27))

    assert [open_span.covers(date(2026, 6, d), today) for d in (24, 25, 30)] == [False, True, True]
    assert not open_span.covers(date(2026, 7, 1), today)
    assert [closed_span.covers(date(2026, 6, d), today) for d in (26, 27, 28)] == [
        True,
        True,
        False,
    ]


def test_spans_come_from_the_summaries() -> None:
    items = summaries_of([view(injury("a"), DAY0), view(illness(), DAY0)])

    kinds = [(s.kind, s.zone, s.illness_kind) for s in spans_of(items)]

    assert kinds == [
        (ProblemKind.INJURY, BodyZone.SHIN, None),
        (ProblemKind.ILLNESS, None, IllnessKind.SYSTEMIC),
    ]


# --- The drafts of the forms --------------------------------------------------------------------


def test_a_questionnaire_draft_becomes_the_questionnaire() -> None:
    draft = QuestionnaireDraft(onset_day=DAY0, rest=1, walking=2, running=NO_ANSWER, next_morning=3)

    q = questionnaire_of(draft)

    assert q is not None
    assert q.pain.running is None
    assert (q.pain.rest.value, q.pain.next_morning.value) == (1, 3)


@pytest.mark.parametrize("field", ["rest", "walking", "next_morning"])
@pytest.mark.parametrize("value", [-1, 11, 100])
def test_pain_out_of_the_scale_is_not_a_questionnaire(field: str, value: int) -> None:
    changes: dict[str, Any] = {field: value}
    assert questionnaire_of(replace(QuestionnaireDraft(onset_day=DAY0), **changes)) is None


def test_notes_too_long_or_with_control_characters_cannot_be_saved() -> None:
    assert not notes_problem("")
    assert not notes_problem("x" * 2000)
    assert notes_problem("x" * 2001)
    assert notes_problem("bell\x07")
    assert not notes_problem("two\nlines\tand a tab")


def test_the_injury_opens_today_and_not_before_the_pain_began() -> None:
    draft = QuestionnaireDraft(onset_day=DAY0)

    assert injury_of(draft, InjuryId("a"), DAY0) is not None
    assert injury_of(draft, InjuryId("a"), DAY0 - timedelta(days=1)) is None
    assert injury_of(replace(draft, notes="x" * 2001), InjuryId("a"), DAY0) is None


def test_strong_rest_pain_and_ticked_signs_are_alarms() -> None:
    base = QuestionnaireDraft(onset_day=DAY0)

    assert alarms_of(base) == frozenset()
    assert alarms_of(replace(base, rest=5)) == {WarningSign.STRONG_REST_PAIN}
    assert alarms_of(replace(base, rest=4, signs=frozenset({WarningSign.FEVER}))) == {
        WarningSign.FEVER
    }
    assert alarms_of(replace(base, rest=99)) == frozenset()


def test_a_check_in_draft_round_trips() -> None:
    day = DAY0 + timedelta(days=1)
    for draft in (
        CheckInDraft(),
        CheckInDraft(3, 1, frozenset({WarningSign.CHEST_PAIN})),
        CheckInDraft(NO_ANSWER, 10),
    ):
        saved = checkin_of(day, draft)
        assert saved is not None
        assert draft_of_checkin(saved) == draft
    assert draft_of_checkin(None) == CheckInDraft()
    assert checkin_of(day, CheckInDraft(11, 0)) is None


def test_an_illness_cannot_begin_before_1900() -> None:
    assert illness_of(IllnessKind.NECK_UP, DAY0, InjuryId("a")) is not None
    assert illness_of(IllnessKind.NECK_UP, date(1899, 12, 31), InjuryId("a")) is None


def test_the_injury_of_a_draft_is_an_injury() -> None:
    result = injury_of(QuestionnaireDraft(onset_day=DAY0), InjuryId("a"), DAY0)

    assert isinstance(result, Injury)
