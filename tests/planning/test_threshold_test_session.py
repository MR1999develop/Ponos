# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""T-PIANO-10: the threshold test is a session of the plan (ADR 015).

A plan that starts with no valid test, or with the retest window open or gone, puts the test
(the protocol the user chose) in one of its first weeks: a day with time for it, preceded and
followed by an easy day or a rest, never in the 72 hours before a race. With a recent test, an
injury or an illness (open, or closed recently) there is none. A test that went by undone comes
back once, the week after; then the plan stops asking. The test is a hard session: it counts in
the weekly limits and its load is in the ramp.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from ponos_coach.activities.domain import Sport
from ponos_coach.assessment.domain import (
    NoTest,
    NotYet,
    Overdue,
    Recommended,
    RetestStatus,
    ThresholdProtocol,
)
from ponos_coach.garmin.workout_json import workout_payload
from ponos_coach.goals.domain import HARD_TYPES, Priority, SessionType
from ponos_coach.injury.domain import (
    BodyRestriction,
    BodyZone,
    IllnessKind,
    IllnessRestriction,
    InjuryId,
    PainFreeActivity,
    Step,
)
from ponos_coach.planning.domain import (
    BlockRole,
    Phase,
    Plan,
    PlanDay,
    PlannedSession,
    RetestBeforePlan,
    RetestScheduled,
    StepKind,
    WeekKind,
    build_plan,
    defaults,
    missed_threshold_tests,
    workout_from_session,
)
from ponos_coach.planning.domain.sessions import Shape, threshold_test_shape
from ponos_coach.planning.domain.week import Placed, WeekSetup, week_ok
from ponos_coach.planning.plan_codec import decode_plan, digest
from tests.planning.builders import BIG_WEEK, RECENT_TEST, TODAY, availability, inputs, race
from tests.planning.invariants import violations

THIRTY = ThresholdProtocol.THIRTY_MINUTES
TWENTY = ThresholdProtocol.TWENTY_MINUTES
FAR_RACE = TODAY + timedelta(weeks=16)


def planned(**kwargs: object) -> Plan:
    """A 16-week plan for a 10 km race, an hour a day, a test that is due unless said otherwise."""
    kwargs.setdefault("retest", NoTest())
    kwargs.setdefault("avail", BIG_WEEK)
    races = kwargs.pop("races", [race("a", FAR_RACE)])
    result = build_plan(inputs(races, **kwargs), TODAY)  # type: ignore[arg-type]
    assert isinstance(result, Plan)
    assert violations(result) == []
    return result


def threshold_tests_of(plan: Plan) -> list[PlanDay]:
    return [d for d in plan.days if d.session is not None and d.session.threshold_test]


def week_of(day: date) -> date:
    return day - timedelta(days=day.weekday())


# --- A new plan without a recent test ------------------------------------------------------------
@pytest.mark.parametrize(
    "status",
    [
        NoTest(),
        Recommended(TODAY - timedelta(days=2), TODAY + timedelta(days=12)),
        Overdue(4, TODAY),
    ],
    ids=["no test yet", "retest window open", "retest window gone"],
)
def test_a_plan_with_a_test_that_is_due_has_it_in_its_first_week(status: RetestStatus) -> None:
    plan = planned(retest=status)
    (day,) = threshold_tests_of(plan)
    assert week_of(day.day) == week_of(plan.days[0].day)
    assert plan.retest_proposal == RetestScheduled(day.day)


def test_with_a_recent_test_the_plan_has_none() -> None:
    plan = planned(retest=NotYet(TODAY + timedelta(days=30), TODAY + timedelta(days=44)))
    assert threshold_tests_of(plan) == []
    assert not isinstance(plan.retest_proposal, RetestScheduled)


def test_the_test_is_the_protocol_the_user_chose() -> None:
    for protocol, minutes in ((THIRTY, 55), (TWENTY, 45)):
        (day,) = threshold_tests_of(planned(test_protocol=protocol))
        session = day.session
        assert session is not None
        assert session.threshold_test is protocol
        assert session.session_type is SessionType.THRESHOLD
        assert session.minutes == minutes
        warmup, work, cooldown = session.blocks
        assert (warmup.role, warmup.minutes) == (BlockRole.WARMUP, 15)
        assert (work.role, work.minutes, work.repeats) == (
            BlockRole.WORK,
            protocol.duration_s // 60,
            1,
        )
        assert (cooldown.role, cooldown.minutes) == (BlockRole.COOLDOWN, 10)


