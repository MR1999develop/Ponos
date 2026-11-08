# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""The injuries in the window: the section, the commands, the notices, the status bar and the
calendar marks.

Owns the dialogs, the undo stack (every command on injuries and illnesses shares it: one Undo, one
Redo at the end of the menu "Injuries", no global shortcut) and the jobs of `InjuryLoader`; the
window only gives it callbacks and puts its actions in the menu. A command goes through a dialog (or
none) and then through a command on the stack; the job runs on the data thread, the records are read
again and, if nothing else is waiting, shown. After each command the overview is recalculated, and
with it the plan: the plan arrives through `WorkspaceController.plan_rebuilt` and says, if it must,
that it is stopped or adapted (`PlanHalted`, `PlanAdapted`, `NoSessionPossible`).

A warning sign never blocks anything and is never hidden: a notice at the top of the section and of
the Goals section stays while the sign is not reviewed, the status bar says "See a doctor", and a
message that does not block opens the first time the path is stopped. The app invites a visit and
never says what a sign means. The profile is read only on the data thread. Health data: never in
logs or error messages.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from functools import partial

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QAction, QKeySequence, QUndoStack
from PySide6.QtWidgets import QMessageBox, QWidget

from ponos_coach.injury.domain import CheckIn, Injury, InjuryId, Problem
from ponos_coach.injury.use_cases import ProblemChanged, ProblemOpened
from ponos_coach.profiles.rebuild_plan import PlanBuilt
from ponos_coach.ui.calendar_page import CalendarPage
from ponos_coach.ui.errors import ErrorMessage
from ponos_coach.ui.injury_dialogs import CheckInDialog, IllnessDialog
from ponos_coach.ui.injury_draft import (
    checkin_of,
    draft_of_checkin,
    illness_of,
    injury_of,
    new_problem_id,
)
from ponos_coach.ui.injury_loader import InjuryList, InjuryLoader
from ponos_coach.ui.injury_page import InjuriesPage, InjuriesPageActions
from ponos_coach.ui.injury_presentation import (
    HealthWarning,
    ProblemKind,
    ProblemSummary,
    halted_ids,
    health_warnings_of,
    notices_of,
    spans_of,
    status_facts_of,
    summaries_of,
)
from ponos_coach.ui.injury_texts import alert_body, alert_title, problem_title, status_bar_text
from ponos_coach.ui.injury_undo import (
    CheckInCommand,
    CloseCommand,
    MedicalReviewCommand,
    OpenProblemCommand,
)
from ponos_coach.ui.injury_wizard import InjuryWizard
from ponos_coach.ui.plan_undo import OutcomeCell
from ponos_coach.ui.tokens import Theme
from ponos_coach.ui.workspace import WorkspaceController


class CountedInjuryOps:
    """The jobs the commands start (`InjuryOps`), counted: what the window shows is the store's
    state once the last job on its way has returned."""

    def __init__(self, loader: InjuryLoader) -> None:
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

    def open_problem(self, problem: Problem, cell: OutcomeCell[ProblemOpened]) -> bool:
        """Open an injury or report an illness."""
        return self._start(partial(self._loader.open_problem, problem, cell))

    def remove_problem(self, injury_id: InjuryId) -> bool:
        """Delete a record."""
        return self._start(partial(self._loader.remove_problem, injury_id))

    def restore_problem(self, problem: Problem) -> bool:
        """Put a record back exactly as it was."""
        return self._start(partial(self._loader.restore_problem, problem))

    def record_checkin(
        self, injury_id: InjuryId, checkin: CheckIn, cell: OutcomeCell[ProblemChanged]
    ) -> bool:
        """Save the day's answers."""
        return self._start(partial(self._loader.record_checkin, injury_id, checkin, cell))

    def confirm_review(
        self, injury_id: InjuryId, day: date, cell: OutcomeCell[ProblemChanged]
    ) -> bool:
        """Say a doctor has seen the warning signs."""
        return self._start(partial(self._loader.confirm_review, injury_id, day, cell))

    def close_problem(
        self, injury_id: InjuryId, day: date, cell: OutcomeCell[ProblemChanged]
    ) -> bool:
        """Close an injury or say an illness is over."""
        return self._start(partial(self._loader.close_problem, injury_id, day, cell))

    def restore_before(self, cell: OutcomeCell[ProblemChanged]) -> bool:
        """Put back the record as it was before the change."""
        return self._start(partial(self._loader.restore_before, cell))

    def restore_after(self, cell: OutcomeCell[ProblemChanged]) -> bool:
        """Put back the record as the change left it."""
        return self._start(partial(self._loader.restore_after, cell))


