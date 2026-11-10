# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""The goals as a table: model and sorting proxy.

One row per goal: the day, the kind (race with its priority, or form goal), the sport, the distance
in kilometres, the elevation gain in metres, the target time and the verdict on it. The texts come
ready from `plan_texts`; the model serves them with numbers aligned on the right and gives the
proxy numeric keys, so a date or a distance sorts as a number and not as text. The verdict of a
target time is words, with a tooltip that explains it, and the best time the plan thinks possible.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from enum import IntEnum

from PySide6.QtCore import (
    QAbstractTableModel,
    QCoreApplication,
    QModelIndex,
    QObject,
    QPersistentModelIndex,
    QSortFilterProxyModel,
    Qt,
)

from ponos_coach.goals.domain import FitnessGoal, RaceGoal
from ponos_coach.goals.ports import Goal
from ponos_coach.planning.domain import TargetAssessment
from ponos_coach.ui.activity_text import sport_text
from ponos_coach.ui.goal_draft import target_time_text
from ponos_coach.ui.plan_texts import (
    ascent_text,
    distance_km_text,
    goal_kind_cell_text,
    short_date_text,
    target_class_explanation,
    target_class_text,
)

SORT_ROLE = Qt.ItemDataRole.UserRole
GOAL_ID_ROLE = Qt.ItemDataRole.UserRole + 1

_ALIGN_RIGHT = int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
_ALIGN_LEFT = int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)


class GoalColumn(IntEnum):
    """Columns, in display order."""

    DAY = 0
    KIND = 1
    SPORT = 2
    DISTANCE = 3
    ASCENT = 4
    TARGET_TIME = 5
    VERDICT = 6


_NUMERIC = frozenset({GoalColumn.DISTANCE, GoalColumn.ASCENT, GoalColumn.TARGET_TIME})


