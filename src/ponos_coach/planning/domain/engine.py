# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""The plan engine: `build_plan` turns goals, availability and fitness into a plan.

Pure and deterministic: no clock, no randomness, no I/O. The date of today is an argument.
The steps: refuse what cannot be planned, lay out the macrocycle backwards from the goals, then
go week by week from tomorrow with a simulated PMC, sizing each week's sessions to the load that
climbs the CTL towards the next goal without exceeding the maximum ramp or the user's hours.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta

from ponos_coach.activities.domain import Sport
from ponos_coach.assessment.domain import NoTest, Overdue
from ponos_coach.goals.domain import HARD_TYPES, INTENSE_TYPES, Priority, RaceGoal, SessionType
from ponos_coach.injury.domain import PlanRestriction
from ponos_coach.planning.domain import defaults
from ponos_coach.planning.domain.health import LIMITED_FILLERS, allowed_types, restriction_of
from ponos_coach.planning.domain.inputs import PlanInputs, PlanRefusal, RefusalReason
from ponos_coach.planning.domain.macrocycle import Anchor, Macrocycle, build_macrocycle
from ponos_coach.planning.domain.phases import (
    LOADED_PHASES,
    PHASE_RANK,
    TAPER_PHASES,
    Phase,
    RaceClass,
)
from ponos_coach.planning.domain.plan import (
    CloseARaces,
    CtlTargetNotReached,
    GoalBeyondHorizon,
    LongDayUnavailable,
    MissingIntenseSessions,
    MissingLongSession,
    NoSessionPossible,
    Plan,
    PlanAdapted,
    PlanDay,
    PlanHalted,
    PlannedRace,
    PlanWarning,
    PlanWeek,
    PmcPoint,
    RecoveryInserted,
    RetestBeforePlan,
    RetestInUnloadWeek,
    RetestNotPlaceable,
    RetestProposal,
    TargetAssessment,
    TargetTimeUnreachable,
    WeekKind,
)
from ponos_coach.planning.domain.projection import Fitness, ctl_after, decay, step
from ponos_coach.planning.domain.sessions import (
    RacePace,
    ZoneRanges,
    ZoneRates,
    make_session,
    race_load,
    rates_by_sport,
    raw_blocks,
    session_load,
    zone_ranges,
)
from ponos_coach.planning.domain.target import (
    TargetClass,
    best_time_by_race_s,
    classify_target,
    race_class_for,
    recovery_days_for,
    taper_days_for,
    target_ctl_for_distance,
)
from ponos_coach.planning.domain.week import FILLERS, DayInfo, Placed, Sizer, WeekSetup, plan_week

_DAYS_PER_WEEK = 7
_RUNNING = (Sport.RUNNING, Sport.TRAIL)
_KIND_OF_PHASE = {
    Phase.MAINTENANCE: WeekKind.MAINTENANCE,
    Phase.BASE: WeekKind.BASE,
    Phase.BUILD: WeekKind.BUILD,
    Phase.PEAK: WeekKind.PEAK,
    Phase.RECOVERY: WeekKind.RECOVERY,
    Phase.MINI_TAPER: WeekKind.TAPER,
    Phase.TAPER: WeekKind.TAPER,
    Phase.RACE: WeekKind.RACE,
}


@dataclass(slots=True)
class _Run:
    """The state that moves from week to week while the plan is built."""

    fitness: Fitness
    ref_daily: float
    loaded_weeks: int = 0
    last_hard: date | None = None


@dataclass(frozen=True, slots=True)
class _RaceTarget:
    """An A race as the weeks before it see it: its length class and the pace it is run at."""

    day: date
    race_class: RaceClass
    pace_s_per_km: int | None


@dataclass(frozen=True, slots=True)
class _Context:
    """The fixed things every week needs."""

    inputs: PlanInputs
    macro: Macrocycle
    sport: Sport
    sizer: Sizer
    rates_of: dict[SessionType, ZoneRates]
    rates: dict[Sport, ZoneRates]
    ranges: dict[Sport, ZoneRanges]
    races: dict[date, PlannedRace]
    max_ramp: float
    targets: tuple[_RaceTarget, ...]
    restriction: PlanRestriction
    chosen: frozenset[SessionType]