class InjuryUi(QObject):
    """Owns the injuries flow of the window."""

    status_text_changed = Signal(str)  # the status bar sentence; empty with nothing to say

    def __init__(  # noqa: PLR0913 - each argument is a piece of the window it works with
        self,
        window: QWidget,
        controller: WorkspaceController,
        calendar: CalendarPage,
        page: InjuriesPage,
        *,
        today: Callable[[], date],
        theme: Callable[[], Theme],
        show_error: Callable[[ErrorMessage], None],
        show_page: Callable[[], None],
        make_id: Callable[[], InjuryId] = new_problem_id,
    ) -> None:
        super().__init__(window)
        self._window = window
        self._controller = controller
        self._calendar = calendar
        self._page = page
        self._today = today
        self._theme = theme
        self._show_error = show_error
        self._show_page = show_page
        self._make_id = make_id
        self._loader = InjuryLoader(controller, self)
        self._jobs = CountedInjuryOps(self._loader)
        self._stack = QUndoStack(self)
        self._records: InjuryList | None = None
        self._summaries: tuple[ProblemSummary, ...] = ()
        self._warnings: tuple[HealthWarning, ...] = ()
        self._known_halted: frozenset[str] = frozenset()
        self._wizard: InjuryWizard | None = None
        self._illness_dialog: IllnessDialog | None = None
        self._checkin_dialog: CheckInDialog | None = None
        self._alert: QMessageBox | None = None
        self._build_actions()
        page.attach_actions(
            InjuriesPageActions(
                self._report_injury_action,
                self._report_illness_action,
                self._check_in_action,
                self._review_action,
                self._close_action,
                self._undo_action,
                self._redo_action,
            )
        )
        self._connect()
        self._update_actions()

    # --- Construction -------------------------------------------------------------------

    def _action(self, text: str, shortcut: str | None, slot: Callable[[], object]) -> QAction:
        action = QAction(text, self)
        if shortcut is not None:
            action.setShortcut(QKeySequence(shortcut))
        action.triggered.connect(slot)
        return action

    def _build_actions(self) -> None:
        self._report_injury_action = self._action(
            self.tr("Report an injury..."), "Ctrl+J", self.report_injury
        )
        self._report_illness_action = self._action(
            self.tr("Report illness..."), "Ctrl+Shift+J", self.report_illness
        )
        self._check_in_action = self._action(self.tr("Daily check-in..."), "Ctrl+K", self.check_in)
        self._review_action = self._action(
            self.tr("Medical review done"), "Ctrl+Shift+V", self.confirm_review
        )
        self._close_action = self._action(self.tr("Close"), "Ctrl+Shift+X", self.close_problem)
        self._undo_action = self._stack.createUndoAction(self, self.tr("Undo"))
        # "Undo <change>" / "Redo <change>" at the end of the menu, like the plan: no global
        # shortcut (a global Ctrl+Z would also act while a text field has the focus).
        self._redo_action = self._stack.createRedoAction(self, self.tr("Redo"))
        self._stack.canUndoChanged.connect(self._update_actions)
        self._stack.canRedoChanged.connect(self._update_actions)

    def _connect(self) -> None:
        loader = self._loader
        loader.loaded.connect(self._on_loaded)
        loader.changed.connect(self._on_changed)
        loader.command_failed.connect(self._on_command_failed)
        loader.failed.connect(self._on_failed)
        self._controller.plan_rebuilt.connect(self._on_plan_rebuilt)
        self._page.selection_changed.connect(self._update_actions)
        self._page.problem_activated.connect(self._on_activated)

    # --- Test accessors --------------------------------------------------------------------

    @property
    def loader(self) -> InjuryLoader:
        """The injury jobs."""
        return self._loader

    @property
    def undo_stack(self) -> QUndoStack:
        """The stack of the commands on injuries and illnesses."""
        return self._stack

    @property
    def is_idle(self) -> bool:
        """True when no change is on its way to the store and the records have been read."""
        return self._jobs.pending == 0 and self._records is not None

    @property
    def summaries(self) -> tuple[ProblemSummary, ...]:
        """The records as last read."""
        return self._summaries

    @property
    def records(self) -> InjuryList | None:
        """The records as last read, with the domain objects (None until read)."""
        return self._records

    @property
    def wizard(self) -> InjuryWizard | None:
        """The open questionnaire, if any."""
        return self._wizard

    @property
    def illness_dialog(self) -> IllnessDialog | None:
        """The open illness dialog, if any."""
        return self._illness_dialog

    @property
    def checkin_dialog(self) -> CheckInDialog | None:
        """The open check-in dialog, if any."""
        return self._checkin_dialog

    @property
    def alert(self) -> QMessageBox | None:
        """The message that invites a visit to a doctor, while it is open."""
        return self._alert

    @property
    def actions(self) -> dict[str, QAction]:
        """The actions by name."""
        return {
            "report_injury": self._report_injury_action,
            "report_illness": self._report_illness_action,
            "check_in": self._check_in_action,
            "review": self._review_action,
            "close": self._close_action,
            "undo": self._undo_action,
            "redo": self._redo_action,
        }

    # --- The commands -----------------------------------------------------------------------

    def report_injury(self) -> None:
        """Open the questionnaire that reports an injury."""
        if self._wizard is not None:
            self._wizard.raise_()
            self._wizard.activateWindow()
            return
        if not self._ready:
            return
        wizard = InjuryWizard(self._today(), self._window)
        wizard.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        wizard.accepted.connect(partial(self._on_wizard_accepted, wizard))
        wizard.finished.connect(self._on_wizard_closed)
        self._wizard = wizard
        wizard.open()

    def _on_wizard_accepted(self, wizard: InjuryWizard) -> None:
        injury = injury_of(wizard.draft(), self._make_id(), self._today())
        if injury is None:
            self._show_error(self._not_saved_message())
            return
        self._stack.push(OpenProblemCommand(self._jobs, injury))

    def _on_wizard_closed(self) -> None:
        self._wizard = None

    def report_illness(self) -> None:
        """Open the dialog that reports an illness."""
        if self._illness_dialog is not None:
            self._illness_dialog.raise_()
            self._illness_dialog.activateWindow()
            return
        if not self._ready or self._open_illness() is not None:
            return
        dialog = IllnessDialog(self._today(), self._window)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.accepted.connect(partial(self._on_illness_accepted, dialog))
        dialog.finished.connect(self._on_illness_closed)
        self._illness_dialog = dialog
        dialog.open()

    def _on_illness_accepted(self, dialog: IllnessDialog) -> None:
        illness = illness_of(dialog.kind(), dialog.started_on(), self._make_id())
        if illness is None:
            self._show_error(self._not_saved_message())
            return
        self._stack.push(OpenProblemCommand(self._jobs, illness))

    def _on_illness_closed(self) -> None:
        self._illness_dialog = None

    def check_in(self) -> None:
        """Open today's check-in of the selected injury (or of the only open one)."""
        if self._checkin_dialog is not None:
            self._checkin_dialog.raise_()
            self._checkin_dialog.activateWindow()
            return
        target = self._target(lambda item: item.can_check_in)
        record = None if target is None else self._injury(target.problem_id)
        if target is None or record is None:
            return
        today = self._today()
        saved = next((c for c in record.checkins if c.day == today), None)
        dialog = CheckInDialog(problem_title(target), today, draft_of_checkin(saved), self._window)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.accepted.connect(partial(self._on_checkin_accepted, dialog, record.injury_id, today))
        dialog.finished.connect(self._on_checkin_closed)
        self._checkin_dialog = dialog
        dialog.open()

    def _on_checkin_accepted(self, dialog: CheckInDialog, injury_id: InjuryId, day: date) -> None:
        checkin = checkin_of(day, dialog.draft())
        if checkin is None:
            self._show_error(self._not_saved_message())
            return
        self._stack.push(CheckInCommand(self._jobs, injury_id, checkin))

    def _on_checkin_closed(self) -> None:
        self._checkin_dialog = None

    def confirm_review(self) -> None:
        """Say that a doctor has seen the warning signs of the selected injury: the path starts
        again from the first step. It can be undone, so nothing is asked first."""
        target = self._target(lambda item: item.can_review)
        if target is not None:
            self._stack.push(
                MedicalReviewCommand(self._jobs, InjuryId(target.problem_id), self._today())
            )

    def close_problem(self) -> None:
        """Close the selected injury or illness; it can be undone, so nothing is asked first."""
        target = self._target(lambda item: item.is_open)
        if target is not None:
            self._stack.push(CloseCommand(self._jobs, InjuryId(target.problem_id), self._today()))

    def _on_activated(self, problem_id: str) -> None:
        """Enter on a record: its check-in, if it takes one."""
        item = next((s for s in self._summaries if s.problem_id == problem_id), None)
        if item is not None and item.can_check_in:
            self.check_in()

    # --- What the commands act on -------------------------------------------------------------

    @property
    def _ready(self) -> bool:
        return self._controller.profile is not None and self._records is not None

    def _target(self, fits: Callable[[ProblemSummary], bool]) -> ProblemSummary | None:
        """The selected record if it fits; with no selection, the only record that fits."""
        if not self._ready:
            return None
        selected = self._page.selected_summary()
        if selected is not None:
            return selected if fits(selected) else None
        candidates = [item for item in self._summaries if fits(item)]
        return candidates[0] if len(candidates) == 1 else None

    def _injury(self, problem_id: str) -> Injury | None:
        records = self._records
        if records is None:
            return None
        for view in records.views:
            problem = view.problem
            if isinstance(problem, Injury) and problem.injury_id.value == problem_id:
                return problem
        return None

    def _open_illness(self) -> ProblemSummary | None:
        return next(
            (s for s in self._summaries if s.kind is ProblemKind.ILLNESS and s.is_open), None
        )

    def _not_saved_message(self) -> ErrorMessage:
        return ErrorMessage(
            self.tr("The answers cannot be saved"),
            self.tr("Something in the answers does not fit what can be recorded."),
            self.tr("Open the form again and check the answers."),
        )

    # --- What comes back from the data thread ---------------------------------------------------

    def _on_loaded(self, records: object) -> None:
        if isinstance(records, InjuryList) and self._jobs.pending == 0:
            self._apply(records, announce=False)

    def _on_changed(self, records: object) -> None:
        """A change went through: the records are read again. The overview (and the plan with it)
        is calculated again from them."""
        self._jobs.settled()
        if isinstance(records, InjuryList) and self._jobs.pending == 0:
            self._apply(records, announce=True)
        self._controller.request_overview_refresh()

    def _on_command_failed(self, message: object) -> None:
        """A change was refused: nothing changed, so the stack no longer matches the store."""
        self._jobs.settled()
        self._stack.clear()
        if isinstance(message, ErrorMessage):
            self._show_error(message)
        self._loader.load()  # the screen shows what is really stored

    def _on_failed(self, message: object) -> None:
        if isinstance(message, ErrorMessage):
            self._show_error(message)

    def _on_plan_rebuilt(self, result: object) -> None:
        if isinstance(result, PlanBuilt):
            self._warnings = health_warnings_of(result.plan.warnings)
        else:
            self._warnings = ()
        self._show()

    # --- Showing ---------------------------------------------------------------------------------

    def _apply(self, records: InjuryList, *, announce: bool) -> None:
        self._records = records
        self._summaries = summaries_of(records.views)
        halted = halted_ids(self._summaries)
        newly = halted - self._known_halted
        self._known_halted = halted
        self._show()
        self._calendar.set_problems(spans_of(self._summaries))
        self.status_text_changed.emit(status_bar_text(status_facts_of(self._summaries)))
        if announce and newly:
            self._show_alert()

    def _show(self) -> None:
        self._page.show_state(
            self._summaries, notices_of(self._summaries, self._warnings), self._theme()
        )
        self._update_actions()

    def _show_alert(self) -> None:
        """A message that invites a visit to a doctor: it does not block the window, and it stays
        until it is closed. The notice and the status bar stay as long as the sign is not seen
        to."""
        if self._alert is not None:
            self._alert.close()
        box = QMessageBox(
            QMessageBox.Icon.Warning,
            alert_title(),
            alert_body(),
            QMessageBox.StandardButton.NoButton,
            self._window,
        )
        box.setObjectName("injuryAlert")
        box.setTextFormat(Qt.TextFormat.PlainText)
        box.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        show = box.addButton(self.tr("Show the injuries"), QMessageBox.ButtonRole.AcceptRole)
        box.addButton(self.tr("Not now"), QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(show)
        box.setWindowModality(Qt.WindowModality.NonModal)
        show.clicked.connect(self._show_page)
        box.finished.connect(self._on_alert_closed)
        self._alert = box
        box.show()

    def _on_alert_closed(self) -> None:
        self._alert = None

    def _update_actions(self) -> None:
        ready = self._ready
        profile_open = self._controller.profile is not None
        if ready:
            reason = ""
        elif not profile_open:
            reason = self.tr("Create or open a profile first.")
        else:
            reason = self.tr("Wait a moment: the injuries are being read.")
        self._report_injury_action.setEnabled(ready)
        self._report_injury_action.setToolTip(reason)
        ill = ready and self._open_illness() is not None
        self._report_illness_action.setEnabled(ready and not ill)
        self._report_illness_action.setToolTip(
            reason or (self.tr("An illness is already open: close it first.") if ill else "")
        )
        self._set_target_action(
            self._check_in_action,
            lambda item: item.can_check_in,
            reason or self.tr("Select an open injury in the Injuries section first."),
        )
        self._set_target_action(
            self._review_action,
            lambda item: item.can_review,
            reason or self.tr("No warning sign is waiting for a doctor's visit."),
        )
        self._set_target_action(
            self._close_action,
            lambda item: item.is_open,
            reason or self.tr("Select an open injury or illness in the Injuries section first."),
        )
        self._undo_action.setToolTip(
            "" if self._stack.canUndo() else self.tr("There is nothing to take back.")
        )
        self._redo_action.setToolTip(
            "" if self._stack.canRedo() else self.tr("There is nothing to do again.")
        )

    def _set_target_action(
        self, action: QAction, fits: Callable[[ProblemSummary], bool], why_not: str
    ) -> None:
        enabled = self._target(fits) is not None
        action.setEnabled(enabled)
        action.setToolTip("" if enabled else why_not)

    # --- The window's events ----------------------------------------------------------------------

    def profile_changed(self) -> None:
        """Another profile opened: the stack, the dialogs and what was shown belong to the old one.
        The records of the new one are read."""
        self._stack.clear()
        self.close_dialogs()
        self._jobs.forget()
        self._records = None
        self._summaries = ()
        self._warnings = ()
        self._known_halted = frozenset()
        self._show()
        self._calendar.set_problems(())
        self.status_text_changed.emit("")
        self._loader.load()

    def theme_changed(self) -> None:
        """The colour scheme changed: the notices take the new colours."""
        self._show()

    def close_dialogs(self) -> None:
        """Close the open dialogs (another profile opens, or the window is closing)."""
        for dialog in (self._wizard, self._illness_dialog, self._checkin_dialog, self._alert):
            if dialog is not None:
                dialog.close()
