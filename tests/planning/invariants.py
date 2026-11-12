# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""The constraints every plan must respect (T-PIANO-02), checked from the outside: only the
plan's public data and the rules of `PLAN.md`, never the engine's own helpers."""

from __future__ import annotations

from collections import defaultdict
from datetime import timedelta

from ponos_coach.activities.domain import Sport
from ponos_coach.assessment.domain import NotYet
from ponos_coach.goals.domain import HARD_TYPES, INTENSE_TYPES, Priority, SessionType
from ponos_coach.planning.domain import Phase, Plan, PlanDay, WeekKind
from ponos_coach.planning.domain.target import rule_distance_m

MAX_RAMP = 5.0
MAX_INTENSE_SHARE_PERCENT = 25
MIN_SESSIONS_FOR_INTENSE = 3
MAX_HARD_DAYS = 3
FEW_SESSIONS = 6
MAX_INTENSE_FEW_SESSIONS = 2
NO_QUALITY_DAYS = 3
# The taper is made of the first of these that is chosen, with its shortest duration in minutes.
FILLER_MINUTES = ((SessionType.EASY, 20), (SessionType.ALTERNATIVE, 30), (SessionType.RECOVERY, 20))
SHORT_RACE_M = 10_000
MAX_SHARE_OF_LONG = 75  # percent of the long session that an easy or recovery one may take
_BELOW_LONG = (SessionType.EASY, SessionType.MEDIUM, SessionType.ALTERNATIVE, SessionType.RECOVERY)
_AT_MOST_THREE_QUARTERS = (SessionType.EASY, SessionType.RECOVERY)
EASY_MAX_MINUTES = 90  # the longest easy session
MAX_RAMP_AFTER_INJURY = 3.0
CTL_DAYS = 42.0
EPSILON = 1e-6
HILL_MIN_SLOPE = 20  # metres of climb per kilometre from which an A race has hill work


def _weeks(plan: Plan) -> dict[object, list[PlanDay]]:
    weeks: dict[object, list[PlanDay]] = defaultdict(list)
    for day in plan.days:
        weeks[day.day - timedelta(days=day.day.weekday())].append(day)
    return weeks


def _makes_up_a_long(plan: Plan, day: PlanDay) -> bool:
    """A long session off the long day is allowed only to make up one missed earlier in the
    plan's first week, when the long day of that week is gone."""
    first = plan.days[0].day
    monday = first - timedelta(days=first.weekday())
    if not monday <= day.day < monday + timedelta(days=7):
        return False
    long_day = monday + timedelta(days=plan.inputs.availability.long_day)
    return long_day < first and any(
        m.session_type is SessionType.LONG and monday <= m.day < first
        for m in plan.inputs.missed_sessions
    )


def _is_hard(day: PlanDay) -> bool:
    return day.race is not None or (
        day.session is not None and day.session.session_type in HARD_TYPES
    )


def _check_days(plan: Plan) -> list[str]:
    problems: list[str] = []
    inputs = plan.inputs
    allowed = inputs.choices.for_sport_or_suggested(inputs.plan_sport())
    for day in plan.days:
        if day.session is None:
            continue
        if day.day.weekday() not in inputs.availability.session_days:
            problems.append(f"{day.day}: a session on a day that is not a session day")
        if day.session.session_type not in allowed and day.session.threshold_test is None:
            problems.append(f"{day.day}: {day.session.session_type} was not chosen")
        sport = day.session.sport
        if sport not in (inputs.plan_sport(), Sport.STRENGTH, Sport.OTHER) and (
            day.session.session_type not in inputs.choices.for_sport_or_suggested(sport)
            or day.session.session_type not in (SessionType.EASY, SessionType.LONG)
            or not any(r.sport is sport and r.day > day.day for r in inputs.races)
        ):
            problems.append(f"{day.day}: a {sport} session that no race of that sport asks for")
        long_day = inputs.availability.long_day
        if (
            day.session.session_type.value == "long"
            and day.day.weekday() != long_day
            and not _makes_up_a_long(plan, day)
        ):
            problems.append(f"{day.day}: long session off the long day")
        if day.race is not None:
            problems.append(f"{day.day}: session on a race day")
    return problems


