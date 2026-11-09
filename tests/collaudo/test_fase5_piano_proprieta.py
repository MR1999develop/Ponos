# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""T-PIANO-01 and T-PIANO-02 as properties, with an oracle written again from the rules in
`PLAN.md` and the project skill (not the invariants module of the unit tests): random goals,
availability, session types, fitness, injury flag and start day.

Rules checked on every plan: ramp (CTL gain per week, training days only), hours (day and week),
only available days and chosen types, the long run on its day, at most 25% intense sessions (and
the minimum of one from three sessions), at most 3 hard days, never two hard days in a row (nor next
to a race), no intense session in the 72 hours before a race, stepped taper before an A race (7, 14
or 21 days, never rising towards the race), the projection recomputed from the planned loads, the
quotas of each week, determinism, independence from the order of the goals and an exact write/read.
"""

from __future__ import annotations

import random
from collections import defaultdict
from dataclasses import replace
from datetime import date, timedelta
from itertools import pairwise

import pytest
from hypothesis import HealthCheck, given, settings
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
from ponos_coach.goals.domain import (
    FitnessGoal,
    GoalId,
    Priority,
    RaceGoal,
    SessionChoices,
    SessionType,
    WeeklyAvailability,
)
from ponos_coach.load.domain import HeartRateLimits, Physiology, Sex, ThresholdHeartRate
from ponos_coach.planning.domain import (
    FitnessState,
    GoalBeyondHorizon,
    Phase,
    Plan,
    PlanDay,
    PlanInputs,
    PlanRefusal,
    build_plan,
)
from ponos_coach.planning.plan_codec import decode_plan, digest

INTENSE = {SessionType.MEDIUM, SessionType.THRESHOLD, SessionType.VO2MAX}
HARD = INTENSE | {SessionType.LONG}
HORIZON = 364
MAX_RAMP, MAX_RAMP_INJURY = 5.0, 3.0
TAPER_DAYS = {"short": 7, "half": 14, "long": 21}
EPSILON = 1e-6


def week_of(day: date) -> date:
    return day - timedelta(days=day.weekday())


def race_class(distance_m: int) -> str:
    return "short" if distance_m <= 10_000 else "half" if distance_m <= 21_097 else "long"


def is_hard(day: PlanDay) -> bool:
    return day.session is not None and day.session.session_type in HARD


def violations(plan: Plan, today: date) -> list[str]:  # noqa: PLR0912, PLR0915 - rule by rule
    problems: list[str] = []
    inputs = plan.inputs
    days = plan.days
    if not days:
        return ["a plan without days"]
    if days[0].day != today + timedelta(days=1):
        problems.append("the plan does not start tomorrow")
    if len(days) > HORIZON:
        problems.append(f"{len(days)} days beyond the horizon")
    for earlier, later in pairwise(days):
        if later.day - earlier.day != timedelta(days=1):
            problems.append(f"a hole after {earlier.day}")
    sport = inputs.plan_sport()
    allowed = inputs.choices.for_sport(sport)
    by_day = {d.day: d for d in days}
    race_days = {d.day for d in days if d.race is not None}

    # --- day by day ---
    for d in days:
        if d.session is not None:
            if d.day.weekday() not in inputs.availability.days:
                problems.append(f"{d.day}: a session on a day the user cannot train")
            # the threshold test is a measurement, not one of the chosen types (ADR 015)
            if d.session.session_type not in allowed and d.session.threshold_test is None:
                problems.append(f"{d.day}: {d.session.session_type.value} was not chosen")
            if (
                d.session.session_type is SessionType.LONG
                and d.day.weekday() != inputs.availability.long_day
            ):
                problems.append(f"{d.day}: the long run is off its day")
            if d.day in race_days:
                problems.append(f"{d.day}: a session on a race day")
            if d.session.minutes < 1:
                problems.append(f"{d.day}: an empty session")
        expected = (d.session.load if d.session else 0.0) + (d.race.load if d.race else 0.0)
        if abs(d.load - expected) > 1e-6:
            problems.append(f"{d.day}: the load of the day is not the sum of its parts")

    # --- week by week ---
    weeks: dict[date, list[PlanDay]] = defaultdict(list)
    for d in days:
        weeks[week_of(d.day)].append(d)
    for start, group in weeks.items():
        sessions = [d.session for d in group if d.session is not None]
        total = sum(s.minutes for s in sessions)
        if len(sessions) > inputs.availability.sessions_per_week:
            problems.append(f"week {start}: {len(sessions)} sessions over the chosen number")
        longs = [s.minutes for s in sessions if s.session_type is SessionType.LONG]
        if longs and any(
            s.minutes >= max(longs)
            for s in sessions
            if s.session_type in (SessionType.EASY, SessionType.MEDIUM, SessionType.ALTERNATIVE)
        ):
            problems.append(f"week {start}: the long session is not the longest")
        if total > inputs.availability.max_week_minutes:
            problems.append(
                f"week {start}: {total} min over {inputs.availability.max_week_minutes}"
            )
        intense = sum(s.session_type in INTENSE for s in sessions)
        tests = sum(s.threshold_test is not None for s in sessions)
        cap = max(tests, 0 if len(sessions) < 3 else max(1, len(sessions) * 25 // 100))
        if intense > cap:
            problems.append(f"week {start}: {intense} intense of {len(sessions)} sessions")
        if intense > 3 or (len(sessions) < 6 and intense > 2):
            problems.append(f"week {start}: {intense} intense sessions over the absolute limits")
        if sum(is_hard(d) for d in group) > 3:
            problems.append(f"week {start}: more than 3 hard days")
        row = next((w for w in plan.weeks if w.week_start == start), None)
        if row is None:
            problems.append(f"week {start}: missing from the summary")
        elif row.session_minutes != total or row.session_count != len(sessions):
            problems.append(f"week {start}: the summary disagrees with the sessions")

    # --- hard days never touch each other, nor a race ---
    for earlier, later in pairwise(days):
        if is_hard(earlier) and is_hard(later):
            problems.append(f"{earlier.day}: two hard days in a row")
        if (is_hard(earlier) and later.race is not None) or (
            earlier.race is not None and is_hard(later)
        ):
            problems.append(f"{earlier.day}: a hard day next to a race")

    # --- 72 hours before any race ---
    for race_day in race_days:
        for back in (1, 2, 3):
            before = by_day.get(race_day - timedelta(days=back))
            if (
                before is not None
                and before.session is not None
                and before.session.session_type in INTENSE
            ):
                problems.append(f"{before.day}: intense session {back} days before a race")

    # --- stepped taper before every A race ---
    for d in days:
        if d.race is None or d.race.priority is not Priority.A:
            continue
        if any(0 < abs((r.day - d.day).days) <= 31 for r in inputs.races):
            # tapers and recoveries of close races share days: no rule per race; the races
            # beyond the horizon count too (their taper starts inside the plan)
            continue
        length = 0
        while (
            e := by_day.get(d.day - timedelta(days=length + 1))
        ) is not None and e.phase is Phase.TAPER:
            length += 1
        full = TAPER_DAYS[race_class(d.race.distance_m)]
        if length > full:
            problems.append(f"{d.day}: a taper of {length} days, the longest is {full}")
        if (d.day - days[0].day).days >= full and length != full:
            problems.append(f"{d.day}: a taper of {length} days with room for {full}")

    # --- ramp, projection ---
    ramp = MAX_RAMP_INJURY if inputs.after_injury else MAX_RAMP
    ctl, atl = inputs.fitness.ctl, inputs.fitness.atl
    if len(plan.projection) != len(days):
        problems.append("projection and days differ in length")
    for d, point in zip(days, plan.projection, strict=False):
        if abs(point.tsb - (ctl - atl)) > 1e-3:
            problems.append(f"{d.day}: the form of the morning is not CTL - ATL of the day before")
        ctl += (d.load - ctl) / 42
        atl += (d.load - atl) / 7
        if abs(point.ctl - ctl) > 1e-3 or abs(point.atl - atl) > 1e-3 or point.day != d.day:
            problems.append(f"{d.day}: the projection is not the PMC of the planned loads")
    # the gain of the training days of each week, from the CTL the week starts with (a race
    # raises that CTL but is not a training choice), at most the ramp in proportion to the days
    for start, group in weeks.items():
        first = days.index(group[0])
        ctl0 = inputs.fitness.ctl if first == 0 else plan.projection[first - 1].ctl
        gain = ctl0
        for d in group:
            gain += ((d.session.load if d.session else 0.0) - gain) / 42
        week_ramp = ramp * len(group) / 7
        if gain - ctl0 > week_ramp + 1e-3:
            problems.append(f"week {start}: CTL gain {gain - ctl0:.3f} over {week_ramp:.3f}")

    # --- goals ---
    for race in inputs.races:
        inside = race.day <= days[-1].day
        planned = by_day.get(race.day)
        if inside and (
            planned is None or planned.race is None or planned.race.goal_id != race.goal_id.value
        ):
            problems.append(f"{race.goal_id.value}: the race is not in the plan")
        if not inside and not any(isinstance(w, GoalBeyondHorizon) for w in plan.warnings):
            problems.append(f"{race.goal_id.value}: beyond the horizon without a warning")
    return problems


# --- Random inputs --------------------------------------------------------------------------------


@st.composite
def _availability(draw: st.DrawFn) -> WeeklyAvailability:
    days = draw(st.sets(st.integers(0, 6), max_size=7))
    if not days:
        return WeeklyAvailability.empty()
    return WeeklyAvailability(
        frozenset(days),
        draw(st.integers(1, len(days))),
        draw(st.sampled_from(sorted(days))),
        draw(st.sampled_from([0, 60, 180, 300, 420, 600, 900, 1500, 3000])),
    )


_types = st.sets(st.sampled_from(list(SessionType)), min_size=1)
_distances = st.sampled_from([1_500, 5_000, 10_000, 10_001, 21_097, 21_098, 42_195, 80_000])
_starts = st.integers(0, 13).map(lambda n: date(2026, 3, 2) + timedelta(days=n))
PHYSIOLOGY = Physiology(HeartRateLimits(50, 190), ThresholdHeartRate(170), Sex.MALE)
_retests: st.SearchStrategy[RetestStatus] = st.sampled_from(
    [
        NoTest(),
        NoTest(),
        NotYet(date(2026, 4, 1), date(2026, 4, 15)),
        Recommended(date(2026, 3, 1), date(2026, 3, 15)),
        Overdue(5, date(2026, 2, 20)),
    ]
)


@st.composite
def scenarios(draw: st.DrawFn) -> tuple[PlanInputs, date]:
    today = draw(_starts)
    offsets = draw(st.lists(st.integers(-2, 400), max_size=5, unique=True))
    races = tuple(
        RaceGoal(
            GoalId(f"r{i}"),
            today + timedelta(days=offset),
            draw(st.sampled_from(list(Priority))),
            Sport.RUNNING,
            draw(_distances),
            draw(st.one_of(st.none(), st.integers(600, 20_000))),
        )
        for i, offset in enumerate(offsets)
    )
    form = tuple(
        FitnessGoal(
            GoalId(f"f{i}"),
            today + timedelta(days=draw(st.integers(1, 400))),
            draw(st.floats(0, 150)),
        )
        for i in range(draw(st.integers(0, 2)))
    )
    inputs = PlanInputs(
        races,
        form,
        draw(_availability()),
        SessionChoices.of({Sport.RUNNING: draw(_types)}),
        PHYSIOLOGY,
        FitnessState(draw(st.floats(0, 140)), draw(st.floats(0, 200))),
        threshold_pace_s_per_km=draw(st.one_of(st.none(), st.integers(180, 600))),
        after_injury=draw(st.booleans()),
        retest=draw(_retests),
        test_protocol=draw(st.sampled_from(list(ThresholdProtocol))),
    )
    return inputs, today


def check_scenario(inputs: PlanInputs, today: date) -> None:
    result = build_plan(inputs, today)
    if isinstance(result, PlanRefusal):
        return
    assert violations(result, today) == []
    text = result.to_json()
    assert text.isascii()
    rebuilt = build_plan(inputs, today)
    assert isinstance(rebuilt, Plan)
    assert rebuilt.to_json() == text  # same inputs, same bytes
    shuffled_races = list(inputs.races)
    random.Random(len(text)).shuffle(shuffled_races)
    other = build_plan(
        replace(
            inputs,
            races=tuple(shuffled_races),
            fitness_goals=tuple(reversed(inputs.fitness_goals)),
        ),
        today,
    )
    assert isinstance(other, Plan)
    assert other.to_json() == text  # the order the goals were given in does not matter
    again = decode_plan(text, digest(text))
    assert again.to_json() == text  # exact write and read


@given(scenarios())
@settings(max_examples=150, deadline=None, suppress_health_check=[HealthCheck.too_slow])
def test_t_piano_02_every_plan_respects_the_rules(scenario: tuple[PlanInputs, date]) -> None:
    check_scenario(*scenario)


@pytest.mark.collaudo
@given(scenarios())
@settings(max_examples=3000, deadline=None, suppress_health_check=[HealthCheck.too_slow])
def test_t_piano_02_every_plan_respects_the_rules_long(scenario: tuple[PlanInputs, date]) -> None:
    check_scenario(*scenario)


# --- The oracle itself is not vacuous: damaged plans are caught -----------------------------------
def _good_plan() -> tuple[Plan, date]:
    today = date(2026, 3, 2)
    inputs = PlanInputs(
        (
            RaceGoal(
                GoalId("a"), today + timedelta(days=84), Priority.A, Sport.RUNNING, 21_097, None
            ),
        ),
        (),
        WeeklyAvailability(frozenset(range(7)), 7, 6, 600),
        SessionChoices.of({Sport.RUNNING: set(SessionType)}),
        PHYSIOLOGY,
        FitnessState(40.0, 40.0),
    )
    plan = build_plan(inputs, today)
    assert isinstance(plan, Plan)
    assert violations(plan, today) == []
    return plan, today


def _with_day(plan: Plan, index: int, **changes: object) -> Plan:
    days = list(plan.days)
    days[index] = replace(days[index], **changes)  # type: ignore[arg-type]
    return replace(plan, days=tuple(days))


def _a_race(name: str, day: date, distance_m: int) -> RaceGoal:
    return RaceGoal(GoalId(name), day, Priority.A, Sport.RUNNING, distance_m, None)


@pytest.mark.parametrize(
    "races",
    [
        # a race whose own taper is cut short because it falls in the taper of a later race
        # that lies beyond the horizon (the plan ends on 2027-03-01)
        [
            _a_race("r3", date(2027, 2, 24), 1500),
            _a_race("r4", date(2027, 3, 2), 10_001),
        ],
        # consecutive A races: the day between them is not a taper of either
        [_a_race("r0", date(2026, 3, 3), 1500), _a_race("r1", date(2026, 3, 4), 1500)],
    ],
    ids=["race_in_the_taper_of_one_beyond_the_horizon", "consecutive_a_races"],
)
def test_d_f5_close_a_races_keep_the_rules_of_the_taper(races: list[RaceGoal]) -> None:
    inputs = PlanInputs(
        tuple(races),
        (),
        WeeklyAvailability(frozenset({6}), 1, 6, 60),
        SessionChoices.of({Sport.RUNNING: frozenset({SessionType.EASY})}),
        PHYSIOLOGY,
        FitnessState(0.0, 0.0),
    )

    check_scenario(inputs, date(2026, 3, 2))


def test_the_oracle_catches_a_session_longer_than_the_day_allows() -> None:
    plan, today = _good_plan()
    index = next(i for i, d in enumerate(plan.days) if d.session is not None)
    session = plan.days[index].session
    assert session is not None
    broken = _with_day(plan, index, session=replace(session, minutes=999))
    assert any("999 min" in p for p in violations(broken, today))


def test_the_oracle_catches_an_intense_session_the_day_before_a_race() -> None:
    plan, today = _good_plan()
    index = next(i for i, d in enumerate(plan.days) if d.race is not None) - 1
    template = next(
        d.session
        for d in plan.days
        if d.session is not None and d.session.session_type is SessionType.THRESHOLD
    )
    broken = _with_day(plan, index, session=template)
    found = violations(broken, today)
    assert any("days before a race" in p for p in found)
    assert any("next to a race" in p for p in found)


def test_the_oracle_catches_a_projection_that_is_not_the_pmc_of_the_loads() -> None:
    plan, today = _good_plan()
    points = list(plan.projection)
    points[5] = replace(points[5], ctl=points[5].ctl + 3.0)
    assert any(
        "projection" in p for p in violations(replace(plan, projection=tuple(points)), today)
    )


def test_the_oracle_catches_a_plan_that_does_not_start_tomorrow() -> None:
    plan, today = _good_plan()
    assert any("tomorrow" in p for p in violations(plan, today + timedelta(days=1)))
