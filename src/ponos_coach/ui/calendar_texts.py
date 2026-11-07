# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Calendar texts: the month's title, weekdays, sports in short, and what each day says.

The domain gives categories (`Sport`, `GoalKind`); here we choose the words. Context "Calendar"
written as a literal: lupdate does not follow aliases.
"""

from __future__ import annotations

from datetime import date

from PySide6.QtCore import QCoreApplication, QDate, QLocale

from ponos_coach.activities.domain import Sport
from ponos_coach.ui.activity_text import sport_text
from ponos_coach.ui.calendar_model import DAYS_PER_WEEK, CalendarDay, GoalKind
from ponos_coach.ui.day_presentation import number_text

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
    return f"{number_text(load)} hrTSS"


def cell_text(cell: CalendarDay) -> str:
    """What a day cell shows: the day, sports, load and goal (nothing outside the month)."""
    if not cell.in_month:
        return ""
    lines = [str(cell.day.day)]
    if cell.activity_count:
        lines.append(", ".join(sport_short_text(sport) for sport in cell.sports))
        lines.append(load_text(cell.load))
    if cell.goal is not None:
        lines.append(goal_kind_text(cell.goal))
    return "\n".join(lines)


def cell_description(cell: CalendarDay) -> str:
    """A day in full words, for the tooltip and the accessible description."""
    if not cell.in_month:
        return ""
    parts = [
        QLocale().toString(
            QDate(cell.day.year, cell.day.month, cell.day.day), QLocale.FormatType.LongFormat
        )
    ]
    if cell.is_today:
        parts.append(QCoreApplication.translate("Calendar", "Today"))
    if cell.is_future:
        parts.append(QCoreApplication.translate("Calendar", "Not available yet"))
    elif cell.activity_count:
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
    else:
        parts.append(QCoreApplication.translate("Calendar", "No activities"))
    if cell.goal is not None:
        # {goal}: the name of the nutrition goal.
        parts.append(
            QCoreApplication.translate("Calendar", "Goal: {goal}").format(
                goal=goal_kind_text(cell.goal)
            )
        )
    return ". ".join(parts)