def _check_weeks(plan: Plan) -> list[str]:
    problems: list[str] = []
    cap = plan.inputs.availability.max_week_minutes
    for start, days in _weeks(plan).items():
        minutes = sum(d.session.minutes for d in days if d.session)
        count = sum(1 for d in days if d.session)
        chosen = plan.inputs.availability.sessions_per_week
        if count > chosen:
            problems.append(f"week {start}: {count} sessions over the {chosen} chosen")
        intense = sum(1 for d in days if d.session and d.session.session_type in INTENSE_TYPES)
        if minutes > cap:
            problems.append(f"week {start}: {minutes} min over the maximum {cap}")
        if intense > 3:
            problems.append(f"week {start}: {intense} intense sessions")
        problems.extend(_check_distribution(start, days))
        if (
            all(d.phase is Phase.BASE for d in days if d.phase is not Phase.RECOVERY)
            and intense > 2
        ):
            problems.append(f"week {start}: {intense} intense sessions in base")
    return problems


def _check_long_is_the_longest(plan: Plan) -> list[str]:
    """In a week with a long session it is the longest session by minutes: longer than every
    easy, medium or alternative one, and the easy and recovery ones are at most 75% of it."""
    problems: list[str] = []
    for start, days in _weeks(plan).items():
        sessions = [d.session for d in days if d.session]
        longs = [s.minutes for s in sessions if s.session_type is SessionType.LONG]
        if not longs:
            continue
        top = max(longs)
        for s in sessions:
            kind = s.session_type
            if kind in _BELOW_LONG and s.minutes >= top:
                problems.append(f"week {start}: a {kind.value} of {s.minutes} min, long {top}")
            if kind in _AT_MOST_THREE_QUARTERS and s.minutes * 100 > MAX_SHARE_OF_LONG * top:
                problems.append(
                    f"week {start}: a {kind.value} of {s.minutes} min over 75% of {top}"
                )
    return problems