def test_the_test_is_planned_even_if_the_user_chose_no_threshold_session() -> None:
    (day,) = threshold_tests_of(planned(types=[SessionType.EASY, SessionType.LONG]))
    assert day.session is not None


def test_a_sport_without_a_test_gets_none() -> None:
    walking = planned(
        races=[race("w", FAR_RACE, sport=Sport.WALKING)],
        types=[SessionType.EASY],
        choices_for=Sport.WALKING,
    )
    assert threshold_tests_of(walking) == []
    cycling = planned(
        races=[race("c", FAR_RACE, sport=Sport.CYCLING, distance_m=100_000)],
        types=[SessionType.EASY, SessionType.THRESHOLD],
        choices_for=Sport.CYCLING,
    )
    assert len(threshold_tests_of(cycling)) == 1


# --- Where in the week ---------------------------------------------------------------------------
def test_the_test_is_on_a_day_with_easy_days_around_it_and_not_next_to_the_long_session() -> None:
    plan = planned()
    (day,) = threshold_tests_of(plan)
    by_day = {d.day: d for d in plan.days}
    for offset in (-1, 1):
        neighbour = by_day.get(day.day + timedelta(days=offset))
        if neighbour is not None and neighbour.session is not None:
            assert neighbour.session.session_type not in HARD_TYPES
            assert neighbour.race is None
    long_days = [
        d.day for d in plan.days if d.session and d.session.session_type is SessionType.LONG
    ]
    assert all(abs((day.day - other).days) >= 2 for other in long_days)


def test_the_test_goes_on_a_session_day_that_is_not_the_long_day() -> None:
    two_days = availability((2, 5), long_day=5, max_week_minutes=600)
    (day,) = threshold_tests_of(planned(avail=two_days))
    assert day.day.weekday() == 2
    chosen = availability(range(7), sessions=3, long_day=6, max_week_minutes=600)
    assert {d.day.weekday() for d in threshold_tests_of(planned(avail=chosen))} <= (
        chosen.session_days - {chosen.long_day}
    )


def test_a_first_week_with_no_fitting_day_leaves_the_second_week() -> None:
    # the only day with time is the long day, which the long session takes first: no test that
    # week; the plan never has a long session and a test on the same day, nor next to each other
    saturday_long = availability((5,), long_day=5, max_week_minutes=600)
    plan = planned(avail=saturday_long, types=[SessionType.LONG, SessionType.EASY])
    assert threshold_tests_of(plan) == []


# --- A race close by ------------------------------------------------------------------------------
def test_a_race_in_three_days_leaves_no_test() -> None:
    plan = planned(races=[race("a", TODAY + timedelta(days=3))])
    assert threshold_tests_of(plan) == []


def test_a_test_never_falls_in_the_72_hours_before_a_race_and_moves_after_it() -> None:
    c_race = race("c", TODAY + timedelta(days=3), priority=Priority.C)
    plan = planned(races=[race("a", FAR_RACE), c_race])
    (day,) = threshold_tests_of(plan)
    gap = (c_race.day - day.day).days
    assert not 0 <= gap <= defaults.NO_QUALITY_DAYS_BEFORE_RACE
    assert day.day > c_race.day  # the days of the first week before the race are out
    assert abs(gap) >= 2  # and the one after it is next to a hard day


# --- Injuries ----------------------------------------------------------------------------------
@pytest.mark.parametrize(
    "problem",
    [
        BodyRestriction(
            InjuryId("i"), BodyZone.UPPER_BODY, Step.INTENSITY, frozenset(PainFreeActivity)
        ),
        BodyRestriction(InjuryId("i"), BodyZone.SHIN, Step.ACTIVATION, frozenset(PainFreeActivity)),
        IllnessRestriction(InjuryId("i"), IllnessKind.NECK_UP),
    ],
    ids=["upper body at the last step", "shin at the first step", "illness"],
)
def test_an_open_injury_or_illness_leaves_no_test(
    problem: BodyRestriction | IllnessRestriction,
) -> None:
    result = build_plan(
        inputs([race("a", FAR_RACE)], avail=BIG_WEEK, retest=NoTest(), injuries=[problem]), TODAY
    )
    assert isinstance(result, Plan)
    assert threshold_tests_of(result) == []


def test_a_recent_return_from_an_injury_leaves_no_test() -> None:
    assert threshold_tests_of(planned(after_injury=True)) == []


