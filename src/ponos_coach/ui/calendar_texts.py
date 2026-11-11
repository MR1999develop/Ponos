# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Calendar texts: the month title, weekdays, sports in short, and what each day says.

The domain gives categories (`Sport`, `GoalKind`); here we choose the words. Context "Calendar"
written as a literal: lupdate does not follow aliases.
"""

from __future__ import annotations

from datetime import date

from PySide6.QtCore import QCoreApplication, QDate, QLocale

from ponos_coach.activities.domain import Sport
from ponos_coach.planning.domain import PlanDay
from ponos_coach.ui.activity_text import sport_text
from ponos_coach.ui.calendar_model import DAYS_PER_WEEK, CalendarDay, DayGoal, GoalKind
from ponos_coach.ui.day_presentation import number_text
from ponos_coach.ui.injury_texts import span_cell_text
from ponos_coach.ui.plan_texts import (
    duration_clock_text,
    goal_kind_cell_text,
    goal_label,
    out_of_reach_mark_text,
    out_of_reach_texts,
    phase_text,
    planned_load_text,
    priority_race_text,
    session_lines,
    session_title,
    target_class_explanation,
    target_class_text,
)

_ISO_MONDAY = 1


def month_title(first_day: date) -> str:
    """The month and year, for example "June 2026" (in the locale's words)."""
    return QLocale().toString(QDate(first_day.year, first_day.month, first_day.day), "MMMM yyyy")


def weekday_names() -> tuple[str, ...]:
    """Short weekday names from Monday to Sunday."""
    locale = QLocale()
    return tuple(
        locale.dayName(offset + _ISO_MONDAY, QLocale.FormatType.ShortFormat)
        for offset in range(DAYS_PER_WEEK)
    )


def sport_short_text(sport: Sport) -> str:
    """A sport in one short word, to fit in a day cell."""
    texts = {
        Sport.RUNNING: QCoreApplication.translate("Calendar", "Run"),
        Sport.TRAIL: QCoreApplication.translate("Calendar", "Trail"),
        Sport.CYCLING: QCoreApplication.translate("Calendar", "Bike"),
        Sport.STRENGTH: QCoreApplication.translate("Calendar", "Gym"),
        Sport.HIKING: QCoreApplication.translate("Calendar", "Hike"),
        Sport.WALKING: QCoreApplication.translate("Calendar", "Walk"),
        Sport.OTHER: QCoreApplication.translate("Calendar", "Other"),
    }
    return texts[sport]


def goal_kind_text(kind: GoalKind) -> str:
    """A goal kind's name, for the legend and the cells."""
    texts = {
        GoalKind.WEIGHT_LOSS: QCoreApplication.translate("Calendar", "Weight loss"),
        GoalKind.PERFORMANCE: QCoreApplication.translate("Calendar", "Performance"),
        GoalKind.MAINTENANCE: QCoreApplication.translate("Calendar", "Maintenance"),
    }
    return texts[kind]


def load_text(load: float) -> str:
    """A load for a cell, with its unit."""
    return f"{number_text(load)} TRIMPeq"


def _plan_lines(cell: CalendarDay) -> list[str]:
    """The goal of the day, the planned session and the phase, one short line each. A rest day is
    blank, as if there were no plan (`CalendarDay.grid_plan`)."""
    lines = []
    for item in cell.goals:
        lines.append(goal_kind_cell_text(item.goal))
        if item.out_of_reach is not None:
            lines.append(out_of_reach_mark_text())
    plan = cell.grid_plan
    if plan is None:
        return lines
    if plan.race is not None:
        if not cell.goals:
            lines.append(priority_race_text(plan.race.priority))
    elif plan.session is not None:
        # {session}: the planned session, for example "Threshold, 60 min".
        lines.append(
            QCoreApplication.translate("Calendar", "Plan: {session}").format(
                session=session_title(plan.session)
            )
        )
    lines.append(phase_text(plan.phase))
    return lines


def cell_text(cell: CalendarDay) -> str:
    """What a day cell shows: the day, sports, load, goal, plan and nutrition goal (nothing outside
    the month)."""
    if not cell.in_month:
        return ""
    lines = [str(cell.day.day)]
    if cell.activity_count:
        lines.append(", ".join(sport_short_text(sport) for sport in cell.sports))
        lines.append(load_text(cell.load))
    lines.extend(_plan_lines(cell))
    lines.extend(span_cell_text(span) for span in cell.problems)
    if cell.goal is not None:
        lines.append(goal_kind_text(cell.goal))
    return "\n".join(lines)


def _long_date(cell: CalendarDay) -> str:
    return QLocale().toString(
        QDate(cell.day.year, cell.day.month, cell.day.day), QLocale.FormatType.LongFormat
    )


def cell_description(cell: CalendarDay) -> str:
    """A day in full words, for the tooltip and the accessible description."""
    if not cell.in_month:
        return ""
    parts = [_long_date(cell)]
    if cell.is_today:
        parts.append(QCoreApplication.translate("Calendar", "Today"))
    for item in cell.goals:
        parts.append(goal_label(item.goal))
        parts.extend(_out_of_reach_lines(item))
    parts.extend(_planned_sentences(cell))
    parts.extend(span_cell_text(span) for span in cell.problems)
    if cell.activity_count:
        sports = ", ".join(sport_text(sport) for sport in cell.sports)
        # {sports}: the names of the sports of the day; %n: the number of activities.
        parts.append(
            QCoreApplication.translate(
                "Calendar", "Activities: %n ({sports})", "", cell.activity_count
            ).format(sports=sports)
        )
        # {load}: the total load of the day with its unit.
        parts.append(
            QCoreApplication.translate("Calendar", "Load {load}").format(load=load_text(cell.load))
        )
    elif not cell.is_future:
        parts.append(QCoreApplication.translate("Calendar", "No activities"))
    if cell.goal is not None:
        # {goal}: the name of the nutrition goal.
        parts.append(
            QCoreApplication.translate("Calendar", "Goal: {goal}").format(
                goal=goal_kind_text(cell.goal)
            )
        )
    return ". ".join(parts)


def _planned_sentences(cell: CalendarDay) -> list[str]:
    plan = cell.plan
    if plan is None:
        if cell.is_future:
            return [QCoreApplication.translate("Calendar", "No plan for this day")]
        return []
    parts = [phase_text(plan.phase)]
    if plan.race is not None:
        parts.append(priority_race_text(plan.race.priority))
    elif plan.session is not None:
        # {session}: the planned session, for example "Threshold, 60 min".
        parts.append(
            QCoreApplication.translate("Calendar", "Planned: {session}").format(
                session=session_title(plan.session)
            )
        )
    else:
        parts.append(QCoreApplication.translate("Calendar", "Planned: rest day"))
    return parts


# --- The day in full (the panel under the grid) ------------------------------------------------


def _out_of_reach_lines(item: DayGoal) -> list[str]:
    """Why the fitness asked for a goal is out of reach and what to do (nothing if it is not)."""
    if item.out_of_reach is None:
        return []
    sentence, remedy = out_of_reach_texts(item.out_of_reach, lambda _goal_id: goal_label(item.goal))
    return [sentence, remedy]


def _goal_detail_lines(item: DayGoal, expected_time_s: int | None) -> list[str]:
    lines = [goal_label(item.goal), *_out_of_reach_lines(item)]
    if expected_time_s is not None:
        # {time}: the time the plan expects, as h:mm:ss.
        lines.append(
            QCoreApplication.translate("Calendar", "The plan expects {time}").format(
                time=duration_clock_text(expected_time_s)
            )
        )
    if item.assessment is not None:
        # {verdict}: realistic, ambitious, not reachable or cannot be estimated.
        lines.append(
            QCoreApplication.translate("Calendar", "Target time: {verdict}").format(
                verdict=target_class_text(item.assessment.classification)
            )
        )
        lines.append(target_class_explanation(item.assessment.classification))
    return lines


def planned_section_text(plan: PlanDay) -> str:
    """The phase of the day and, if there is one, its session with the blocks and the load."""
    lines = [phase_text(plan.phase)]
    if plan.session is not None:
        lines.extend(session_lines(plan.session))
        # {load}: the estimated load with its unit.
        lines.append(
            QCoreApplication.translate("Calendar", "Estimated load: {load}").format(
                load=planned_load_text(plan.load)
            )
        )
    elif plan.race is None:
        lines.append(QCoreApplication.translate("Calendar", "Rest day"))
    return "\n".join(lines)


def day_detail_text(cell: CalendarDay) -> str:
    """The day in full, for the panel under the grid: the planned session with its blocks, what was
    done and how it went against the plan, and the goals. Sections are separated by a blank line."""
    if not cell.in_month:
        return ""
    sections = [_long_date(cell)]
    plan = cell.plan
    expected = None if plan is None or plan.race is None else plan.race.expected_time_s
    if plan is not None:
        sections.append(planned_section_text(plan))
    elif cell.is_future:
        sections.append(QCoreApplication.translate("Calendar", "No plan for this day."))
    sections.extend("\n".join(_goal_detail_lines(item, expected)) for item in cell.goals)
    if cell.activity_count:
        # {sports}: the sports done; {load}: their load with its unit.
        sections.append(
            QCoreApplication.translate("Calendar", "Done: {sports}, {load}").format(
                sports=", ".join(sport_text(sport) for sport in cell.sports),
                load=load_text(cell.load),
            )
        )
    if cell.adherence is not None:
        sections.append(_adherence_text(cell))
    if cell.problems:
        sections.append("\n".join(span_cell_text(span) for span in cell.problems))
    return "\n\n".join(sections)


def _adherence_text(cell: CalendarDay) -> str:
    adherence = cell.adherence
    if adherence is None:
        return ""
    # {planned} and {done}: the load planned and the load done, each with its unit.
    line = QCoreApplication.translate("Calendar", "Planned {planned}, done {done}").format(
        planned=planned_load_text(adherence.planned_load),
        done=planned_load_text(adherence.actual_load),
    )
    if adherence.missed_load > 0:
        # {missed}: the load planned and not done, with its unit.
        missed = QCoreApplication.translate("Calendar", "Not done: {missed}").format(
            missed=planned_load_text(adherence.missed_load)
        )
        return f"{line}\n{missed}"
    return line