def _check_distribution(start: object, days: list[PlanDay]) -> list[str]:
    """80/20 by sessions and hard days: at most 25% of the sessions intense, at most 2 intense
    if there are fewer than 6 sessions, at most 3 hard days (the long run counts)."""
    types = [d.session.session_type for d in days if d.session]
    tests = sum(1 for d in days if d.session and d.session.threshold_test is not None)
    intense = sum(t in INTENSE_TYPES for t in types)
    hard = sum(t in HARD_TYPES for t in types)
    problems: list[str] = []
    # the threshold test is an intense session that the week always makes room for
    allowed = max(
        tests,
        0
        if len(types) < MIN_SESSIONS_FOR_INTENSE
        else max(1, len(types) * MAX_INTENSE_SHARE_PERCENT // 100),
    )
    if intense > allowed:
        problems.append(f"week {start}: {intense} intense of {len(types)} sessions")
    if len(types) < FEW_SESSIONS and intense > MAX_INTENSE_FEW_SESSIONS:
        problems.append(f"week {start}: {intense} intense with only {len(types)} sessions")
    if hard > MAX_HARD_DAYS:
        problems.append(f"week {start}: {hard} hard days")
    return problems


def _check_week_quotas(plan: Plan) -> list[str]:
    """The quotas exposed by each week are the ones of its sessions."""
    problems: list[str] = []
    for week in plan.weeks:
        days = [d for d in plan.days if week.week_start <= d.day < week.week_start + timedelta(7)]
        sessions = [d.session for d in days if d.session]
        total = sum(b.minutes * b.repeats for s in sessions for b in s.blocks)
        low = sum(b.minutes * b.repeats for s in sessions for b in s.blocks if b.zone <= 2)
        intense = sum(s.session_type in INTENSE_TYPES for s in sessions)
        if week.session_count != len(sessions):
            problems.append(f"week {week.week_start}: session count")
        if abs(week.low_zone_share - (low / total if total else 1.0)) > 1e-3:
            problems.append(f"week {week.week_start}: low-zone share")
        share = intense / len(sessions) if sessions else 0.0
        if abs(week.intense_session_share - share) > 1e-3:
            problems.append(f"week {week.week_start}: intense share")
    return problems


def _check_quality_before_races(plan: Plan) -> list[str]:
    """No intense session in the 72 hours before a race, and no VO2max in the peak of a half
    marathon or a marathon."""
    problems: list[str] = []
    by_day = {d.day: d for d in plan.days}
    for race in plan.inputs.races:
        for back in range(1, NO_QUALITY_DAYS + 1):
            day = by_day.get(race.day - timedelta(days=back))
            if day is not None and day.session and day.session.session_type in INTENSE_TYPES:
                problems.append(
                    f"{day.day}: intense session {back} days before {race.goal_id.value}"
                )
    a_races = sorted(
        (r.day, rule_distance_m(r.sport, r.distance_m, r.ascent_m))
        for r in plan.inputs.races
        if r.priority is Priority.A
    )
    for day in plan.days:
        session = day.session
        if day.phase is not Phase.PEAK or session is None:
            continue
        if session.session_type is not SessionType.VO2MAX:
            continue
        distance = next((m for d, m in a_races if d > day.day), 0)
        if distance > SHORT_RACE_M:
            problems.append(f"{day.day}: VO2max in the peak of a {distance} m race")
    return problems


def _check_taper_frequency(plan: Plan) -> list[str]:
    """A full week of taper keeps the number of sessions: every day with time for one has it.
    Not checked when the weekly hours are fewer than a long easy session on each session day
    (the hours decide which sessions to drop), after an injury, from almost no fitness, or when
    no easy type is chosen (the taper is made of easy sessions)."""
    inputs = plan.inputs
    available = inputs.availability
    chosen = inputs.choices.for_sport_or_suggested(inputs.plan_sport())
    shortest = next((m for t, m in FILLER_MINUTES if t in chosen), None)
    if (
        inputs.ramp_reduced
        or inputs.fitness.ctl < 10
        or shortest is None
        or available.max_week_minutes < available.sessions_per_week * EASY_MAX_MINUTES
    ):
        return []
    problems: list[str] = []
    for start, days in _weeks(plan).items():
        if len(days) < 7 or any(d.phase is not Phase.TAPER for d in days):
            continue
        free = [d for d in days if d.session is None and d.day.weekday() in available.session_days]
        if free:
            problems.append(f"week {start}: {len(free)} taper days without a session")
    return problems


def _check_hard_days(plan: Plan) -> list[str]:
    return [
        f"{a.day}: two hard days in a row"
        for a, b in zip(plan.days, plan.days[1:], strict=False)
        if _is_hard(a) and _is_hard(b) and not (a.race and b.race)
    ]


def _check_ramp(plan: Plan) -> list[str]:
    """CTL gained in a week by the training days alone (a race is not a training choice)."""
    cap = MAX_RAMP_AFTER_INJURY if plan.inputs.ramp_reduced else MAX_RAMP
    problems: list[str] = []
    ctl = plan.inputs.fitness.ctl
    for start, days in _weeks(plan).items():
        before = ctl
        trained = ctl
        for day in days:
            training = day.load - (day.race.load if day.race else 0.0)
            trained += (training - trained) / CTL_DAYS
        ctl = plan.projection[plan.days.index(days[-1])].ctl
        if trained - before > cap * len(days) / 7 + EPSILON:
            problems.append(f"week {start}: ramp {trained - before:.2f} over the maximum")
    return problems


def _check_unloads(plan: Plan) -> list[str]:
    """Before an A race the taper days are taper days, without hard sessions; the days before a B
    race are mini-taper days."""
    problems: list[str] = []
    by_day = {d.day: d for d in plan.days}
    for race in plan.inputs.races:
        if race.priority is Priority.C or race.day not in by_day:
            continue
        wanted = {Phase.TAPER} if race.priority is Priority.A else {Phase.TAPER, Phase.MINI_TAPER}
        eve = by_day.get(race.day - timedelta(days=1))
        if eve is None or eve.race is not None:
            continue
        if eve.phase not in wanted:
            problems.append(f"{race.goal_id.value}: the eve of the race is {eve.phase}")
        if _is_hard(eve):
            problems.append(f"{race.goal_id.value}: hard session on the eve of the race")
    return problems


def _check_projection(plan: Plan) -> list[str]:
    ctl, atl = plan.inputs.fitness.ctl, plan.inputs.fitness.atl
    problems: list[str] = []
    for day, point in zip(plan.days, plan.projection, strict=True):
        morning = ctl - atl
        ctl += (day.load - ctl) / CTL_DAYS
        atl += (day.load - atl) / 7.0
        if abs(point.ctl - ctl) > EPSILON or abs(point.tsb - morning) > EPSILON:
            problems.append(f"{day.day}: projection differs from the recurrence")
    return problems


def _check_threshold_test(plan: Plan) -> list[str]:
    """The test is planned only when it is due and nothing forbids it, once, with the protocol
    the user chose, never on a day before the week it was asked for, and it is a hard day."""
    inputs = plan.inputs
    tests = [d for d in plan.days if d.session and d.session.threshold_test is not None]
    if not tests:
        return []
    problems: list[str] = []
    if len(tests) > 1:
        problems.append(f"{len(tests)} threshold tests in one plan")
    if isinstance(inputs.retest, NotYet):
        problems.append("a threshold test with a recent test")
    if inputs.ramp_reduced:
        problems.append("a threshold test with an injury, an illness or a recent return")
    if len(inputs.missed_tests) >= 2:
        problems.append("a threshold test that was already missed twice")
    for day in tests:
        session = day.session
        assert session is not None
        if session.threshold_test is not inputs.test_protocol:
            problems.append(f"{day.day}: not the protocol the user chose")
        if inputs.missed_tests:
            last = inputs.missed_tests[-1]
            if day.day < last - timedelta(days=last.weekday()) + timedelta(days=7):
                problems.append(f"{day.day}: the missed test comes back before the next week")
        if not _is_hard(day):
            problems.append(f"{day.day}: the test is not a hard day")
    return problems


def _check_hills(plan: Plan) -> list[str]:
    """Hill work (a session with an elevation gain) only for a steep A race still to come, only in
    the build and the peak, never in an unload week, at most one hill-repeats session a week, a
    long run or an intense session, and never with a pace target."""
    problems: list[str] = []
    unload = {w.week_start for w in plan.weeks if w.kind is WeekKind.UNLOAD}
    steep = sorted(
        r.day
        for r in plan.inputs.races
        if r.priority is Priority.A and r.ascent_m * 1000 >= HILL_MIN_SLOPE * r.distance_m
    )
    repeats: dict[object, int] = defaultdict(int)
    for item in plan.days:
        session = item.session
        if session is None or session.ascent_m == 0:
            continue
        monday = item.day - timedelta(days=item.day.weekday())
        if item.phase not in (Phase.BUILD, Phase.PEAK):
            problems.append(f"{item.day}: hill work in the phase {item.phase}")
        if monday in unload:
            problems.append(f"{item.day}: hill work in an unload week")
        if not any(day > item.day for day in steep):
            problems.append(f"{item.day}: hill work with no steep A race ahead")
        if session.session_type not in HARD_TYPES:
            problems.append(f"{item.day}: hill work in a {session.session_type.value} session")
        if any(
            b.pace_slow_s_per_km or b.pace_fast_s_per_km or b.race_pace_s_per_km
            for b in session.blocks
        ):
            problems.append(f"{item.day}: a pace target on a hill session")
        if session.session_type in INTENSE_TYPES:
            repeats[monday] += 1
    problems.extend(f"week {w}: {n} hill-repeats sessions" for w, n in repeats.items() if n > 1)
    return problems


def violations(plan: Plan) -> list[str]:
    """Every broken constraint of the plan, in words (empty = the plan is valid)."""
    return [
        *_check_days(plan),
        *_check_weeks(plan),
        *_check_long_is_the_longest(plan),
        *_check_hard_days(plan),
        *_check_week_quotas(plan),
        *_check_quality_before_races(plan),
        *_check_taper_frequency(plan),
        *_check_ramp(plan),
        *_check_unloads(plan),
        *_check_projection(plan),
        *_check_threshold_test(plan),
        *_check_hills(plan),
    ]
