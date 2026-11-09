# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Plan texts: session types, phases, target verdicts, the notices and their remedies.

The domain gives categories (`SessionType`, `Phase`, `TargetClass`, a refusal, a warning); here we
choose the words. Every sentence is whole, so the translation never composes fragments: the only
joins are commas between whole phrases. Context "Plan" written as a literal: lupdate does not
follow aliases. Health-related numbers (heart rate) serve only the window, never the logs.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import date

from PySide6.QtCore import QCoreApplication, QDate, QLocale

from ponos_coach.goals.domain import FitnessGoal, Priority, RaceGoal, SessionType
from ponos_coach.goals.ports import Goal
from ponos_coach.planning.domain import (
    Block,
    BlockRole,
    CloseARaces,
    CtlTargetNotReached,
    GoalBeyondHorizon,
    LongDayUnavailable,
    MissingIntenseSessions,
    MissingLongSession,
    NoSessionPossible,
    Phase,
    PlanAdapted,
    PlanHalted,
    PlannedSession,
    PlanRefusal,
    RecoveryInserted,
    RefusalReason,
    RetestBeforePlan,
    RetestInUnloadWeek,
    RetestNotPlaceable,
    RetestProposal,
    RetestScheduled,
    TargetClass,
    TargetTimeUnreachable,
)
from ponos_coach.profiles.rebuild_plan import NotBuiltReason
from ponos_coach.ui.goal_draft import (
    FIVE_KM_M,
    HALF_MARATHON_M,
    MARATHON_M,
    TEN_KM_M,
    target_time_text,
)
from ponos_coach.ui.injury_texts import plan_warning_texts
from ponos_coach.ui.plan_presentation import Notice

_SECONDS_PER_MINUTE = 60
_METERS_PER_KM = 1000.0
_KM_DIGITS = 6  # significant digits of a distance in km ("42.195")
_ISO_MONDAY = 1


def short_date_text(day: date) -> str:
    """A date in the locale's short format."""
    return QLocale().toString(QDate(day.year, day.month, day.day), QLocale.FormatType.ShortFormat)


def weekday_name(weekday: int) -> str:
    """The long name of a weekday (0 = Monday) in the locale's words."""
    return QLocale().dayName(weekday + _ISO_MONDAY, QLocale.FormatType.LongFormat)


# --- Names of categories ---------------------------------------------------------------------


def session_type_text(session_type: SessionType) -> str:
    """The name of a session type."""
    texts = {
        SessionType.EASY: QCoreApplication.translate("Plan", "Easy"),
        SessionType.LONG: QCoreApplication.translate("Plan", "Long"),
        SessionType.MEDIUM: QCoreApplication.translate("Plan", "Medium"),
        SessionType.THRESHOLD: QCoreApplication.translate("Plan", "Threshold"),
        SessionType.VO2MAX: QCoreApplication.translate("Plan", "VO2max"),
        SessionType.RECOVERY: QCoreApplication.translate("Plan", "Recovery run"),
        SessionType.STRENGTH: QCoreApplication.translate("Plan", "Strength"),
        SessionType.ALTERNATIVE: QCoreApplication.translate("Plan", "Alternative activity"),
    }
    return texts[session_type]


def phase_text(phase: Phase) -> str:
    """The name of a phase of the macrocycle."""
    texts = {
        Phase.MAINTENANCE: QCoreApplication.translate("Plan", "Maintenance phase"),
        Phase.BASE: QCoreApplication.translate("Plan", "Base phase"),
        Phase.BUILD: QCoreApplication.translate("Plan", "Build phase"),
        Phase.PEAK: QCoreApplication.translate("Plan", "Peak phase"),
        Phase.RECOVERY: QCoreApplication.translate("Plan", "Recovery phase"),
        Phase.MINI_TAPER: QCoreApplication.translate("Plan", "Mini taper"),
        Phase.TAPER: QCoreApplication.translate("Plan", "Taper"),
        Phase.RACE: QCoreApplication.translate("Plan", "Race day"),
    }
    return texts[phase]


def priority_race_text(priority: Priority) -> str:
    """ "Race A", "Race B" or "Race C"."""
    # {priority}: the letter A, B or C.
    return QCoreApplication.translate("Plan", "Race {priority}").format(priority=priority.value)


