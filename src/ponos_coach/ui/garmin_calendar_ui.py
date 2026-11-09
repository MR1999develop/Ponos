# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""The Garmin calendar in the main window: menu commands, progress in the status bar, dialogs and
summaries.

Humble view: the decisions (which commands are available, what a scope covers, how a job ended)
live in `garmin_calendar_model` and `garmin_calendar_text`; here there are only widgets and
commands. The jobs run on the thread of `SyncController`, one at a time with the synchronisation.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, timedelta

from PySide6.QtCore import QObject, Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
)

from ponos_coach.planning.calendar_use_cases import FailureReason, StopReason
from ponos_coach.planning.domain import Plan
from ponos_coach.planning.domain.workouts import ScheduledWorkout
from ponos_coach.ui.garmin_calendar_dialogs import (
    GarminCalendarDialog,
    ScopeOption,
    SendToGarminDialog,
)
from ponos_coach.ui.garmin_calendar_model import (
    LIST_AHEAD_DAYS,
    CalendarCommands,
    CalendarInputs,
    CalendarNotStarted,
    CalendarProgress,
    CalendarReason,
    CalendarRequest,
    CalendarResult,
    Deleted,
    DeleteRequest,
    Listed,
    ListRequest,
    RecreateRequest,
    Restored,
    ScopeChoice,
    SendRequest,
    commands_for,
    is_calendar_result,
    is_unsupported,
    needs_attention,
    needs_reconnect,
    plan_is_halted,
    scope_of,
    sessions_in,
    unplanned_app_workouts,
)
from ponos_coach.ui.garmin_calendar_text import (
    all_workouts_text,
    headline_text,
    list_problem_text,
    listed_workouts_text,
    progress_text,
    reason_text,
    starting_text,
    summary_of,
)
from ponos_coach.ui.sync_controller import SyncController
from ponos_coach.ui.tokens import SPACE_2, SPACE_4, STATUS_PROGRESS_WIDTH

_STATUS_MESSAGE_MS = 8000


