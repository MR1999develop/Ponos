# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""The plan in the window: goals, training settings, the plan's state, the calendar layer and the
PMC projection.

The goals are added, changed and removed on the days of the calendar: the commands work on the
selected day (a new goal starts on it, a change or removal acts on the goal that falls on it; with
two goals on a day a small menu asks which). The page "Goals and plan status" only shows.

Owns the dialogs, the undo stack (goals, availability and session types share it: one Undo, one
Redo) and the jobs of `PlanLoader`; the window only gives it callbacks and puts its actions in the
menu. A change goes through a dialog and then through a command on the stack; the job runs on the
data thread, the three choices are read again and, if nothing else is waiting, shown. After a
change the overview is recalculated, and with it the plan: the plan then arrives through
`WorkspaceController.plan_rebuilt` and is shown everywhere at once (notices, goals, calendar,
chart, retest). The profile is read only on the data thread.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import replace
from datetime import date, timedelta
from functools import partial

from PySide6.QtCore import QCoreApplication, QObject, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QKeySequence, QUndoStack
from PySide6.QtWidgets import QWidget

from ponos_coach.goals.domain import (
    GoalId,
    Priority,
    RaceGoal,
    SessionChoices,
    WeeklyAvailability,
)
from ponos_coach.goals.ports import Goal
from ponos_coach.goals.use_cases import (
    AvailabilityChanged,
    GoalChanged,
    GoalRemoved,
    SessionChoicesChanged,
)
from ponos_coach.load.use_cases import ProfileOverview
from ponos_coach.profiles.rebuild_plan import PlanBuilt, PlanNotBuilt, PlanRefused
from ponos_coach.ui.calendar_model import plan_layer_of
from ponos_coach.ui.calendar_page import CalendarPage
from ponos_coach.ui.choice_menu import popup_choices
from ponos_coach.ui.errors import ErrorMessage
from ponos_coach.ui.goal_dialog import GoalDialog
from ponos_coach.ui.goal_draft import (
    GoalDraft,
    GoalFormKind,
    default_draft,
    goal_of,
)
from ponos_coach.ui.goal_draft import (
    draft_of as draft_of_goal,
)
from ponos_coach.ui.goals_page import GoalsPage
from ponos_coach.ui.overview_page import OverviewPage
from ponos_coach.ui.overview_state import OverviewState, Ready
from ponos_coach.ui.plan_loader import PastLoaded, PlanLoader, PlanSnapshot
from ponos_coach.ui.plan_presentation import (
    NoPlanYet,
    NoticeAction,
    PlanState,
    assessments_of,
    notices_of,
    plan_of,
    plan_state_of,
    projection_of,
    retest_proposal_of,
)
from ponos_coach.ui.plan_texts import (
    goal_label,
    label_lookup,
    race_short_name,
    retest_proposal_text,
)
from ponos_coach.ui.plan_undo import (
    AddGoalCommand,
    ChangeGoalCommand,
    OutcomeCell,
    RemoveGoalCommand,
    SetAvailabilityCommand,
    SetChoicesCommand,
)
from ponos_coach.ui.settings_page import SettingsPage
from ponos_coach.ui.tokens import Theme
from ponos_coach.ui.training_dialog import TrainingSettingsDialog
from ponos_coach.ui.training_draft import draft_of, is_suggestion
from ponos_coach.ui.workspace import WorkspaceController

_DEFAULT_LEAD_DAYS = 84  # a new goal starts twelve weeks ahead


def new_goal_id() -> str:
    """A fresh goal id (the interface chooses it, so a repeated command is refused, not doubled)."""
    return uuid.uuid4().hex