def target_class_text(target_class: TargetClass) -> str:
    """The verdict on a target time, in one or two words."""
    texts = {
        TargetClass.REALISTIC: QCoreApplication.translate("Plan", "Realistic"),
        TargetClass.AMBITIOUS: QCoreApplication.translate("Plan", "Ambitious"),
        TargetClass.UNREACHABLE: QCoreApplication.translate("Plan", "Not reachable"),
        TargetClass.NOT_ESTIMABLE: QCoreApplication.translate("Plan", "Cannot be estimated"),
    }
    return texts[target_class]


def target_class_explanation(target_class: TargetClass) -> str:
    """What the verdict on a target time means, in a sentence."""
    texts = {
        TargetClass.REALISTIC: QCoreApplication.translate(
            "Plan", "Realistic: the target time is within what you can run by the race."
        ),
        TargetClass.AMBITIOUS: QCoreApplication.translate(
            "Plan", "Ambitious: the target time is a little faster than the expected progress."
        ),
        TargetClass.UNREACHABLE: QCoreApplication.translate(
            "Plan",
            "Not reachable: the target time is out of reach by the race, even with the "
            "expected progress.",
        ),
        TargetClass.NOT_ESTIMABLE: QCoreApplication.translate(
            "Plan",
            "Cannot be estimated: the estimate needs a threshold pace from a running test.",
        ),
    }
    return texts[target_class]


# --- Goals -----------------------------------------------------------------------------------


def distance_name(distance_m: int) -> str:
    """A distance as people say it: "Marathon" for 42 195 m, otherwise in kilometres."""
    names = {
        FIVE_KM_M: QCoreApplication.translate("Plan", "5 km"),
        TEN_KM_M: QCoreApplication.translate("Plan", "10 km"),
        HALF_MARATHON_M: QCoreApplication.translate("Plan", "Half marathon"),
        MARATHON_M: QCoreApplication.translate("Plan", "Marathon"),
    }
    return names.get(distance_m) or distance_km_text(distance_m, with_unit=True)


def distance_km_text(distance_m: int, *, with_unit: bool = False) -> str:
    """A distance in kilometres in the locale's format, without trailing zeros."""
    number = QLocale().toString(distance_m / _METERS_PER_KM, "g", _KM_DIGITS)
    # {distance}: the number of kilometres.
    return (
        QCoreApplication.translate("Plan", "{distance} km").format(distance=number)
        if with_unit
        else number
    )


def goal_label(goal: Goal) -> str:
    """A goal in one phrase: "Race A, Marathon, 9/20/26"."""
    if isinstance(goal, FitnessGoal):
        # {ctl}: the CTL to reach; {date}: the day.
        return QCoreApplication.translate("Plan", "Form goal, CTL {ctl}, {date}").format(
            ctl=QLocale().toString(goal.ctl, "f", 0), date=short_date_text(goal.day)
        )
    # {priority}: A, B or C; {distance}: "Marathon" or "12.5 km"; {date}: the day.
    return QCoreApplication.translate("Plan", "Race {priority}, {distance}, {date}").format(
        priority=goal.priority.value,
        distance=distance_name(goal.distance_m),
        date=short_date_text(goal.day),
    )


def goal_kind_cell_text(goal: Goal) -> str:
    """The kind of a goal for the table: "Race A" or "Form goal"."""
    if isinstance(goal, FitnessGoal):
        return QCoreApplication.translate("Plan", "Form goal")
    return priority_race_text(goal.priority)


def race_short_name(goal: RaceGoal) -> str:
    """A race for a chart marker: its distance."""
    return distance_name(goal.distance_m)


# --- Sessions --------------------------------------------------------------------------------


def _role_text(role: BlockRole) -> str:
    texts = {
        BlockRole.WARMUP: QCoreApplication.translate("Plan", "Warm-up"),
        BlockRole.WORK: QCoreApplication.translate("Plan", "Work"),
        BlockRole.REST: QCoreApplication.translate("Plan", "Rest"),
        BlockRole.COOLDOWN: QCoreApplication.translate("Plan", "Cool-down"),
        BlockRole.STEADY: QCoreApplication.translate("Plan", "Steady"),
    }
    return texts[role]


