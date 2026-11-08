# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Page "Goals": the state of the plan, and the table of the goals with their commands.

Humble view: what is wrong with the plan and what to do about it come ready from `plan_presentation`
and `plan_texts` (notices with a remedy in plain words); the table is a model with a sorting proxy.
A notice always opens with a word ("No plan", "Warning"), and its colour is only a border: the
meaning never rests on the colour alone. The commands are the window's own actions, shown as
buttons, so a button and a menu entry are the same command with the same tooltip.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from PySide6.QtCore import QCoreApplication, QModelIndex, Qt, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableView,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ponos_coach.goals.ports import Goal
from ponos_coach.planning.domain import TargetAssessment
from ponos_coach.ui.goals_table import GoalsProxy, GoalsTableModel, sorted_goals_model
from ponos_coach.ui.plan_presentation import Notice, NoticeAction, Severity
from ponos_coach.ui.plan_texts import notice_prefix, notice_texts
from ponos_coach.ui.tokens import (
    SPACE_1,
    SPACE_2,
    SPACE_3,
    SPACE_4,
    Theme,
    tokens_for,
    warning_color_for,
)

_BORDER_WIDTH = 4


@dataclass(frozen=True, slots=True)
class GoalsPageActions:
    """The window's actions that the page shows as buttons."""

    add_race: QAction
    add_form_goal: QAction
    change_goal: QAction
    remove_goal: QAction
    training_settings: QAction
    undo: QAction
    redo: QAction


def _action_label(action: NoticeAction) -> str:
    texts = {
        NoticeAction.ADD_RACE: QCoreApplication.translate("GoalsPage", "Add race..."),
        NoticeAction.OPEN_GOALS: QCoreApplication.translate("GoalsPage", "Show the goals"),
        NoticeAction.TRAINING_SETTINGS: QCoreApplication.translate(
            "GoalsPage", "Training settings..."
        ),
        NoticeAction.EDIT_PROFILE: QCoreApplication.translate("GoalsPage", "Edit profile..."),
    }
    return texts.get(action, "")