@dataclass(frozen=True, slots=True)
class _WeekResult:
    days: tuple[PlanDay, ...]
    points: tuple[PmcPoint, ...]
    unload: bool


def build_plan(inputs: PlanInputs, today: date) -> Plan | PlanRefusal:
    """The plan from tomorrow, or the reason there is none.

    Refused: no goals, a goal today or in the past, two races on one day, no availability, no
    session type chosen for the sport of the plan. Otherwise the plan always exists; what is
    wrong with it (a target out of reach, a missing session type, two A races too close...)
    is in its warnings. Same inputs and same `today`, same plan, whatever the order of the goals.
    """
    refusal = _refusal(inputs, today)
    if refusal is not None:
        return refusal
    norm = inputs.normalised()
    start = today + timedelta(days=1)
    end = min(_last_goal_day(norm), today + timedelta(days=defaults.HORIZON_DAYS))
    recover = _needs_recovery(norm)
    unload_first = defaults.RECOVERY_DAYS_ADAPTATION if recover else 0
    macro = _macrocycle(norm, start, end, unload_first)
    max_ramp = (
        defaults.MAX_RAMP_PER_WEEK_AFTER_INJURY if norm.ramp_reduced else defaults.MAX_RAMP_PER_WEEK
    )
    context = _context(norm, macro, today, max_ramp)
    run = _Run(Fitness(norm.fitness.ctl, norm.fitness.atl), norm.fitness.ctl)
    results = [_plan_one_week(context, run, week) for week in _week_groups(start, end)]
    days = tuple(day for result in results for day in result.days)
    projection = tuple(point for result in results for point in result.points)
    weeks = tuple(_summary(result) for result in results)
    return _assemble(context, today, (days, weeks, projection), recover)


# --- Refusals ----------------------------------------------------------------------------------
def _refusal(inputs: PlanInputs, today: date) -> PlanRefusal | None:
    goals = _goal_days(inputs)
    if not goals:
        return PlanRefusal(RefusalReason.NO_GOALS)
    past = sorted(goal_id for goal_id, day in goals if day <= today)
    if past:
        return PlanRefusal(RefusalReason.GOAL_NOT_IN_FUTURE, tuple(past))
    days = Counter(race.day for race in inputs.races)
    clash = sorted(r.goal_id.value for r in inputs.races if days[r.day] > 1)
    if clash:
        return PlanRefusal(RefusalReason.GOALS_ON_SAME_DAY, tuple(clash))
    if inputs.availability.is_empty:
        return PlanRefusal(RefusalReason.NO_AVAILABILITY)
    if not inputs.choices.for_sport(inputs.plan_sport()):
        return PlanRefusal(RefusalReason.NO_SESSION_TYPES)
    return None


def _goal_days(inputs: PlanInputs) -> list[tuple[str, date]]:
    """(id, date) of every goal, races and fitness goals."""
    races = [(r.goal_id.value, r.day) for r in inputs.races]
    return races + [(g.goal_id.value, g.day) for g in inputs.fitness_goals]


def _last_goal_day(inputs: PlanInputs) -> date:
    return max(day for _, day in _goal_days(inputs))


def _needs_recovery(inputs: PlanInputs) -> bool:
    """True if tomorrow's form is too low or the fatigue too high: recovery days first."""
    fitness = inputs.fitness
    if fitness.tsb_tomorrow < defaults.RECOVERY_TSB:
        return True
    return (
        fitness.ctl >= defaults.RATIO_MIN_CTL
        and fitness.atl > defaults.RECOVERY_ATL_CTL_RATIO * fitness.ctl
    )