def pace_text(seconds_per_km: int) -> str:
    """A pace as `m:ss`."""
    minutes, seconds = divmod(seconds_per_km, _SECONDS_PER_MINUTE)
    return f"{minutes}:{seconds:02d}"


def _pace_phrase(slow: int | None, fast: int | None) -> str:
    """The pace range of a block; empty when the block has none."""
    if slow is not None and fast is not None:
        # {slow} and {fast}: the ends of the range as m:ss, slowest first.
        return QCoreApplication.translate("Plan", "pace {slow} to {fast} per km").format(
            slow=pace_text(slow), fast=pace_text(fast)
        )
    if fast is not None:
        # {pace}: m:ss per kilometre.
        return QCoreApplication.translate("Plan", "pace {pace} per km or slower").format(
            pace=pace_text(fast)
        )
    if slow is not None:
        # {pace}: m:ss per kilometre.
        return QCoreApplication.translate("Plan", "pace {pace} per km or faster").format(
            pace=pace_text(slow)
        )
    return ""


def block_text(block: Block) -> str:
    """One block of a session in one line: what, how long, which zone, pace and heart rate."""
    if block.repeats > 1:
        # {repeats}: how many times; %n: the minutes of each repetition.
        duration = QCoreApplication.translate(
            "Plan", "{repeats} x %n min", "", block.minutes
        ).format(repeats=block.repeats)
    else:
        duration = QCoreApplication.translate("Plan", "%n min", "", block.minutes)
    # {zone}: the intensity zone, 1 to 5.
    zone = QCoreApplication.translate("Plan", "zone {zone}").format(zone=block.zone)
    # {low} and {high}: the heart rate range in beats per minute.
    heart_rate = QCoreApplication.translate("Plan", "{low} to {high} bpm").format(
        low=block.hr_low_bpm, high=block.hr_high_bpm
    )
    pace = _pace_phrase(block.pace_slow_s_per_km, block.pace_fast_s_per_km)
    # The pace leads when there is one (running): it is what the watch follows.
    details = ", ".join(part for part in (duration, zone, pace, heart_rate) if part)
    # {role}: the kind of block; {details}: its duration, zone, heart rate and pace.
    return QCoreApplication.translate("Plan", "{role}: {details}").format(
        role=_role_text(block.role), details=details
    )


def session_name(session: PlannedSession) -> str:
    """The name of a session: its type, or "Threshold test" when it is the test itself."""
    if session.threshold_test is not None:
        return QCoreApplication.translate("Plan", "Threshold test")
    return session_type_text(session.session_type)


def session_title(session: PlannedSession) -> str:
    """A session in one line: "Threshold, 60 min" ("Threshold test, 55 min" for the test)."""
    # {type}: the session type; %n: the minutes.
    return QCoreApplication.translate("Plan", "{type}, %n min", "", session.minutes).format(
        type=session_name(session)
    )


def session_lines(session: PlannedSession) -> tuple[str, ...]:
    """The session in full: the title, the sport if it is not the plan's, and its blocks."""
    lines = [session_title(session)]
    lines.extend(block_text(block) for block in session.blocks)
    return tuple(lines)


def planned_load_text(load: float) -> str:
    """A load with its unit."""
    # {load}: the estimated training load.
    return QCoreApplication.translate("Plan", "{load} hrTSS").format(
        load=QLocale().toString(round(load))
    )


def duration_clock_text(seconds: int) -> str:
    """A time as `h:mm:ss`."""
    return target_time_text(seconds)


# --- Notices ---------------------------------------------------------------------------------


def notice_texts(notice: Notice, label_of: Callable[[str], str]) -> tuple[str, str]:
    """What is wrong (a sentence) and what to do about it (a sentence), in plain words.

    `label_of` gives a phrase for a goal id, so the sentence says which goal."""
    subject = notice.subject
    if isinstance(subject, NotBuiltReason):
        return _needs_data_texts(subject)
    if isinstance(subject, PlanRefusal):
        return _refusal_texts(subject, label_of)
    return _warning_texts(subject, label_of)