class GarminCalendarUi(QObject):
    """ "Send plan to Garmin...", "Garmin calendar..." and the rest of the calendar commands."""

    def __init__(  # noqa: PLR0913 - each argument is a piece of the window it works with
        self,
        window: QMainWindow,
        controller: SyncController,
        *,
        plan: Callable[[], Plan | None],
        today: Callable[[], date],
        selected_day: Callable[[], date | None],
        remember_box: Callable[[QMessageBox], None],
        connect_account: Callable[[], None],
    ) -> None:
        super().__init__(window)
        self._window = window
        self._controller = controller
        self._plan = plan
        self._today = today
        self._selected_day = selected_day
        self._remember_box = remember_box
        self._connect_account = connect_account
        self._supported = True
        self._offer_unplanned = False  # after a recreation that left sessions out
        self._dialog: GarminCalendarDialog | None = None
        self._send_dialog: SendToGarminDialog | None = None
        self._build_actions()
        self._build_status_widgets()
        controller.state_changed.connect(self.refresh)
        controller.account_changed.connect(self._on_account_changed)
        controller.calendar_progress_changed.connect(self._on_progress)
        controller.calendar_job_finished.connect(self._on_job_finished)
        self.refresh()

    # --- Construction -----

    def _action(self, text: str, shortcut: str, slot: Callable[[], object]) -> QAction:
        action = QAction(text, self._window)
        action.setShortcut(QKeySequence(shortcut))
        action.triggered.connect(lambda _checked=False, run=slot: run())  # never the flag
        return action

    def _build_actions(self) -> None:
        self._send_plan_action = self._action(
            self.tr("Send plan to Garmin..."), "Ctrl+Shift+S", self.send_plan
        )
        self._send_session_action = self._action(
            self.tr("Send this session to Garmin"), "Ctrl+Shift+Y", self.send_selected_session
        )
        self._browse_action = self._action(
            self.tr("Garmin calendar..."), "Ctrl+Shift+C", self.show_calendar
        )
        self._recreate_action = self._action(
            self.tr("Recreate deleted workouts"), "Ctrl+Shift+D", self.recreate_deleted
        )
        self._stop_action = self._action(
            self.tr("Stop Garmin calendar task"),
            "Ctrl+Shift+X",
            self._controller.cancel_calendar_job,
        )

    def add_to_menu(self, menu: QMenu) -> None:
        """Add the calendar commands to the "Garmin" menu."""
        menu.addSeparator()
        menu.addAction(self._send_plan_action)
        menu.addAction(self._send_session_action)
        menu.addAction(self._browse_action)
        menu.addAction(self._recreate_action)
        menu.addAction(self._stop_action)

    def _build_status_widgets(self) -> None:
        window = self._window
        self._label = QLabel(window)
        self._label.setObjectName("calendarJobStatus")
        self._label.setTextFormat(Qt.TextFormat.PlainText)
        self._label.setContentsMargins(SPACE_2, 0, SPACE_4, 0)
        self._bar = QProgressBar(window)
        self._bar.setTextVisible(False)
        self._bar.setFixedWidth(STATUS_PROGRESS_WIDTH)
        self._bar.setAccessibleName(self.tr("Garmin calendar task progress"))
        self._stop_button = QPushButton(self.tr("Stop"), window)
        self._stop_button.setObjectName("calendarJobStop")
        self._stop_button.setAccessibleName(self.tr("Stop Garmin calendar task"))
        self._stop_button.clicked.connect(self._controller.cancel_calendar_job)
        self._widgets = (self._label, self._bar, self._stop_button)
        status_bar = window.statusBar()
        for widget in self._widgets:
            status_bar.addPermanentWidget(widget)
            widget.setVisible(False)

    # --- Accessors for tests -----

    @property
    def send_plan_action(self) -> QAction:
        """Action "Send plan to Garmin..."."""
        return self._send_plan_action

    @property
    def send_session_action(self) -> QAction:
        """Action "Send this session to Garmin" (also the day panel's button)."""
        return self._send_session_action

    @property
    def browse_action(self) -> QAction:
        """Action "Garmin calendar..."."""
        return self._browse_action

    @property
    def recreate_action(self) -> QAction:
        """Action "Recreate deleted workouts"."""
        return self._recreate_action

    @property
    def stop_action(self) -> QAction:
        """Action "Stop Garmin calendar task"."""
        return self._stop_action

    @property
    def stop_button(self) -> QPushButton:
        """The "Stop" button in the status bar."""
        return self._stop_button

    @property
    def status_text(self) -> str:
        """The progress text in the status bar (empty if no calendar task runs)."""
        return "" if self._label.isHidden() else self._label.text()

    @property
    def send_dialog(self) -> SendToGarminDialog | None:
        """The open "Send plan to Garmin" dialog, if any."""
        return self._send_dialog

    @property
    def calendar_dialog(self) -> GarminCalendarDialog | None:
        """The open "Garmin calendar" dialog, if any."""
        return self._dialog

    # --- Command state -----

    def _commands(self) -> CalendarCommands:
        return commands_for(
            CalendarInputs(
                self._controller.commands.sync.reason,
                self._supported,
                self._plan(),
                self._today(),
                self._selected_day(),
            )
        )

    def refresh(self) -> None:
        """Align commands, status bar and open dialog with what the controller says."""
        commands = self._commands()
        for action, availability in (
            (self._send_plan_action, commands.send_plan),
            (self._send_session_action, commands.send_session),
            (self._browse_action, commands.browse),
            (self._recreate_action, commands.recreate),
        ):
            self._apply(action, availability.reason)
        stoppable = self._controller.is_calendar_busy
        self._stop_action.setEnabled(stoppable)
        self._stop_action.setToolTip("" if stoppable else self.tr("No Garmin calendar task runs."))
        self._stop_button.setEnabled(stoppable)
        for widget in self._widgets:
            widget.setVisible(stoppable)
        if stoppable and self._label.text() == "":
            self._show_progress(None)
        if self._dialog is not None:
            self._dialog.set_busy(stoppable)

    @staticmethod
    def _apply(action: QAction, reason: CalendarReason | None) -> None:
        action.setEnabled(reason is None)
        action.setToolTip("" if reason is None else reason_text(reason))

    def _on_account_changed(self, _status: object) -> None:
        self._supported = True  # the connection may be a new one: ask again
        self.refresh()

    def _on_progress(self, progress: object) -> None:
        if isinstance(progress, CalendarProgress):
            self._show_progress(progress)

    def _show_progress(self, progress: CalendarProgress | None) -> None:
        if progress is None:
            self._label.setText(starting_text())
            self._bar.setRange(0, 0)
            return
        self._label.setText(progress_text(progress))
        if progress.total:
            self._bar.setRange(0, progress.total)
            self._bar.setValue(min(progress.done, progress.total))
        else:
            self._bar.setRange(0, 0)  # unknown length

    # --- Send -----

    def send_plan(self) -> None:
        """Ask what to send (the whole plan, the selected week or day) and send it."""
        plan = self._plan()
        if plan is None or not self._commands().send_plan.enabled or self._send_dialog is not None:
            return
        dialog = SendToGarminDialog(self._scope_options(plan), self._window)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.chosen.connect(self._send_chosen)
        dialog.finished.connect(self._on_send_dialog_closed)
        self._send_dialog = dialog
        dialog.open()

    def _scope_options(self, plan: Plan) -> tuple[ScopeOption, ...]:
        commands = self._commands()
        today, selected = self._today(), self._selected_day()
        options: list[ScopeOption] = []
        for choice, label, availability in (
            (ScopeChoice.WHOLE_PLAN, self.tr("The whole plan from today on"), commands.send_plan),
            (
                ScopeChoice.WEEK,
                self.tr("The week of the selected day"),
                commands.send_week,
            ),
            (
                ScopeChoice.SESSION,
                self.tr("The session of the selected day"),
                commands.send_session,
            ),
        ):
            scope = scope_of(choice, selected)
            count = 0 if scope is None else sessions_in(plan, scope, today)
            reason = availability.reason
            options.append(
                ScopeOption(
                    choice,
                    label,
                    count,
                    availability.enabled,
                    "" if reason is None else reason_text(reason),
                )
            )
        return tuple(options)

    def _send_chosen(self, choice: object) -> None:
        if isinstance(choice, ScopeChoice):
            self._start_send(choice)

    def _on_send_dialog_closed(self) -> None:
        self._send_dialog = None

    def send_selected_session(self) -> None:
        """Send the session of the selected day, without asking (it is one workout)."""
        self._start_send(ScopeChoice.SESSION)

    def _start_send(self, choice: ScopeChoice) -> None:
        plan = self._plan()
        scope = scope_of(choice, self._selected_day())
        if plan is None or scope is None:
            return
        self._start(SendRequest(plan, scope, self._today()))

    # --- Calendar list, deletion and recreation -----

    def show_calendar(self) -> None:
        """Open the list of the workouts on the Garmin calendar and read it."""
        if self._dialog is not None or not self._commands().browse.enabled:
            return
        dialog = GarminCalendarDialog(self._window)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.refresh_requested.connect(self._read_calendar)
        dialog.delete_requested.connect(self._confirm_deletion)
        dialog.recreate_requested.connect(self.recreate_deleted)
        dialog.finished.connect(self._on_dialog_closed)
        self._dialog = dialog
        dialog.open()
        self._read_calendar()

    def _on_dialog_closed(self) -> None:
        self._dialog = None

    def _read_calendar(self) -> None:
        today = self._today()
        if self._dialog is not None:
            self._dialog.show_loading()
        self._start(ListRequest(today, today + timedelta(days=LIST_AHEAD_DAYS)))

    def _confirm_deletion(self, items: object) -> None:
        if not isinstance(items, tuple) or not items:
            return
        workouts = tuple(item for item in items if isinstance(item, ScheduledWorkout))
        self._ask_deletion(
            workouts,
            self.tr(
                "Delete the selected workouts from the Garmin calendar? (%n)", "", len(workouts)
            ),
        )

    def _offer_unplanned_deletion(self, workouts: tuple[ScheduledWorkout, ...]) -> None:
        """After a recreation that left sessions out: the plan changed or is stopped, so the
        app's workouts of days it no longer plans are offered for deletion (same confirmation)."""
        if not workouts:
            return
        if plan_is_halted(self._plan()):
            reason = self.tr("The plan is stopped, so it has no sessions to do.")
        else:
            reason = self.tr("The plan has changed since these workouts were sent.")
        question = self.tr(
            "Delete the workouts of days the plan no longer has from the Garmin calendar? (%n)",
            "",
            len(workouts),
        )
        self._ask_deletion(workouts, reason + "\n" + question)

    def _ask_deletion(self, workouts: tuple[ScheduledWorkout, ...], question: str) -> None:
        count = len(workouts)
        box = QMessageBox(self._dialog or self._window)
        box.setObjectName("deleteWorkoutsConfirmation")
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle(self.tr("Delete workouts"))
        box.setTextFormat(Qt.TextFormat.PlainText)
        box.setText(question)
        box.setInformativeText(
            listed_workouts_text(workouts)
            + "\n\n"
            + self.tr("A copy of each is kept on this computer, so you can create them again.")
        )
        box.setDetailedText(all_workouts_text(workouts))
        delete = box.addButton(
            self.tr("Delete workouts (%n)", "", count), QMessageBox.ButtonRole.DestructiveRole
        )
        keep = box.addButton(self.tr("Keep them"), QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(keep)
        box.setEscapeButton(keep)
        box.buttonClicked.connect(lambda clicked: self._on_delete_answer(clicked, delete, workouts))
        self._remember_box(box)
        box.open()

    def _on_delete_answer(
        self, clicked: object, delete: object, workouts: tuple[ScheduledWorkout, ...]
    ) -> None:
        if clicked is delete:
            self._start(DeleteRequest(workouts, self._today()))

    def recreate_deleted(self) -> None:
        """Create again the deleted workouts that wait (their local copies)."""
        if not self._commands().recreate.enabled:
            return
        self._start(RecreateRequest(self._today(), self._plan()))

    def _start(self, request: CalendarRequest) -> None:
        """Start a job. If it cannot start now the list dialog, if open, says why instead of
        waiting for an answer that will not come."""
        self._label.setText("")  # the first progress sets it
        if self._controller.start_calendar_job(request):
            return
        if self._dialog is not None:
            self._dialog.show_workouts((), 0, reason_text(CalendarReason.BUSY))

    # --- Outcomes -----

    def _on_job_finished(self, result: object) -> None:
        if not is_calendar_result(result):  # the controller sends only results
            return
        if is_unsupported(result):
            self._supported = False
        self._label.setText("")
        self.refresh()
        self._window.statusBar().showMessage(headline_text(result), _STATUS_MESSAGE_MS)
        if isinstance(result, Listed):
            self._show_list(result)
            self._offer_unplanned_if_asked(result)
            return
        if self._dialog is not None and isinstance(result, CalendarNotStarted):
            self._dialog.show_workouts((), 0, headline_text(result))
        self._show_summary(result)
        self._read_calendar_again_if_needed(result)

    def _read_calendar_again_if_needed(self, result: CalendarResult) -> None:
        if isinstance(result, Restored) and any(
            failure.reason is FailureReason.NOT_IN_PLAN for failure in result.outcome.failed
        ):
            self._offer_unplanned = True  # read the calendar to see what is left of the old plan
        changed = isinstance(result, Deleted | Restored)
        if (self._dialog is not None and changed) or self._offer_unplanned:
            self._read_calendar()  # what was deleted or created again is no longer as listed

    def _offer_unplanned_if_asked(self, result: Listed) -> None:
        if not self._offer_unplanned:
            return
        self._offer_unplanned = False
        if result.outcome.stop is StopReason.COMPLETED:
            self._offer_unplanned_deletion(
                unplanned_app_workouts(self._plan(), result.outcome.workouts, self._today())
            )

    def _show_list(self, result: Listed) -> None:
        if self._dialog is not None:
            self._dialog.show_workouts(
                result.outcome.workouts, len(result.pending), list_problem_text(result)
            )
        if (self._dialog is None and needs_attention(result)) or needs_reconnect(result):
            self._show_summary(result)

    def _show_summary(self, result: CalendarResult) -> None:
        summary = summary_of(result)
        if summary is None:
            return
        box = QMessageBox(self._dialog or self._window)
        box.setObjectName("calendarSummary")
        attention = needs_attention(result)
        box.setIcon(QMessageBox.Icon.Warning if attention else QMessageBox.Icon.Information)
        box.setWindowTitle(self.tr("Garmin calendar"))
        box.setTextFormat(Qt.TextFormat.PlainText)
        box.setText(summary.title)
        box.setInformativeText("\n".join(summary.lines))
        reconnect: QPushButton | None = None
        if needs_reconnect(result):
            reconnect = box.addButton(
                self.tr("Connect account..."), QMessageBox.ButtonRole.AcceptRole
            )
        close = box.addButton(QMessageBox.StandardButton.Close)
        box.setDefaultButton(close if reconnect is None else reconnect)
        box.setEscapeButton(close)
        if reconnect is not None:
            reconnect.clicked.connect(self._connect_account)
        self._remember_box(box)
        box.open()