# --- The test that was missed -------------------------------------------------------------------
def test_a_missed_test_comes_back_in_the_week_after_it_was_missed_once_only() -> None:
    wednesday = TODAY + timedelta(days=2)
    (day,) = threshold_tests_of(planned(missed_tests=[wednesday]))
    assert week_of(day.day) == week_of(wednesday) + timedelta(days=7)
    assert (
        threshold_tests_of(planned(missed_tests=[wednesday - timedelta(days=14), wednesday])) == []
    )


def test_a_missed_test_of_a_week_long_gone_comes_back_from_tomorrow() -> None:
    (day,) = threshold_tests_of(planned(missed_tests=[TODAY - timedelta(days=40)]))
    assert week_of(day.day) == week_of(plan_start())


def plan_start() -> date:
    return TODAY + timedelta(days=1)


def test_the_test_done_on_its_day_is_done_whatever_the_plan_says() -> None:
    # the activity of the day was applied as a test: the window is "not yet", the plan has none
    plan = planned(retest=NotYet(TODAY + timedelta(days=42), TODAY + timedelta(days=56)))
    assert threshold_tests_of(plan) == []


# --- A hard session ------------------------------------------------------------------------------
def test_the_test_is_a_hard_session_whose_load_is_in_the_plan() -> None:
    plan = planned()
    (day,) = threshold_tests_of(plan)
    assert day.session is not None
    assert day.session.session_type in HARD_TYPES
    assert day.session.load > 0
    assert day.load == pytest.approx(day.session.load)
    point = plan.projection[plan.days.index(day)]
    before = plan.projection[plan.days.index(day) - 1] if day is not plan.days[0] else None
    assert before is None or point.atl > before.atl  # the load went into the projection


def test_the_test_is_paid_from_the_week_load_not_added_to_it() -> None:
    with_test = planned(ctl=20.0, atl=20.0)
    without = planned(ctl=20.0, atl=20.0, retest=RECENT_TEST)
    assert with_test.weeks[0].load <= without.weeks[0].load * 1.10


def test_the_week_takes_the_test_as_its_intense_session_even_when_it_has_few_sessions() -> None:
    easy = Placed(Shape(SessionType.EASY, 30, 0), Phase.BASE)
    test = Placed(threshold_test_shape(THIRTY), Phase.BASE)
    plain_threshold = Placed(Shape(SessionType.THRESHOLD, 55, 0), Phase.BASE)
    setup = WeekSetup((), Phase.BUILD, False, 6, frozenset(SessionType), frozenset())
    start = TODAY
    assert week_ok(setup, {start: test, start + timedelta(days=2): easy})
    assert not week_ok(setup, {start: plain_threshold, start + timedelta(days=2): easy})


def test_the_test_counts_as_an_intense_session_in_a_week_of_few_sessions() -> None:
    two_days = availability((1, 4), long_day=4, max_week_minutes=600)
    plan = planned(avail=two_days)
    (day,) = threshold_tests_of(plan)
    week = [d for d in plan.days if week_of(d.day) == week_of(day.day) and d.session]
    intense = [d for d in week if d.session and d.session.session_type is SessionType.THRESHOLD]
    assert len(week) <= 2 and len(intense) == 1


