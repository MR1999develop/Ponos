# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""The threshold test in the window: instructions, review and apply, history, undo and retest.

Owns the dialogs, the undo stack of the applied tests and the jobs of `ThresholdLoader`; the window
only gives it callbacks and puts its actions in the menus. Evaluating opens the review at once in a
"working" state and fills it in when the data thread answers; closing it drops the answer that is
on its way.
An apply goes through the dialog and only then is pushed on the stack, so the stack holds exactly
what was applied. After an apply, an undo or a redo the overview is recalculated and the history
read again. The profile is read only on the data thread.
"""

from __future__ import annotations

from collections.abc import Callable
from zoneinfo import ZoneInfo

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QAction, QKeySequence, QUndoStack
from PySide6.QtWidgets import QWidget

from ponos_coach.activities.domain import ActivityIdentity
from ponos_coach.assessment.domain import ThresholdRejection
from ponos_coach.load.use_cases import ExcludedActivity
from ponos_coach.ui.errors import ErrorMessage, unavailable_detail_message
from ponos_coach.ui.threshold_dialogs import (
    ThresholdHistoryDialog,
    ThresholdInstructionsDialog,
    ThresholdResultDialog,
)
from ponos_coach.ui.threshold_loader import ThresholdApplied, ThresholdLoader
from ponos_coach.ui.threshold_presentation import (
    ThresholdHistory,
    ThresholdReview,
    applied_text,
    present_review,
    rejection_message,
    retest_status_bar_text,
)
from ponos_coach.ui.threshold_undo import ApplyThresholdTestCommand
from ponos_coach.ui.workspace import WorkspaceController


class ThresholdUi(QObject):
    """Owns the threshold test flow of the window."""

    retest_text_changed = Signal(str)  # the status bar sentence; empty without a profile

    def __init__(
        self,
        window: QWidget,
        controller: WorkspaceController,
        *,
        zone: Callable[[], ZoneInfo],
        show_error: Callable[[ErrorMessage], None],
        notify: Callable[[str], None],
    ) -> None:
        super().__init__(window)
        self._window = window
        self._controller = controller
        self._zone = zone
        self._show_error = show_error
        self._notify = notify
        self._loader = ThresholdLoader(controller, self)
        self._stack = QUndoStack(self)
        self._undo_action = self._stack.createUndoAction(self, self.tr("Undo"))
        self._undo_action.setShortcut(QKeySequence.StandardKey.Undo)
        self._redo_action = self._stack.createRedoAction(self, self.tr("Redo"))
        self._redo_action.setShortcut(QKeySequence.StandardKey.Redo)
        self._stack.canUndoChanged.connect(self._update_undo_tips)
        self._stack.canRedoChanged.connect(self._update_undo_tips)
        self._update_undo_tips()
        self._result_dialog: ThresholdResultDialog | None = None
        self._history_dialog: ThresholdHistoryDialog | None = None
        self._review: ThresholdReview | None = None
        self._waiting_for_history = False
        self._loader.evaluated.connect(self._on_evaluated)
        self._loader.applied.connect(self._on_applied)
        self._loader.apply_failed.connect(self._on_apply_failed)
        self._loader.changed.connect(self._on_changed)
        self._loader.command_failed.connect(self._on_command_failed)
        self._loader.history_loaded.connect(self._on_history_loaded)
        self._loader.failed.connect(self._on_failed)

    # --- Test accessors -------------------------------------------------------------

    @property
    def loader(self) -> ThresholdLoader:
        """The threshold test jobs."""
        return self._loader

    @property
    def undo_stack(self) -> QUndoStack:
        """The stack of the applied tests."""
        return self._stack

    @property
    def undo_action(self) -> QAction:
        """ "Undo", for the Edit menu."""
        return self._undo_action

    @property
    def redo_action(self) -> QAction:
        """ "Redo", for the Edit menu."""
        return self._redo_action

    @property
    def result_dialog(self) -> ThresholdResultDialog | None:
        """The open review of a test, if any."""
        return self._result_dialog

    @property
    def history_dialog(self) -> ThresholdHistoryDialog | None:
        """The open history, if any."""
        return self._history_dialog

    # --- Instructions ---------------------------------------------------------------

    def show_instructions(self) -> None:
        """Open the instructions of the test."""
        dialog = ThresholdInstructionsDialog(self._window)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.open()

    # --- Evaluate and apply ---------------------------------------------------------

    def start_test(self, identity: ActivityIdentity) -> None:
        """Open the review of an activity as the threshold test and start evaluating it."""
        if self._controller.profile is None:
            return
        if self._result_dialog is not None:
            self._result_dialog.raise_()
            self._result_dialog.activateWindow()
            return
        dialog = ThresholdResultDialog(self._window)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.apply_requested.connect(self._on_apply_requested)
        dialog.finished.connect(self._on_result_closed)
        self._result_dialog = dialog
        self._review = None
        dialog.open()
        if not self._loader.evaluate(identity):
            dialog.close()

    def _on_evaluated(self, verdict: object) -> None:
        dialog = self._result_dialog
        if dialog is None:
            return
        if isinstance(verdict, ThresholdReview):
            self._review = verdict
            dialog.show_review(present_review(verdict, self._zone()))
        elif isinstance(verdict, ThresholdRejection):
            dialog.show_message(rejection_message(verdict))
        elif isinstance(verdict, ExcludedActivity):
            dialog.show_message(unavailable_detail_message(verdict.reason))

    def _on_apply_requested(self) -> None:
        dialog = self._result_dialog
        review = self._review
        if dialog is None or review is None:
            return
        if not self._controller.can_edit_profile or not self._loader.apply(review.result):
            dialog.show_failure(self._busy_message())

    def _busy_message(self) -> ErrorMessage:
        return ErrorMessage(
            self.tr("The profile is busy"),
            self.tr("The threshold cannot be saved while the profile is being used."),
            self.tr("Apply the test again when the operation has finished."),
        )

    def _on_applied(self, applied: object) -> None:
        if not isinstance(applied, ThresholdApplied):
            return
        self._stack.push(ApplyThresholdTestCommand(self._loader, applied.result, applied.cell))
        done = applied.cell.value
        if done is not None:
            self._notify(applied_text(applied.result, done.period))
        if self._result_dialog is not None:
            self._result_dialog.accept()

    def _on_apply_failed(self, message: object) -> None:
        if not isinstance(message, ErrorMessage):
            return
        if self._result_dialog is not None:
            self._result_dialog.show_failure(message)
        else:
            self._show_error(message)

    def _on_result_closed(self) -> None:
        self._result_dialog = None
        self._review = None
        self._loader.cancel_evaluation()

    # --- After a change -------------------------------------------------------------

    def _on_changed(self) -> None:
        """A test was applied, taken back or applied again: the load depends on the threshold."""
        self._controller.request_overview_refresh()
        self._loader.load_history()

    def _on_command_failed(self, message: object) -> None:
        """A redo or an undo was refused: nothing changed, so the stack no longer matches."""
        self._stack.clear()
        if isinstance(message, ErrorMessage):
            self._show_error(message)
        self._loader.load_history()

    def _update_undo_tips(self) -> None:
        self._undo_action.setToolTip(
            "" if self._stack.canUndo() else self.tr("There is no threshold test to take back.")
        )
        self._redo_action.setToolTip(
            "" if self._stack.canRedo() else self.tr("There is no threshold test to apply again.")
        )

    # --- History and retest ---------------------------------------------------------

    def show_history(self) -> None:
        """Read the history and open it (no effect if it is already open)."""
        if self._history_dialog is not None:
            self._history_dialog.raise_()
            self._history_dialog.activateWindow()
            return
        if self._waiting_for_history or self._controller.profile is None:
            return
        self._waiting_for_history = self._loader.load_history()

    def _on_history_loaded(self, history: object) -> None:
        if not isinstance(history, ThresholdHistory):
            return
        self.retest_text_changed.emit(retest_status_bar_text(history.retest))
        if self._history_dialog is not None:
            self._history_dialog.show_history(history, self._zone())
        elif self._waiting_for_history:
            self._waiting_for_history = False
            dialog = ThresholdHistoryDialog(self._window)
            dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
            dialog.finished.connect(self._on_history_closed)
            dialog.show_history(history, self._zone())
            self._history_dialog = dialog
            dialog.open()

    def _on_history_closed(self) -> None:
        self._history_dialog = None

    def _on_failed(self, message: object) -> None:
        self._waiting_for_history = False
        if not isinstance(message, ErrorMessage):
            return
        if self._result_dialog is not None and self._result_dialog.is_loading:
            self._result_dialog.show_message(message)
        else:
            self._show_error(message)

    # --- The window's events --------------------------------------------------------

    def profile_changed(self) -> None:
        """Another profile opened: the stack and the dialogs belong to the old one."""
        self._stack.clear()
        self.close_dialogs()
        self.retest_text_changed.emit("")
        self._loader.load_history()

    def data_changed(self) -> None:
        """Activities or the profile's data changed: read the retest status again."""
        self._loader.load_history()

    def close_dialogs(self) -> None:
        """Close the open dialogs (another profile opens, or the window is closing)."""
        for dialog in (self._result_dialog, self._history_dialog):
            if dialog is not None:
                dialog.close()