# --- Set-up ------------------------------------------------------------------------------------
def _anchors(inputs: PlanInputs) -> list[Anchor]:
    """A races and fitness goals; a fitness goal on the day of an A race sets its CTL."""
    fitness_on = {g.day: g for g in reversed(inputs.fitness_goals)}
    used: set[str] = set()
    anchors: list[Anchor] = []
    for race in inputs.races:
        if race.priority is not Priority.A:
            continue
        ctl = target_ctl_for_distance(race.distance_m)
        goal = fitness_on.get(race.day)
        if goal is not None:
            ctl = goal.ctl
            used.add(goal.goal_id.value)
        anchors.append(
            Anchor(
                race.goal_id.value,
                race.day,
                True,
                taper_days_for(race.distance_m),
                defaults.PEAK_DAYS,
                recovery_days_for(race.distance_m),
                ctl,
            )
        )
    anchors.extend(
        Anchor(g.goal_id.value, g.day, False, 0, 0, 0, g.ctl)
        for g in inputs.fitness_goals
        if g.goal_id.value not in used
    )
    return sorted(anchors, key=lambda a: (a.day, a.goal_id))


def _macrocycle(inputs: PlanInputs, start: date, end: date, unload_first: int) -> Macrocycle:
    b_days = [r.day for r in inputs.races if r.priority is Priority.B]
    return build_macrocycle(
        start, end, _anchors(inputs), b_days, [r.day for r in inputs.races], unload_first
    )


def _session_sport(session_type: SessionType, plan_sport: Sport) -> Sport:
    """Strength is its own sport, the alternative session is 'other', the rest the plan's sport."""
    if session_type is SessionType.STRENGTH:
        return Sport.STRENGTH
    if session_type is SessionType.ALTERNATIVE:
        return Sport.OTHER
    return plan_sport