class GoalsPage(QWidget):
    """Plan status, goals table and commands."""

    notice_action_requested = Signal(object)  # NoticeAction
    selection_changed = Signal(object)  # str | None: the goal id selected
    goal_activated = Signal(str)  # Enter or double click on a goal

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._model = GoalsTableModel(self)
        self._proxy: GoalsProxy = sorted_goals_model(self._model, self)
        self._notice_widgets: list[QWidget] = []
        self._build()

    # --- Construction -------------------------------------------------------------------

    def _build(self) -> None:
        self._notices = QVBoxLayout()
        self._notices.setSpacing(SPACE_2)
        self._table = QTableView(self)
        self._table.setObjectName("goalsTable")
        self._table.setAccessibleName(self.tr("Goals"))
        self._table.setModel(self._proxy)
        self._table.setSortingEnabled(True)
        self._table.sortByColumn(0, Qt.SortOrder.AscendingOrder)
        self._table.setShowGrid(False)  # no vertical or horizontal lines
        self._table.setAlternatingRowColors(True)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.verticalHeader().setVisible(False)
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._table.selectionModel().selectionChanged.connect(self._on_selection_changed)
        self._table.activated.connect(self._on_activated)
        self._empty = QLabel(self)
        self._empty.setObjectName("goalsEmpty")
        self._empty.setTextFormat(Qt.TextFormat.PlainText)
        self._empty.setWordWrap(True)
        self._empty.setText(self.tr("No goals yet. To get a training plan, add your first race."))
        self._buttons = QHBoxLayout()
        self._buttons.setSpacing(SPACE_2)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACE_4, SPACE_4, SPACE_4, SPACE_4)
        layout.setSpacing(SPACE_3)
        layout.addLayout(self._notices)
        layout.addWidget(self._empty)
        layout.addWidget(self._table, stretch=1)
        layout.addLayout(self._buttons)

    def attach_actions(self, actions: GoalsPageActions) -> None:
        """Show the window's actions as buttons under the table."""
        for action, name in (
            (actions.add_race, "goalsAddRace"),
            (actions.add_form_goal, "goalsAddFormGoal"),
            (actions.change_goal, "goalsChange"),
            (actions.remove_goal, "goalsRemove"),
            (actions.training_settings, "goalsTraining"),
            (actions.undo, "goalsUndo"),
            (actions.redo, "goalsRedo"),
        ):
            button = QToolButton(self)
            button.setObjectName(name)
            button.setDefaultAction(action)
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
            self._buttons.addWidget(button)
        self._buttons.addStretch(1)

    # --- State for the tests ---------------------------------------------------------------

    @property
    def table(self) -> QTableView:
        """The goals table view."""
        return self._table

    @property
    def model(self) -> GoalsTableModel:
        """The goals model."""
        return self._model

    @property
    def proxy(self) -> GoalsProxy:
        """The sorting proxy."""
        return self._proxy

    @property
    def notice_texts(self) -> tuple[str, ...]:
        """The text of each notice shown (prefix, sentence and remedy), top to bottom."""
        return tuple(
            "\n".join(
                label.text()
                for label in widget.findChildren(QLabel)
                if label.objectName() in {"noticePrefix", "noticeHeadline", "noticeRemedy"}
            )
            for widget in self._notice_widgets
        )

    def notice_buttons(self) -> list[QPushButton]:
        """The command button of each notice that has one."""
        return [
            button for widget in self._notice_widgets for button in widget.findChildren(QPushButton)
        ]

    @property
    def empty_text(self) -> str:
        """The empty-state sentence (empty while there are goals)."""
        return "" if self._empty.isHidden() else self._empty.text()

    def selected_goal_id(self) -> str | None:
        """The id of the selected goal, if any."""
        rows = self._table.selectionModel().selectedRows()
        return self._proxy.goal_id_at(rows[0].row()) if rows else None

    def select_goal(self, goal_id: str) -> None:
        """Select the row of a goal."""
        for row in range(self._proxy.rowCount()):
            if self._proxy.goal_id_at(row) == goal_id:
                self._table.selectRow(row)
                return

    # --- Showing the state -----------------------------------------------------------------

    def show_state(
        self,
        notices: Sequence[Notice],
        goals: Sequence[Goal],
        assessments: Mapping[str, TargetAssessment],
        label_of: Callable[[str], str],
        theme: Theme,
    ) -> None:
        """Show the notices of the plan and the goals with the verdicts on their target times."""
        selected = self.selected_goal_id()
        self._model.set_goals(goals, assessments)
        self._empty.setVisible(not goals)
        self._table.setVisible(bool(goals))
        for widget in self._notice_widgets:
            self._notices.removeWidget(widget)
            widget.hide()
            widget.deleteLater()
        self._notice_widgets = []
        for notice in notices:
            widget = self._notice_widget(notice, label_of, theme)
            self._notices.addWidget(widget)
            self._notice_widgets.append(widget)
        if selected is not None:
            self.select_goal(selected)
        self.selection_changed.emit(self.selected_goal_id())

    def _notice_widget(
        self, notice: Notice, label_of: Callable[[str], str], theme: Theme
    ) -> QWidget:
        no_plan = notice.severity is Severity.NO_PLAN
        # No plan yet is a normal state, not an error: neutral border; red stays for errors.
        color = tokens_for(theme).text_muted if no_plan else warning_color_for(theme)
        headline, remedy = notice_texts(notice, label_of)
        frame = QFrame(self)
        frame.setObjectName("planNotice")
        frame.setStyleSheet(
            f"QFrame#planNotice {{ border-left: {_BORDER_WIDTH}px solid {color}; }}"
        )
        prefix = QLabel(notice_prefix(no_plan), frame)
        prefix.setObjectName("noticePrefix")
        prefix.setTextFormat(Qt.TextFormat.PlainText)
        font = prefix.font()
        font.setBold(True)
        prefix.setFont(font)
        head = QLabel(headline, frame)
        head.setObjectName("noticeHeadline")
        head.setTextFormat(Qt.TextFormat.PlainText)
        head.setWordWrap(True)
        text = QVBoxLayout()
        text.setSpacing(SPACE_1)
        text.addWidget(prefix)
        text.addWidget(head)
        if remedy:
            fix = QLabel(remedy, frame)
            fix.setObjectName("noticeRemedy")
            fix.setTextFormat(Qt.TextFormat.PlainText)
            fix.setWordWrap(True)
            text.addWidget(fix)
        row = QHBoxLayout(frame)
        row.setContentsMargins(SPACE_3, SPACE_1, SPACE_1, SPACE_1)
        row.setSpacing(SPACE_3)
        row.addLayout(text, stretch=1)
        label = _action_label(notice.action)
        if label:
            button = QPushButton(label, frame)
            button.clicked.connect(
                lambda _checked=False, a=notice.action: self.notice_action_requested.emit(a)
            )
            row.addWidget(button, alignment=Qt.AlignmentFlag.AlignTop)
        return frame

    def _on_selection_changed(self) -> None:
        self.selection_changed.emit(self.selected_goal_id())

    def _on_activated(self, index: QModelIndex) -> None:
        goal_id = self._proxy.goal_id_at(index.row())
        if goal_id is not None:
            self.goal_activated.emit(goal_id)