class CountedJobs:
    """The jobs the commands start (`PlanOps`), counted: what the window shows is the store's state
    once the last job on its way has returned."""

    def __init__(self, loader: PlanLoader) -> None:
        self._loader = loader
        self.pending = 0

    def settled(self) -> None:
        """A job has returned."""
        self.pending = max(self.pending - 1, 0)

    def forget(self) -> None:
        """The profile changed: the jobs on their way no longer matter."""
        self.pending = 0

    def _start(self, job: Callable[[], bool]) -> bool:
        self.pending += 1
        if job():
            return True
        self.pending -= 1
        return False

    def add_goal(self, goal: Goal) -> bool:
        """Add a goal."""
        return self._start(partial(self._loader.add_goal, goal))

    def change_goal(self, goal: Goal, cell: OutcomeCell[GoalChanged]) -> bool:
        """Replace the goal with the same id."""
        return self._start(partial(self._loader.change_goal, goal, cell))

    def restore_goal(self, cell: OutcomeCell[GoalChanged]) -> bool:
        """Put back the replaced version."""
        return self._start(partial(self._loader.restore_goal, cell))

    def remove_goal(self, goal_id: GoalId, cell: OutcomeCell[GoalRemoved]) -> bool:
        """Remove a goal."""
        return self._start(partial(self._loader.remove_goal, goal_id, cell))

    def restore_removed_goal(self, cell: OutcomeCell[GoalRemoved]) -> bool:
        """Add back the removed goal."""
        return self._start(partial(self._loader.restore_removed_goal, cell))

    def set_availability(
        self, availability: WeeklyAvailability | None, cell: OutcomeCell[AvailabilityChanged]
    ) -> bool:
        """Set the weekly availability."""
        return self._start(partial(self._loader.set_availability, availability, cell))

    def restore_availability(self, cell: OutcomeCell[AvailabilityChanged]) -> bool:
        """Set the previous availability."""
        return self._start(partial(self._loader.restore_availability, cell))

    def set_choices(
        self, choices: SessionChoices, cell: OutcomeCell[SessionChoicesChanged]
    ) -> bool:
        """Set the session types."""
        return self._start(partial(self._loader.set_choices, choices, cell))

    def restore_choices(self, cell: OutcomeCell[SessionChoicesChanged]) -> bool:
        """Set the previous session types."""
        return self._start(partial(self._loader.restore_choices, cell))


