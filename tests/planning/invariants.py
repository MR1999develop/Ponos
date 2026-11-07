# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""The constraints every plan must respect (T-PIANO-02), checked from the outside: only the
plan's public data and the rules of `PLAN.md`, never the engine's own helpers."""

from __future__ import annotations

from collections import defaultdict
from datetime import timedelta

from ponos_coach.goals.domain import HARD_TYPES, INTENSE_TYPES, Priority
from ponos_coach.planning.domain import Phase, Plan, PlanDay

MAX_RAMP = 5.0
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
        if (
            all(d.phase is Phase.BASE for d in days if d.phase is not Phase.RECOVERY)
            and intense > 2
        ):
            problems.append(f"week {start}: {intense} intense sessions in base")
    return problems


def _check_hard_days(plan: Plan) -> list[str]:
    return [
        f"{a.day}: two hard days in a row"
        for a, b in zip(plan.days, plan.days[1:], strict=False)
        if _is_hard(a) and _is_hard(b) and not (a.race and b.race)
    ]


def _check_ramp(plan: Plan) -> list[str]:
    """CTL gained in a week by the training days alone (a race is not a training choice)."""
    cap = MAX_RAMP_AFTER_INJURY if plan.inputs.after_injury else MAX_RAMP
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
        *_check_ramp(plan),
        *_check_unloads(plan),
        *_check_projection(plan),
    ]