def _needs_data_texts(reason: NotBuiltReason) -> tuple[str, str]:
    texts = {
        NotBuiltReason.MISSING_PHYSIOLOGY: (
            QCoreApplication.translate("Plan", "Your heart-rate limits are missing."),
            QCoreApplication.translate(
                "Plan", "Enter your minimum, maximum and threshold heart rate in Edit profile."
            ),
        )
    }
    return texts.get(
        reason, (QCoreApplication.translate("Plan", "The plan could not be made."), "")
    )


def _refusal_texts(refusal: PlanRefusal, label_of: Callable[[str], str]) -> tuple[str, str]:
    goals = "; ".join(label_of(goal_id) for goal_id in refusal.goal_ids)
    match refusal.reason:
        case RefusalReason.NO_GOALS:
            return (
                QCoreApplication.translate("Plan", "You have no goal yet."),
                QCoreApplication.translate(
                    "Plan", "Add a race or a form goal and the plan starts from tomorrow."
                ),
            )
        case RefusalReason.GOAL_NOT_IN_FUTURE:
            # {goals}: the goals that are today or in the past.
            return (
                QCoreApplication.translate(
                    "Plan", "A goal is today or in the past ({goals})."
                ).format(goals=goals),
                QCoreApplication.translate(
                    "Plan", "Change its date to a day after today, or remove it."
                ),
            )
        case RefusalReason.GOALS_ON_SAME_DAY:
            return (
                QCoreApplication.translate("Plan", "Two races are on the same day."),
                QCoreApplication.translate("Plan", "Move one of the races to another day."),
            )
        case RefusalReason.NO_AVAILABILITY:
            return (
                QCoreApplication.translate("Plan", "No training time is set."),
                QCoreApplication.translate(
                    "Plan", "Set the minutes you can train each day in Training settings."
                ),
            )
        case RefusalReason.NO_SESSION_TYPES:
            return (
                QCoreApplication.translate("Plan", "No session type is chosen."),
                QCoreApplication.translate(
                    "Plan", "Choose at least the easy session in Training settings."
                ),
            )


type _Label = Callable[[str], str]


def _missing_long(warning: MissingLongSession, label_of: _Label) -> tuple[str, str]:
    # {goal}: the race.
    return (
        QCoreApplication.translate(
            "Plan", "No long session is chosen for {goal}: your endurance will lag."
        ).format(goal=label_of(warning.goal_id)),
        QCoreApplication.translate("Plan", "Choose the long session in Training settings."),
    )


def _missing_intense(warning: MissingIntenseSessions, label_of: _Label) -> tuple[str, str]:
    # {goal}: the race.
    return (
        QCoreApplication.translate(
            "Plan",
            "Neither threshold nor VO2max sessions are chosen for {goal}: the build phase has "
            "no quality work.",
        ).format(goal=label_of(warning.goal_id)),
        QCoreApplication.translate(
            "Plan", "Choose threshold or VO2max sessions in Training settings."
        ),
    )


def _long_day_unavailable(_warning: LongDayUnavailable, _label_of: _Label) -> tuple[str, str]:
    return (
        QCoreApplication.translate(
            "Plan", "The long session day has no time available: no long session is planned."
        ),
        QCoreApplication.translate(
            "Plan",
            "Give that day some minutes, or choose another long day, in Training settings.",
        ),
    )


def _close_a_races(warning: CloseARaces, _label_of: _Label) -> tuple[str, str]:
    return (
        QCoreApplication.translate(
            "Plan",
            "Two A races are close together (days between them: %n): between them the plan "
            "only unloads, recovers and maintains.",
            "",
            warning.gap_days,
        ),
        QCoreApplication.translate("Plan", "Give them more time, or make one of them a B race."),
    )


def _ctl_not_reached(warning: CtlTargetNotReached, label_of: _Label) -> tuple[str, str]:
    # {goal}: the goal; {projected} and {target}: the CTL the plan reaches, and the one asked.
    return (
        QCoreApplication.translate(
            "Plan",
            "The fitness asked for {goal} is out of reach: the plan reaches {projected} of "
            "{target} CTL.",
        ).format(
            goal=label_of(warning.goal_id),
            projected=QLocale().toString(warning.projected_ctl, "f", 0),
            target=QLocale().toString(warning.target_ctl, "f", 0),
        ),
        QCoreApplication.translate(
            "Plan",
            "Move the goal later or ask for less fitness: the plan never ramps faster than the "
            "safe limit.",
        ),
    )