class PlanUi(QObject):
    """Owns the plan flow of the window."""

    retest_proposal_changed = Signal(str)  # the status bar sentence; empty without a plan

    def __init__(  # noqa: PLR0913 - each argument is a piece of the window it works with
        self,
        window: QWidget,
        controller: WorkspaceController,
        calendar: CalendarPage,
        goals_page: GoalsPage,
        overview: OverviewPage,
        *,
        today: Callable[[], date],
        theme: Callable[[], Theme],
        show_error: Callable[[ErrorMessage], None],
        show_goals_page: Callable[[], None],
        edit_profile: Callable[[], None],
        training_page: SettingsPage,
        show_training_page: Callable[[], None],
        no_profile_text: Callable[[], str],
        show_injuries_page: Callable[[], None] = lambda: None,
        make_goal_id: Callable[[], str] = new_goal_id,
    ) -> None:
        super().__init__(window)
        self._window = window
        self._controller = controller
        self._calendar = calendar
        self._goals_page = goals_page
        self._overview_page = overview
        self._today = today
        self._theme = theme
        self._show_error = show_error
        self._show_goals_page = show_goals_page
        self._edit_profile = edit_profile
        self._show_injuries_page = show_injuries_page
        self._make_goal_id = make_goal_id
        self._loader = PlanLoader(controller, self)
        self._stack = QUndoStack(self)
        self._snapshot: PlanSnapshot | None = None
        self._state: PlanState = NoPlanYet()
        self._past: PastLoaded | None = None
        self._overview: ProfileOverview | None = None
        self._jobs = CountedJobs(self._loader)
        self._goal_dialog: GoalDialog | None = None
        self._training_page = training_page
        self._show_training_page = show_training_page
        self._no_profile_text = no_profile_text
        training_page.shown.connect(self.refresh_training_page)
        self._build_actions()
        self._connect()
        self._update_actions()

    # --- Construction -------------------------------------------------------------------

    def _action(self, text: str, shortcut: str | None, slot: Callable[[], object]) -> QAction:
        action = QAction(text, self)
        if shortcut is not None:
            action.setShortcut(QKeySequence(shortcut))
        action.triggered.connect(lambda _checked=False, run=slot: run())  # never the flag
        return action

    def _build_actions(self) -> None:
        self._add_race_action = self._action(self.tr("Add race..."), "Ctrl+R", self._add_race_here)
        self._add_form_goal_action = self._action(
            self.tr("Add form goal..."), "Ctrl+Shift+F", self._add_form_goal_here
        )
        self._change_goal_action = self._action(
            self.tr("Change goal..."), "Ctrl+Shift+E", self.change_selected_goal
        )
        self._remove_goal_action = self._action(
            self.tr("Remove goal"), "Ctrl+Delete", self.remove_selected_goal
        )
        self._training_action = self._action(
            self.tr("Training settings..."), "Ctrl+Shift+T", self.show_training_settings
        )
        self._undo_action = self._stack.createUndoAction(self, self.tr("Undo"))
        # "Undo <change>" / "Redo <change>" at the end of the Plan menu, like the threshold test:
        # no global shortcut (a global Ctrl+Z would also act while a text field has the focus).
        self._redo_action = self._stack.createRedoAction(self, self.tr("Redo"))
        self._stack.canUndoChanged.connect(self._update_actions)
        self._stack.canRedoChanged.connect(self._update_actions)

    def _connect(self) -> None:
        self._loader.snapshot_loaded.connect(self._on_snapshot_loaded)
        self._loader.changed.connect(self._on_changed)
        self._loader.command_failed.connect(self._on_command_failed)
        self._loader.past_loaded.connect(self._on_past_loaded)
        self._loader.failed.connect(self._on_failed)
        self._controller.plan_rebuilt.connect(self._on_plan_rebuilt)
        self._controller.overview_changed.connect(self._on_overview_changed)
        self._calendar.month_changed.connect(self._reload_past)
        self._calendar.selection_changed.connect(self._update_actions)
        self._goals_page.goal_activated.connect(self.change_goal)
        self._goals_page.notice_action_requested.connect(self._on_notice_action)

    # --- Test accessors --------------------------------------------------------------------

    @property
    def loader(self) -> PlanLoader:
        """The plan jobs."""
        return self._loader

    @property
    def undo_stack(self) -> QUndoStack:
        """The stack of the goal and training changes."""
        return self._stack

    @property
    def snapshot(self) -> PlanSnapshot | None:
        """The goals, availability and choices as stored (None until read)."""
        return self._snapshot

    @property
    def state(self) -> PlanState:
        """The state of the plan as last told by the rebuild."""
        return self._state

    @property
    def is_idle(self) -> bool:
        """True when no change is on its way to the store."""
        return self._jobs.pending == 0

    @property
    def goal_dialog(self) -> GoalDialog | None:
        """The open goal dialog, if any."""
        return self._goal_dialog

    @property
    def training_dialog(self) -> TrainingSettingsDialog | None:
        """The content of the Training plan page, once the settings have been read."""
        form = self._training_page.form
        return form if isinstance(form, TrainingSettingsDialog) else None

    @property
    def actions(self) -> dict[str, QAction]:
        """The plan's actions by name."""
        return {
            "add_race": self._add_race_action,
            "add_form_goal": self._add_form_goal_action,
            "change_goal": self._change_goal_action,
            "remove_goal": self._remove_goal_action,
            "training": self._training_action,
            "undo": self._undo_action,
            "redo": self._redo_action,
        }

    # --- Opening the dialogs ---------------------------------------------------------------

    def add_race(self, day: date | None = None) -> None:
        """Open the dialog that adds a race (on `day`, or twelve weeks from today)."""
        self._open_goal_dialog(GoalFormKind.RACE, day, None)

    def add_form_goal(self, day: date | None = None) -> None:
        """Open the dialog that adds a form goal."""
        self._open_goal_dialog(GoalFormKind.FORM, day, None)

    def change_goal(self, goal_id: str) -> None:
        """Open the dialog that changes a goal."""
        goal = self._goal_with_id(goal_id)
        if goal is not None:
            self._open_goal_dialog(None, None, goal)

    def _add_race_here(self) -> None:
        self.add_race(self._calendar.selected_day())

    def _add_form_goal_here(self) -> None:
        self.add_form_goal(self._calendar.selected_day())

    def change_selected_goal(self) -> None:
        """Change the goal on the day selected in the calendar (which one, if there are two)."""
        self._on_selected_goal(self.change_goal)

    def remove_goal(self, goal_id: str) -> None:
        """Remove a goal; the removal can be undone, so nothing is asked first."""
        goal = self._goal_with_id(goal_id)
        if goal is not None:
            self._stack.push(RemoveGoalCommand(self._jobs, goal.goal_id))

    def remove_selected_goal(self) -> None:
        """Remove the goal on the day selected in the calendar (which one, if there are two)."""
        self._on_selected_goal(self.remove_goal)

    def _selected_goals(self) -> list[Goal]:
        """The goals that fall on the day selected in the calendar."""
        cell = self._calendar.selected_cell()
        if cell is None:
            return []
        found = (self._goal_with_id(item.goal.goal_id.value) for item in cell.goals)
        return [goal for goal in found if goal is not None]

    def _on_selected_goal(self, command: Callable[[str], None]) -> None:
        goals = self._selected_goals()
        if len(goals) == 1:
            command(goals[0].goal_id.value)
        elif goals:
            popup_choices(
                self._window,
                [(goal_label(g), partial(command, g.goal_id.value)) for g in goals],
            )

    def _goal_with_id(self, goal_id: str) -> Goal | None:
        if self._snapshot is None:
            return None
        return next((g for g in self._snapshot.goals if g.goal_id.value == goal_id), None)

    def _open_goal_dialog(
        self, kind: GoalFormKind | None, day: date | None, editing: Goal | None
    ) -> None:
        if self._goal_dialog is not None:
            self._goal_dialog.raise_()
            self._goal_dialog.activateWindow()
            return
        snapshot = self._snapshot
        if snapshot is None or self._controller.profile is None:
            return
        today = self._today()
        if editing is not None:
            draft = draft_of_goal(editing)
        elif kind is not None:
            draft = self._new_draft(kind, day or today + timedelta(days=_DEFAULT_LEAD_DAYS))
        else:
            return
        self._show_goal_dialog(draft, snapshot, editing)

    def _show_goal_dialog(
        self, draft: GoalDraft, snapshot: PlanSnapshot, editing: Goal | None
    ) -> None:
        dialog = GoalDialog(
            draft,
            snapshot.goals,
            self._today(),
            None if editing is None else editing.goal_id,
            self._window,
        )
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.accepted.connect(partial(self._on_goal_accepted, dialog, editing))
        dialog.finished.connect(self._on_goal_dialog_closed)
        self._goal_dialog = dialog
        dialog.open()

    def _new_draft(self, kind: GoalFormKind, day: date) -> GoalDraft:
        """A new goal; the default priority is A, or B if there is already an A race."""
        draft = default_draft(kind, day)
        goals = () if self._snapshot is None else self._snapshot.goals
        has_a_race = any(isinstance(g, RaceGoal) and g.priority is Priority.A for g in goals)
        return replace(draft, priority=Priority.B) if has_a_race else draft

    def _on_goal_accepted(self, dialog: GoalDialog, editing: Goal | None) -> None:
        snapshot = self._snapshot
        if snapshot is None:
            return
        goal_id = editing.goal_id if editing is not None else GoalId(self._make_goal_id())
        draft = dialog.draft()
        goal = goal_of(draft, snapshot.goals, self._today(), goal_id)
        if goal is None:
            # What the goals are now no longer agrees with the form (another race took the day
            # meanwhile): the dialog stays, with the problem written under its field.
            QTimer.singleShot(0, partial(self._reopen_goal_dialog, draft, editing))
            return
        if editing is None:
            self._stack.push(AddGoalCommand(self._jobs, goal))
        elif goal != editing:
            self._stack.push(ChangeGoalCommand(self._jobs, goal))

    def _reopen_goal_dialog(self, draft: GoalDraft, editing: Goal | None) -> None:
        snapshot = self._snapshot
        if self._goal_dialog is None and snapshot is not None:
            self._show_goal_dialog(draft, snapshot, editing)

    def _on_goal_dialog_closed(self) -> None:
        self._goal_dialog = None

    def show_training_settings(self) -> None:
        """Open Settings on the Training plan page."""
        self._show_training_page()

    def refresh_training_page(self) -> None:
        """Fill the Training plan page from the choices read (or say why it cannot be filled). A
        form the user is editing is left alone; a clean one shows what is saved now."""
        page = self._training_page
        snapshot = self._snapshot
        if self._controller.profile is None:
            page.show_notice(self._no_profile_text())
            return
        if snapshot is None:
            page.show_notice(self.tr("Wait a moment: the goals are being read."))
            return
        draft = draft_of(snapshot.availability, snapshot.choices)
        suggested = is_suggestion(snapshot.availability, snapshot.choices)
        form = self.training_dialog
        if form is None:
            form = TrainingSettingsDialog(draft, suggested=suggested)
            form.save_requested.connect(partial(self._on_training_save_requested, form))
            page.show_form(form)
        elif not form.has_unsaved_changes():
            form.show_saved(draft, suggested=suggested)

    def _on_training_save_requested(self, form: TrainingSettingsDialog) -> None:
        before = self._snapshot
        if before is None:
            form.save_failed.emit()
            return
        after = form.draft()
        availability = after.availability()
        steps: list[SetAvailabilityCommand | SetChoicesCommand] = []
        if availability != before.availability:
            steps.append(SetAvailabilityCommand(self._jobs, availability))
        if after.choices != before.choices:
            steps.append(SetChoicesCommand(self._jobs, after.choices))
        if len(steps) == 1:
            self._stack.push(steps[0])
        elif steps:
            self._stack.beginMacro(
                QCoreApplication.translate("PlanUndo", "Change training settings")
            )
            for step in steps:
                self._stack.push(step)
            self._stack.endMacro()
        form.show_saved(after, suggested=False)

    def _on_notice_action(self, action: object) -> None:
        if action is NoticeAction.ADD_RACE:
            self.add_race()
        elif action is NoticeAction.OPEN_GOALS:
            self._show_goals_page()
        elif action is NoticeAction.TRAINING_SETTINGS:
            self.show_training_settings()
        elif action is NoticeAction.EDIT_PROFILE:
            self._edit_profile()
        elif action is NoticeAction.OPEN_INJURIES:
            self._show_injuries_page()

    # --- What comes back from the data thread ---------------------------------------------------

    def _on_snapshot_loaded(self, snapshot: object) -> None:
        if isinstance(snapshot, PlanSnapshot) and self._jobs.pending == 0:
            self._apply_snapshot(snapshot)

    def _on_changed(self, snapshot: object) -> None:
        """A change went through: the store is read again. The overview (and the plan with it) is
        calculated again from the new goals."""
        self._jobs.settled()
        if isinstance(snapshot, PlanSnapshot) and self._jobs.pending == 0:
            self._apply_snapshot(snapshot)
        self._controller.request_overview_refresh()

    def _on_command_failed(self, message: object) -> None:
        """A change was refused: nothing changed, so the stack no longer matches the store."""
        self._jobs.settled()
        self._stack.clear()
        if isinstance(message, ErrorMessage):
            self._show_error(message)
        self._loader.load_snapshot()  # the screen shows what is really stored

    def _on_failed(self, message: object) -> None:
        if isinstance(message, ErrorMessage):
            self._show_error(message)

    def _apply_snapshot(self, snapshot: PlanSnapshot) -> None:
        self._snapshot = snapshot
        self._refresh_view()
        self.refresh_training_page()

    def _on_plan_rebuilt(self, result: object) -> None:
        if isinstance(result, PlanBuilt | PlanRefused | PlanNotBuilt):
            self._state = plan_state_of(result)
            self._refresh_view()
            self._reload_past()

    def _on_overview_changed(self, state: OverviewState) -> None:
        if isinstance(state, Ready):
            self._overview = state.overview

    def _on_past_loaded(self, past: object) -> None:
        if isinstance(past, PastLoaded):
            self._past = past
            self._refresh_layer()

    def _reload_past(self, *_ignored: object) -> None:
        """Read how the days of the month shown were planned, and how they went."""
        overview = self._overview
        if overview is None or self._controller.profile is None:
            return
        first, last = self._calendar.visible_range
        last = min(last, self._today())
        if first <= last:
            self._loader.load_past(first, last, overview)

    # --- Showing --------------------------------------------------------------------------------

    def _goals(self) -> tuple[Goal, ...]:
        return () if self._snapshot is None else self._snapshot.goals

    def _refresh_view(self) -> None:
        state = self._state
        goals = self._goals()
        by_id = {goal.goal_id.value: goal for goal in goals}
        self._goals_page.show_state(
            notices_of(state), goals, assessments_of(state), label_lookup(by_id), self._theme()
        )
        self._refresh_layer()
        self._overview_page.set_projection(projection_of(state, goals, race_short_name))
        proposal = retest_proposal_of(state)
        self.retest_proposal_changed.emit(
            "" if proposal is None else retest_proposal_text(proposal)
        )
        self._update_actions()

    def _refresh_layer(self) -> None:
        past = self._past
        self._calendar.set_plan_layer(
            plan_layer_of(
                plan_of(self._state),
                () if past is None else past.days,
                () if past is None else past.adherence,
                self._goals(),
            )
        )

    def _update_actions(self) -> None:
        profile_open = self._controller.profile is not None
        ready = profile_open and self._snapshot is not None
        if ready:
            reason = ""
        elif not profile_open:
            reason = self.tr("Create or open a profile first.")
        else:
            reason = self.tr("Wait a moment: the goals are being read.")
        self._training_action.setEnabled(ready)
        self._training_action.setToolTip(reason)
        cell = self._calendar.selected_cell()
        # a goal needs a day the plan can still reach; no day selected means "pick it in the form"
        day_fits = cell is None or cell.day > self._today()
        add_reason = reason or ("" if day_fits else self.tr("A goal needs a day after today."))
        for action in (self._add_race_action, self._add_form_goal_action):
            action.setEnabled(ready and day_fits)
            action.setToolTip(add_reason)
        has_goal = ready and cell is not None and bool(cell.goals)
        why = reason or self.tr("Select a day with a goal in the Calendar first.")
        for action in (self._change_goal_action, self._remove_goal_action):
            action.setEnabled(has_goal)
            action.setToolTip("" if has_goal else why)
        self._undo_action.setToolTip(
            ""
            if self._stack.canUndo()
            else self.tr("There is no change to the goals to take back.")
        )
        self._redo_action.setToolTip(
            ""
            if self._stack.canRedo()
            else self.tr("There is no change to the goals to apply again.")
        )

    # --- The window's events ----------------------------------------------------------------------

    def profile_changed(self) -> None:
        """Another profile opened: the stack, the dialogs and what was shown belong to the old
        one. The choices of the new one are read."""
        self._stack.clear()
        self.close_dialogs()
        self._loader.cancel_past()
        self._jobs.forget()
        self._snapshot = None
        self.refresh_training_page()
        self._state = NoPlanYet()
        self._past = None
        self._overview = None
        self._refresh_view()
        self._loader.load_snapshot()

    def theme_changed(self) -> None:
        """The colour scheme changed: the notices take the new colours."""
        self._refresh_view()

    def close_dialogs(self) -> None:
        """Close the open dialogs (another profile opens, or the window is closing)."""
        if self._goal_dialog is not None:
            self._goal_dialog.close()
