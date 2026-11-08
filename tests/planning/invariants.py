# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""The constraints every plan must respect (T-PIANO-02), checked from the outside: only the
plan's public data and the rules of `PLAN.md`, never the engine's own helpers."""

from __future__ import annotations

from collections import defaultdict
from datetime import timedelta

from ponos_coach.goals.domain import HARD_TYPES, INTENSE_TYPES, Priority, SessionType
from ponos_coach.planning.domain import Phase, Plan, PlanDay

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
MAX_RAMP_AFTER_INJURY = 3.0
CTL_DAYS = 42.0
EPSILON = 1e-6


def _weeks(plan: Plan) -> dict[object, list[PlanDay]]:
    weeks: dict[object, list[PlanDay]] = defaultdict(list)
    for day in plan.days:
        weeks[day.day - timedelta(days=day.day.weekday())].append(day)
    return weeks


def _is_hard(day: PlanDay) -> bool:
    return day.race is not None or (
        day.session is not None and day.session.session_type in HARD_TYPES
    )


def _check_days(plan: Plan) -> list[str]:
    problems: list[str] = []
    inputs = plan.inputs
    allowed = inputs.choices.for_sport(inputs.plan_sport())
    for day in plan.days:
        if day.session is None:
            continue
        minutes = inputs.availability.minutes_on(day.day.weekday())
        if day.session.minutes > minutes:
            problems.append(f"{day.day}: {day.session.minutes} min on a day with {minutes}")
        if day.session.session_type not in allowed:
            problems.append(f"{day.day}: {day.session.session_type} was not chosen")
        long_day = inputs.availability.long_day
        if day.session.session_type.value == "long" and day.day.weekday() != long_day:
            problems.append(f"{day.day}: long session off the long day")
        if day.race is not None:
            problems.append(f"{day.day}: session on a race day")
    return problems


def _check_weeks(plan: Plan) -> list[str]:
    problems: list[str] = []
    cap = plan.inputs.availability.max_week_minutes
    for start, days in _weeks(plan).items():
        minutes = sum(d.session.minutes for d in days if d.session)
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


def _check_distribution(start: object, days: list[PlanDay]) -> list[str]:
    """80/20 by sessions and hard days: at most 25% of the sessions intense, at most 2 intense
    if there are fewer than 6 sessions, at most 3 hard days (the long run counts)."""
    types = [d.session.session_type for d in days if d.session]
    intense = sum(t in INTENSE_TYPES for t in types)
    hard = sum(t in HARD_TYPES for t in types)
    problems: list[str] = []
    allowed = (
        0
        if len(types) < MIN_SESSIONS_FOR_INTENSE
        else max(1, len(types) * MAX_INTENSE_SHARE_PERCENT // 100)
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
    a_races = sorted((r.day, r.distance_m) for r in plan.inputs.races if r.priority is Priority.A)
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
    Not checked when the weekly hours are fewer than the minutes the user has on all his days
    (the hours decide which sessions to drop), after an injury, from almost no fitness, or when
    no easy type is chosen (the taper is made of easy sessions)."""
    inputs = plan.inputs
    available = inputs.availability
    chosen = inputs.choices.for_sport(inputs.plan_sport())
    shortest = next((m for t, m in FILLER_MINUTES if t in chosen), None)
    if (
        inputs.ramp_reduced
        or inputs.fitness.ctl < 10
        or shortest is None
        or available.max_week_minutes < sum(available.minutes_on(w) for w in range(7))
    ):
        return []
    problems: list[str] = []
    for start, days in _weeks(plan).items():
        if len(days) < 7 or any(d.phase is not Phase.TAPER for d in days):
            continue
        free = [
            d
            for d in days
            if d.session is None and available.minutes_on(d.day.weekday()) >= shortest
        ]
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


def violations(plan: Plan) -> list[str]:
    """Every broken constraint of the plan, in words (empty = the plan is valid)."""
    return [
        *_check_days(plan),
        *_check_weeks(plan),
        *_check_hard_days(plan),
        *_check_week_quotas(plan),
        *_check_quality_before_races(plan),
        *_check_taper_frequency(plan),
        *_check_ramp(plan),
        *_check_unloads(plan),
        *_check_projection(plan),
    ]