def test_the_other_intense_sessions_make_room_for_the_test() -> None:
    plan = planned()
    (day,) = threshold_tests_of(plan)
    week = [d for d in plan.days if week_of(d.day) == week_of(day.day) and d.session]
    intense = [
        d for d in week if d.session and d.session.session_type in HARD_TYPES - {SessionType.LONG}
    ]
    assert len(intense) <= max(1, len(week) * defaults.MAX_INTENSE_SESSION_SHARE_PERCENT // 100)


def test_the_test_is_never_in_an_unload_week() -> None:
    for missed in ([], [TODAY + timedelta(days=2)]):
        plan = planned(missed_tests=missed)
        kinds = {w.week_start: w.kind for w in plan.weeks}
        (day,) = threshold_tests_of(plan)
        assert kinds[week_of(day.day)] is not WeekKind.UNLOAD


# --- Determinism, saving and the watch -----------------------------------------------------------
def test_same_inputs_same_plan_whatever_the_order_of_the_missed_tests() -> None:
    a = planned(missed_tests=[TODAY - timedelta(days=30), TODAY - timedelta(days=9)])
    b = planned(missed_tests=[TODAY - timedelta(days=9), TODAY - timedelta(days=30)])
    assert (
        a.to_json()
        == b.to_json()
        == planned(missed_tests=[TODAY - timedelta(days=30), TODAY - timedelta(days=9)]).to_json()
    )


@settings(max_examples=25, deadline=None)
@given(
    st.sampled_from([THIRTY, TWENTY]),
    st.lists(st.integers(-60, -1), max_size=3, unique=True),
    st.integers(0, 30),
)
def test_the_plan_with_a_test_is_deterministic_and_valid(
    protocol: ThresholdProtocol, missed_offsets: list[int], ctl: int
) -> None:
    kwargs: dict[str, object] = {
        "test_protocol": protocol,
        "missed_tests": [TODAY + timedelta(days=o) for o in missed_offsets],
        "ctl": float(ctl),
        "atl": float(ctl),
    }
    first, second = planned(**kwargs), planned(**kwargs)
    assert first.to_json() == second.to_json()
    assert len(threshold_tests_of(first)) <= 1


def test_the_saved_plan_with_a_test_reads_back_as_it_was() -> None:
    plan = planned()
    text = plan.to_json()
    assert decode_plan(text, digest(text)).to_json() == text


def test_the_test_becomes_a_structured_workout_with_its_own_name() -> None:
    (day,) = threshold_tests_of(planned(test_protocol=THIRTY))
    assert day.session is not None
    spec = workout_from_session(day.session)
    assert spec.name == "Ponos · Threshold test 55 min"
    assert [(s.kind, s.seconds) for s in spec.steps] == [
        (StepKind.WARMUP, 900),
        (StepKind.INTERVAL, 1800),
        (StepKind.COOLDOWN, 600),
    ]
    assert spec.total_seconds == 55 * 60


def test_the_test_workout_is_accepted_by_the_garmin_payload_without_any_call() -> None:
    (day,) = threshold_tests_of(planned(test_protocol=TWENTY))
    assert day.session is not None
    payload = workout_payload(workout_from_session(day.session))
    assert payload["workoutName"] == "Ponos · Threshold test 45 min"


def test_a_threshold_test_is_never_another_type_nor_at_race_pace() -> None:
    (day,) = threshold_tests_of(planned())
    session = day.session
    assert session is not None
    with pytest.raises(ValueError, match="threshold session"):
        PlannedSession(
            session.sport,
            SessionType.EASY,
            session.minutes,
            session.blocks,
            session.load,
            threshold_test=THIRTY,
        )
    with pytest.raises(ValueError, match="threshold session"):
        PlannedSession(
            session.sport,
            SessionType.THRESHOLD,
            session.minutes,
            session.blocks,
            session.load,
            race_pace=True,
            threshold_test=THIRTY,
        )


def test_a_test_given_up_is_asked_before_the_plan() -> None:
    plan = planned(missed_tests=[TODAY - timedelta(days=20), TODAY - timedelta(days=6)])
    assert threshold_tests_of(plan) == []
    assert plan.retest_proposal == RetestBeforePlan()


def test_the_plan_inputs_refuse_a_missed_test_given_twice() -> None:
    with pytest.raises(ValueError, match="twice"):
        replace(inputs([race("a", FAR_RACE)]), missed_tests=(TODAY, TODAY))


# --- Which planned tests were missed -----------------------------------------------------------
def _planned_test(day: date) -> PlanDay:
    (found,) = (d for d in threshold_tests_of(planned()))
    assert found.session is not None
    return PlanDay(day, found.phase, found.session, None, found.load)


def test_a_planned_test_is_missed_if_its_day_is_past_and_no_test_was_applied_since() -> None:
    monday, wednesday = TODAY, TODAY + timedelta(days=2)
    frozen = [_planned_test(monday), _planned_test(wednesday)]
    today = TODAY + timedelta(days=3)
    assert missed_threshold_tests(frozen, [], today) == (monday, wednesday)
    assert missed_threshold_tests(frozen, [wednesday], today) == ()  # done on its day
    assert missed_threshold_tests(frozen, [monday], today) == (wednesday,)  # only monday's done


def test_a_test_of_today_may_still_be_done() -> None:
    assert missed_threshold_tests([_planned_test(TODAY)], [], TODAY) == ()


def test_days_without_a_test_session_are_not_missed() -> None:
    easy = PlanDay(TODAY, Phase.BASE, None, None, 0.0)
    assert missed_threshold_tests([easy], [], TODAY + timedelta(days=5)) == ()