def _target_unreachable(warning: TargetTimeUnreachable, label_of: _Label) -> tuple[str, str]:
    # {goal}: the race.
    return (
        QCoreApplication.translate(
            "Plan", "The target time of {goal} cannot be reached by the race."
        ).format(goal=label_of(warning.goal_id)),
        QCoreApplication.translate("Plan", "Choose a slower target time, or a later date."),
    )


def _recovery_inserted(warning: RecoveryInserted, _label_of: _Label) -> tuple[str, str]:
    return (
        QCoreApplication.translate(
            "Plan",
            "Your recent load is high. Recovery days at the start of the plan: %n.",
            "",
            warning.days,
        ),
        QCoreApplication.translate(
            "Plan", "Nothing to do: the plan goes back to training when your form recovers."
        ),
    )


def _beyond_horizon(warning: GoalBeyondHorizon, label_of: _Label) -> tuple[str, str]:
    # {goal}: the goal.
    return (
        QCoreApplication.translate(
            "Plan", "{goal} is more than a year away: only the next 52 weeks are planned."
        ).format(goal=label_of(warning.goal_id)),
        QCoreApplication.translate("Plan", "The rest is planned when it comes closer."),
    )


def _from_injuries(
    warning: PlanHalted | PlanAdapted | NoSessionPossible, _label_of: _Label
) -> tuple[str, str]:
    return plan_warning_texts(warning)


_WARNING_BUILDERS: dict[type, Callable[..., tuple[str, str]]] = {
    PlanHalted: _from_injuries,
    PlanAdapted: _from_injuries,
    NoSessionPossible: _from_injuries,
    MissingLongSession: _missing_long,
    MissingIntenseSessions: _missing_intense,
    LongDayUnavailable: _long_day_unavailable,
    CloseARaces: _close_a_races,
    CtlTargetNotReached: _ctl_not_reached,
    TargetTimeUnreachable: _target_unreachable,
    RecoveryInserted: _recovery_inserted,
    GoalBeyondHorizon: _beyond_horizon,
}


def _warning_texts(subject: object, label_of: _Label) -> tuple[str, str]:
    """The sentence and the remedy of a warning of the plan (a generic one for a warning this
    window does not know yet)."""
    build = _WARNING_BUILDERS.get(type(subject))
    if build is None:
        return (QCoreApplication.translate("Plan", "The plan has a warning."), "")
    return build(subject, label_of)


def notice_prefix(no_plan: bool) -> str:
    """The word that opens a notice, so it never relies on colour: "No plan" or "Warning"."""
    return (
        QCoreApplication.translate("Plan", "No plan")
        if no_plan
        else QCoreApplication.translate("Plan", "Warning")
    )


def label_lookup(goals: Mapping[str, Goal]) -> Callable[[str], str]:
    """A function from goal id to phrase; an unknown id reads "a goal"."""

    def label_of(goal_id: str) -> str:
        goal = goals.get(goal_id)
        return (
            goal_label(goal) if goal is not None else QCoreApplication.translate("Plan", "a goal")
        )

    return label_of


# --- Retest ----------------------------------------------------------------------------------


def retest_proposal_text(proposal: RetestProposal) -> str:
    """Where the plan proposes the next threshold test, as a sentence for the status bar."""
    match proposal:
        case RetestBeforePlan():
            return QCoreApplication.translate(
                "Plan", "Plan: do the threshold test before the plan starts"
            )
        case RetestInUnloadWeek(week_start=week):
            # {date}: the Monday of the easy week.
            return QCoreApplication.translate(
                "Plan", "Plan: do the threshold test in the easy week from {date}"
            ).format(date=short_date_text(week))
        case RetestNotPlaceable():
            return QCoreApplication.translate(
                "Plan", "Plan: no easy week fits the next threshold test"
            )
        case RetestScheduled(day=day):
            # {date}: the day of the threshold test in the plan.
            return QCoreApplication.translate("Plan", "Threshold test planned on {date}").format(
                date=short_date_text(day)
            )