class GoalsTableModel(QAbstractTableModel):
    """The goals, one row each, read-only."""

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._goals: tuple[Goal, ...] = ()
        self._assessments: Mapping[str, TargetAssessment] = {}

    def set_goals(self, goals: Sequence[Goal], assessments: Mapping[str, TargetAssessment]) -> None:
        """Replace the goals and the verdicts on their target times."""
        self.beginResetModel()
        self._goals = tuple(goals)
        self._assessments = dict(assessments)
        self.endResetModel()

    def goal_at(self, row: int) -> Goal | None:
        """The goal of a row of the model, if there is one."""
        return self._goals[row] if 0 <= row < len(self._goals) else None

    def rowCount(  # noqa: N802 - Qt
        self, parent: QModelIndex | QPersistentModelIndex | None = None
    ) -> int:
        """Number of goals (no child rows)."""
        if parent is not None and parent.isValid():
            return 0
        return len(self._goals)

    def columnCount(  # noqa: N802 - Qt
        self, parent: QModelIndex | QPersistentModelIndex | None = None
    ) -> int:
        """Number of columns."""
        if parent is not None and parent.isValid():
            return 0
        return len(GoalColumn)

    def headerData(  # noqa: N802 - Qt
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> object:
        """Header text and alignment."""
        if orientation != Qt.Orientation.Horizontal or not 0 <= section < len(GoalColumn):
            return None
        column = GoalColumn(section)
        if role == Qt.ItemDataRole.DisplayRole:
            return _header_text(column)
        if role == Qt.ItemDataRole.TextAlignmentRole:
            return _ALIGN_RIGHT if column in _NUMERIC else _ALIGN_LEFT
        return None

    def data(
        self,
        index: QModelIndex | QPersistentModelIndex,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> object:
        """Text, sort key, tooltip, alignment and goal id of a cell."""
        goal = self.goal_at(index.row()) if index.isValid() else None
        if goal is None or not 0 <= index.column() < len(GoalColumn):
            return None
        column = GoalColumn(index.column())
        assessment = self._assessments.get(goal.goal_id.value)
        readers: dict[int, Callable[[], object]] = {
            Qt.ItemDataRole.DisplayRole: lambda: _cell_text(goal, column, assessment),
            SORT_ROLE: lambda: _sort_key(goal, column, assessment),
            GOAL_ID_ROLE: lambda: goal.goal_id.value,
            Qt.ItemDataRole.ToolTipRole: lambda: _tooltip(column, assessment),
            Qt.ItemDataRole.TextAlignmentRole: lambda: (
                _ALIGN_RIGHT if column in _NUMERIC else _ALIGN_LEFT
            ),
        }
        read = readers.get(role)
        return None if read is None else read()


def _header_text(column: GoalColumn) -> str:
    texts = {
        GoalColumn.DAY: QCoreApplication.translate("GoalsTable", "Date"),
        GoalColumn.KIND: QCoreApplication.translate("GoalsTable", "Goal"),
        GoalColumn.SPORT: QCoreApplication.translate("GoalsTable", "Sport"),
        GoalColumn.DISTANCE: QCoreApplication.translate("GoalsTable", "Distance (km)"),
        GoalColumn.ASCENT: QCoreApplication.translate("GoalsTable", "Elevation gain (m)"),
        GoalColumn.TARGET_TIME: QCoreApplication.translate("GoalsTable", "Target time (h:mm:ss)"),
        GoalColumn.VERDICT: QCoreApplication.translate("GoalsTable", "Target time verdict"),
    }
    return texts[column]


def _race_distance_text(goal: Goal) -> str:
    return "" if isinstance(goal, FitnessGoal) else distance_km_text(goal.distance_m)


def _race_ascent_text(goal: Goal) -> str:
    """The elevation gain of a race; empty for a flat race and for a form goal."""
    if isinstance(goal, RaceGoal) and goal.ascent_m > 0:
        return ascent_text(goal.ascent_m)
    return ""


def _race_time_text(goal: Goal) -> str:
    if isinstance(goal, RaceGoal) and goal.target_time_s is not None:
        return target_time_text(goal.target_time_s)
    return ""


def _cell_text(goal: Goal, column: GoalColumn, assessment: TargetAssessment | None) -> str:
    texts = {
        GoalColumn.DAY: lambda: short_date_text(goal.day),
        GoalColumn.KIND: lambda: goal_kind_cell_text(goal),
        GoalColumn.SPORT: lambda: "" if isinstance(goal, FitnessGoal) else sport_text(goal.sport),
        GoalColumn.DISTANCE: lambda: _race_distance_text(goal),
        GoalColumn.ASCENT: lambda: _race_ascent_text(goal),
        GoalColumn.TARGET_TIME: lambda: _race_time_text(goal),
        GoalColumn.VERDICT: lambda: (
            "" if assessment is None else target_class_text(assessment.classification)
        ),
    }
    return texts[column]()


def _sort_key(goal: Goal, column: GoalColumn, assessment: TargetAssessment | None) -> float | str:
    """Numbers sort as numbers, texts case-insensitively; a missing value sorts first."""
    match column:
        case GoalColumn.DAY:
            return float(goal.day.toordinal())
        case GoalColumn.DISTANCE:
            return -1.0 if isinstance(goal, FitnessGoal) else float(goal.distance_m)
        case GoalColumn.ASCENT:
            return float(goal.ascent_m) if isinstance(goal, RaceGoal) else -1.0
        case GoalColumn.TARGET_TIME:
            if isinstance(goal, RaceGoal) and goal.target_time_s is not None:
                return float(goal.target_time_s)
            return -1.0
        case _:
            return _cell_text(goal, column, assessment).casefold()


def _tooltip(column: GoalColumn, assessment: TargetAssessment | None) -> str:
    if column is not GoalColumn.VERDICT or assessment is None:
        return ""
    explanation = target_class_explanation(assessment.classification)
    if assessment.best_time_s is None:
        return explanation
    # {time}: the best time possible at the race, as h:mm:ss.
    best = QCoreApplication.translate(
        "GoalsTable", "Best time possible by the race: {time}"
    ).format(time=target_time_text(assessment.best_time_s))
    return f"{explanation}\n{best}"


class GoalsProxy(QSortFilterProxyModel):
    """Sorts by the model's keys; the soonest goal first by default."""

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.setSortRole(SORT_ROLE)
        self.setSortCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)

    def goal_id_at(self, row: int) -> str | None:
        """The goal id of a row of the proxy, if there is one."""
        value = self.index(row, 0).data(GOAL_ID_ROLE)
        return value if isinstance(value, str) else None


def sorted_goals_model(model: GoalsTableModel, parent: QObject | None = None) -> GoalsProxy:
    """A proxy over the model, sorted by date (the soonest first)."""
    proxy = GoalsProxy(parent)
    proxy.setSourceModel(model)
    proxy.sort(GoalColumn.DAY, Qt.SortOrder.AscendingOrder)
    return proxy