def _expected_time_s(race: RaceGoal, pace: int | None, days_to_race: int) -> int:
    """Time the plan expects of a race: the target, else the best time possible, else a default."""
    if race.target_time_s is not None:
        return race.target_time_s
    if pace is not None and race.sport in _RUNNING:
        return round(best_time_by_race_s(pace, race.distance_m, days_to_race))
    return max(1, race.distance_m * defaults.DEFAULT_RACE_PACE_S_PER_KM // 1000)


def _context(inputs: PlanInputs, macro: Macrocycle, today: date, max_ramp: float) -> _Context:
    sport = inputs.plan_sport()
    sports = sorted({sport, Sport.STRENGTH, Sport.OTHER, *(r.sport for r in inputs.races)}, key=str)
    rates = rates_by_sport(inputs.physiology, inputs.weights, sports)
    ranges = {s: zone_ranges(inputs.physiology, s, inputs.threshold_pace_s_per_km) for s in sports}
    rates_of = {t: rates[_session_sport(t, sport)] for t in SessionType}
    races = {}
    for race in inputs.races:
        expected = _expected_time_s(race, inputs.threshold_pace_s_per_km, (race.day - today).days)
        races[race.day] = PlannedRace(
            race.goal_id.value,
            race.priority,
            race.sport,
            race.distance_m,
            expected,
            race_load(expected, rates[race.sport]),
        )
    pace = inputs.threshold_pace_s_per_km
    targets = tuple(
        _RaceTarget(
            r.day,
            race_class_for(r.distance_m),
            _race_pace_s_per_km(r, pace, (r.day - today).days),
        )
        for r in inputs.races
        if r.priority is Priority.A
    )
    restriction = restriction_of(inputs.injuries)
    chosen = allowed_types(inputs.choices.for_sport(sport), sport, restriction)
    return _Context(
        inputs,
        macro,
        sport,
        Sizer(rates_of),
        rates_of,
        rates,
        ranges,
        races,
        max_ramp,
        targets,
        restriction,
        chosen,
    )


def _race_pace_s_per_km(race: RaceGoal, pace: int | None, days_to_race: int) -> int | None:
    """Pace the race is expected to be run at, whole s/km: from the target time, else from the
    best time possible (Riegel's prediction from the threshold pace); `None` for a race that is
    not a run or when neither is known (the session then goes by its zone alone)."""
    if race.sport not in _RUNNING:
        return None
    if race.target_time_s is not None:
        time_s = float(race.target_time_s)
    elif pace is not None:
        time_s = best_time_by_race_s(pace, race.distance_m, days_to_race)
    else:
        return None
    return round(time_s * 1000 / race.distance_m)


def _next_target(context: _Context, day: date) -> _RaceTarget | None:
    """The first A race after `day`."""
    return next((t for t in context.targets if t.day > day), None)


def _taper_fraction(phase: Phase, target: _RaceTarget | None, day: date) -> float:
    """Share of the reference load that a day of the taper takes: the step of its week counted
    back from the race (see `defaults.TAPER_FRACTIONS`); a mini taper takes the unload fraction."""
    if phase is Phase.TAPER and target is not None:
        steps = defaults.TAPER_FRACTIONS[target.race_class]
        weeks_before = ((target.day - day).days - 1) // defaults.TAPER_STEP_DAYS
        return steps[max(len(steps) - 1 - weeks_before, 0)]
    return defaults.UNLOAD_FRACTION if phase in TAPER_PHASES else 0.0


def _week_groups(start: date, end: date) -> list[list[date]]:
    """The days from `start` to `end` grouped by ISO week (Monday to Sunday)."""
    groups: list[list[date]] = []
    day = start
    while day <= end:
        if not groups or day.weekday() == 0:
            groups.append([])
        groups[-1].append(day)
        day += timedelta(days=1)
    return groups


# --- One week ----------------------------------------------------------------------------------
def _ramp_for(context: _Context, ctl: float, week_first: date, *, ramping: bool) -> float:
    """CTL points to gain this week: what the next goal needs, at most the maximum ramp. Only a
    week with base or build days gains; peak and maintenance weeks hold the level."""
    if not ramping:
        return 0.0
    for span in context.macro.spans:
        if not span.is_empty and span.last_day >= week_first:
            weeks_left = ((span.last_day - week_first).days + 1) / _DAYS_PER_WEEK
            effective = max(1.0, weeks_left * defaults.LOADED_WEEK_SHARE)
            return min(context.max_ramp, max(0.0, (span.target_ctl - ctl) / effective))
    return 0.0


def _daily_level(
    context: _Context, run: _Run, week: Sequence[date], *, ramping: bool, unload: bool
) -> float:
    """Daily load on the normal days: the load that gains the ramp, an unload week's fraction."""
    if unload:
        return defaults.UNLOAD_FRACTION * run.ref_daily
    days = len(week)
    ramp = _ramp_for(context, run.fitness.ctl, week[0], ramping=ramping)
    level = run.fitness.ctl + ramp * (days / _DAYS_PER_WEEK) / (1.0 - decay(days))
    return max(level, defaults.MIN_DAILY_LOAD)


def _setup(
    context: _Context,
    run: _Run,
    week: Sequence[date],
    week_phase: Phase | None,
    unload: bool,
) -> WeekSetup:
    availability = context.inputs.availability
    race_days = frozenset(context.races)
    infos = tuple(_day_info(context, day) for day in week)
    seed = race_days | ({run.last_hard} if run.last_hard is not None else set())
    monday = week[0] - timedelta(days=week[0].weekday())
    return WeekSetup(
        infos,
        week_phase,
        unload,
        availability.long_day,
        context.chosen,
        frozenset(seed),
        race_days,
        monday.toordinal() // _DAYS_PER_WEEK,
        _fillers(context),
    )


def _fillers(context: _Context) -> tuple[SessionType, ...]:
    """The order of the filler types: the usual one, or the one of a plan with an injury."""
    return LIMITED_FILLERS if context.restriction.limited_by else FILLERS


def _day_minutes(context: _Context, day: date) -> int:
    """Minutes the user has on a day; with an injury on the first running steps, none on every
    other day (sessions on alternate days, by the parity of the date)."""
    if context.restriction.alternate_days and day.toordinal() % 2 == 1:
        return 0
    return context.inputs.availability.minutes_on(day.weekday())


def _day_info(context: _Context, day: date) -> DayInfo:
    phase = context.macro.phase_on(day)
    target = _next_target(context, day)
    return DayInfo(
        day,
        phase,
        _day_minutes(context, day),
        day + timedelta(days=1) in context.races,
        _taper_fraction(phase, target, day),
        None if target is None else target.race_class,
        None if target is None else (target.day - day).days,
    )


def _loads_of(context: _Context, week: Sequence[date], placed: dict[date, Placed]) -> list[float]:
    """Training load of each day of the week (0 where there is no session)."""
    loads = []
    for day in week:
        item = placed.get(day)
        if item is None:
            loads.append(0.0)
        else:
            blocks = raw_blocks(item.shape, item.phase)
            loads.append(session_load(blocks, context.rates_of[item.shape.session_type]))
    return loads


def _realised_daily_load(
    context: _Context, week: Sequence[date], placed: dict[date, Placed], loaded_days: int
) -> float:
    """Average training load per normal day of the week as it was laid out: the reference that
    the unload weeks and the tapers take their fraction of."""
    loads = _loads_of(context, week, placed)
    normal = sum(
        load
        for day, load in zip(week, loads, strict=True)
        if context.macro.phase_on(day) in LOADED_PHASES
    )
    return normal / loaded_days


def _fit_factor(
    context: _Context, run: _Run, week: Sequence[date], placed: dict[date, Placed]
) -> float:
    """How much the week must shrink to respect the weekly hours and the maximum ramp (the
    ramp counts the training days only, a race is not a training choice); 1 or more = fits."""
    minutes = sum(item.shape.minutes for item in placed.values())
    cap = context.inputs.availability.max_week_minutes
    factor = 1.0 if minutes <= cap else cap / minutes
    days = len(week)
    ctl0 = run.fitness.ctl
    delta = ctl_after(ctl0, _loads_of(context, week, placed)) - ctl0
    allowed = context.max_ramp * days / _DAYS_PER_WEEK
    if delta > allowed:
        lost = ctl0 * (1.0 - decay(days))
        factor = min(factor, (allowed + lost) / (delta + lost))
    return factor


def _fit_week(
    context: _Context,
    run: _Run,
    setup: WeekSetup,
    week: Sequence[date],
    budgets: tuple[float, float],
) -> dict[date, Placed]:
    """The week's sessions, shrunk until they fit the hours and the ramp; if they never do, only
    the recovery sessions, and if even those do not fit, a rest week."""
    scale = 1.0
    for _ in range(defaults.SCALE_ATTEMPTS):
        placed = plan_week(
            setup,
            context.sizer,
            budgets[0] * scale,
            budgets[1] * scale,
            keep_frequency=scale == 1.0,
        )
        factor = _fit_factor(context, run, week, placed)
        if factor >= 1.0:
            return placed
        scale *= factor * 0.95
    recovery_only = plan_week(setup, context.sizer, 0.0, 0.0)
    return recovery_only if _fit_factor(context, run, week, recovery_only) >= 1.0 else {}


def _week_phase(context: _Context, week: Sequence[date]) -> tuple[Phase | None, int]:
    """Phase of the week's last normal day, and how many normal days there are."""
    loaded = [d for d in week if context.macro.phase_on(d) in LOADED_PHASES]
    return (context.macro.phase_on(loaded[-1]) if loaded else None), len(loaded)


def _plan_one_week(context: _Context, run: _Run, week: Sequence[date]) -> _WeekResult:
    week_phase, loaded_days = _week_phase(context, week)
    counted = loaded_days >= defaults.MIN_LOADED_DAYS_IN_WEEK
    unload = (
        counted
        and week_phase in (Phase.MAINTENANCE, Phase.BASE, Phase.BUILD)
        and run.loaded_weeks % (defaults.LOADED_WEEKS_PER_MESOCYCLE + 1)
        == defaults.LOADED_WEEKS_PER_MESOCYCLE
    )
    run.loaded_weeks += int(counted)
    ramping = any(context.macro.phase_on(d) in (Phase.BASE, Phase.BUILD) for d in week)
    level = _daily_level(context, run, week, ramping=ramping, unload=unload)
    setup = _setup(context, run, week, week_phase, unload)
    taper_share = sum(d.taper_fraction for d in setup.days)
    budgets = (level * loaded_days, run.ref_daily * taper_share)
    placed = _fit_week(context, run, setup, week, budgets)
    if counted and not unload:
        run.ref_daily = _realised_daily_load(context, week, placed, loaded_days)
    return _advance(context, run, week, placed, unload)


def _advance(
    context: _Context, run: _Run, week: Sequence[date], placed: dict[date, Placed], unload: bool
) -> _WeekResult:
    """Builds the days of the week and moves the PMC through them."""
    days: list[PlanDay] = []
    points: list[PmcPoint] = []
    hard_days: list[date] = []
    for day in week:
        item = placed.get(day)
        session = None
        if item is not None:
            sport = _session_sport(item.shape.session_type, context.sport)
            session = make_session(
                item.shape,
                item.phase,
                sport,
                context.rates[sport],
                context.ranges[sport],
                _race_pace_of(context, day) if item.race_pace else None,
            )
        race = context.races.get(day)
        load = (session.load if session else 0.0) + (race.load if race else 0.0)
        if race is not None or (item is not None and item.shape.session_type in HARD_TYPES):
            hard_days.append(day)
        morning = run.fitness.tsb
        run.fitness = step(run.fitness, load)
        days.append(PlanDay(day, context.macro.phase_on(day), session, race, load))
        points.append(PmcPoint(day, run.fitness.ctl, run.fitness.atl, morning))
    run.last_hard = hard_days[-1] if hard_days else None
    return _WeekResult(tuple(days), tuple(points), unload)


def _race_pace_of(context: _Context, day: date) -> RacePace | None:
    """The race a race-pace session of this day rehearses: the next A race."""
    target = _next_target(context, day)
    if target is None:
        return None
    return RacePace(defaults.RACE_PACE_ZONE[target.race_class], target.pace_s_per_km)


def _shares(days: Sequence[PlanDay]) -> tuple[int, float, float]:
    """Sessions of the days, and the two 80/20 shares: time in Z1-Z2 and intense sessions."""
    sessions = [day.session for day in days if day.session]
    total = low = 0
    for session in sessions:
        for block in session.blocks:
            minutes = block.minutes * block.repeats
            total += minutes
            low += minutes if block.zone <= 2 else 0
    intense = sum(session.session_type in INTENSE_TYPES for session in sessions)
    count = len(sessions)
    return count, (low / total if total else 1.0), (intense / count if count else 0.0)


def _summary(result: _WeekResult) -> PlanWeek:
    phases = Counter(day.phase for day in result.days)
    dominant = max(phases, key=lambda p: (phases[p], PHASE_RANK[p]))
    kind = WeekKind.UNLOAD if result.unload else _KIND_OF_PHASE[dominant]
    minutes = sum(day.session.minutes for day in result.days if day.session)
    week_start = result.days[0].day - timedelta(days=result.days[0].day.weekday())
    count, low_zone, intense = _shares(result.days)
    load = sum(day.load for day in result.days)
    return PlanWeek(week_start, kind, minutes, load, count, low_zone, intense)


# --- Verdicts and warnings ---------------------------------------------------------------------
def _assess_targets(inputs: PlanInputs, today: date) -> tuple[TargetAssessment, ...]:
    pace = inputs.threshold_pace_s_per_km
    out: list[TargetAssessment] = []
    for race in inputs.races:
        if race.target_time_s is None:
            continue
        if pace is None or race.sport not in _RUNNING:
            out.append(TargetAssessment(race.goal_id.value, TargetClass.NOT_ESTIMABLE, None))
            continue
        best = best_time_by_race_s(pace, race.distance_m, (race.day - today).days)
        verdict = classify_target(race.target_time_s, best)
        out.append(TargetAssessment(race.goal_id.value, verdict, round(best)))
    return tuple(out)


def _choice_warnings(inputs: PlanInputs) -> list[PlanWarning]:
    """A race of a half marathon or more needs a long session; every A or B race needs some
    intense work (threshold or VO2max). Chosen types are never added: only said."""
    warnings: list[PlanWarning] = []
    for race in inputs.races:
        if race.priority is Priority.C:
            continue
        chosen = inputs.choices.for_sport(race.sport)
        if race.distance_m >= defaults.HALF_MARATHON_M and SessionType.LONG not in chosen:
            warnings.append(MissingLongSession(race.goal_id.value))
        if not chosen & {SessionType.THRESHOLD, SessionType.VO2MAX}:
            warnings.append(MissingIntenseSessions(race.goal_id.value))
    return warnings


def _health_warnings(context: _Context) -> list[PlanWarning]:
    """What the open injuries and illnesses did to the plan: stopped it, nothing left to schedule,
    or only trimmed it."""
    restriction = context.restriction
    if restriction.halt is not None:
        return [PlanHalted(restriction.halt, restriction.halted_ids)]
    if not restriction.limited_by:
        return []
    if not context.chosen:
        return [NoSessionPossible(restriction.limited_by)]
    return [PlanAdapted(restriction.limited_by)]


def _plan_warnings(
    context: _Context, today: date, targets: Sequence[TargetAssessment], recover: bool
) -> tuple[PlanWarning, ...]:
    inputs = context.inputs
    warnings = _choice_warnings(inputs)
    warnings.extend(_health_warnings(context))
    chosen = inputs.choices.for_sport(context.sport)
    if (
        SessionType.LONG in chosen
        and inputs.availability.minutes_on(inputs.availability.long_day) == 0
    ):
        warnings.append(LongDayUnavailable())
    horizon = today + timedelta(days=defaults.HORIZON_DAYS)
    beyond = sorted((day, goal_id) for goal_id, day in _goal_days(inputs) if day > horizon)
    warnings.extend(GoalBeyondHorizon(goal_id) for _, goal_id in beyond)
    warnings.extend(
        CloseARaces(c.first_id, c.second_id, c.gap_days) for c in context.macro.close_races
    )
    if recover:
        fitness = inputs.fitness
        days = defaults.RECOVERY_DAYS_ADAPTATION
        warnings.append(RecoveryInserted(fitness.tsb_tomorrow, fitness.atl, fitness.ctl, days))
    warnings.extend(
        TargetTimeUnreachable(t.goal_id)
        for t in targets
        if t.classification is TargetClass.UNREACHABLE
    )
    return tuple(warnings)


def _ctl_warnings(context: _Context, projection: Sequence[PmcPoint]) -> list[PlanWarning]:
    """A goal whose CTL is missed by more than the tolerance at the end of its ramp."""
    start = context.macro.start
    last = projection[-1].day
    warnings: list[PlanWarning] = []
    for span in context.macro.spans:
        check = max(span.last_day, start)
        if check > last:
            continue
        projected = projection[(check - start).days].ctl
        if projected < span.target_ctl - defaults.CTL_TOLERANCE:
            warnings.append(CtlTargetNotReached(span.goal_id, projected, span.target_ctl))
    return warnings


def _retest(
    inputs: PlanInputs, weeks: Sequence[PlanWeek], start: date, end: date
) -> RetestProposal:
    """The threshold test goes in an unload week of the retest window (before the plan if there
    is no test yet; any unload week if the window has passed)."""
    status = inputs.retest
    if isinstance(status, NoTest):
        return RetestBeforePlan()
    first, last = (
        (start, end)
        if isinstance(status, Overdue)
        else (max(status.first_day, start), status.last_day)
    )
    for week in weeks:
        week_end = week.week_start + timedelta(days=_DAYS_PER_WEEK - 1)
        if week.kind is WeekKind.UNLOAD and week_end >= first and week.week_start <= last:
            return RetestInUnloadWeek(week.week_start)
    return RetestNotPlaceable()


def _assemble(
    context: _Context,
    today: date,
    parts: tuple[tuple[PlanDay, ...], tuple[PlanWeek, ...], tuple[PmcPoint, ...]],
    recover: bool,
) -> Plan:
    days, weeks, projection = parts
    inputs = context.inputs
    targets = _assess_targets(inputs, today)
    warnings = (
        *_plan_warnings(context, today, targets, recover),
        *_ctl_warnings(context, projection),
    )
    return Plan(
        defaults.METHOD_VERSION,
        defaults.parameter_snapshot(context.max_ramp),
        today,
        inputs,
        days,
        weeks,
        projection,
        targets,
        warnings,
        _retest(inputs, weeks, days[0].day, days[-1].day),
    )
