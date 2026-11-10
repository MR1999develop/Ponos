# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Deleting activities in the window: the commands, the undo, the "Deleted activities" window.

Deleting asks nothing: the activities selected in the Activities list go out at once, the status
bar says what went and that the undo brings it back (the shortcut is shown), and the undo stack
holds the step. The window "Deleted activities" lists what was deleted and restores it; restoring
goes through the same stack, so its undo deletes again. The shortcuts act while the Activities list
has the focus (a global Ctrl+Z would also act while a text field has it); the menu entries are
always there, each with the reason if it cannot be used now.

Owns the actions, the undo stack, the jobs of `ActivityDeletionLoader` and the window; the main
window only gives callbacks and puts the actions in its menu and in the row menu of the list. After
every change the overview is recomputed, and with it the table, the calendar, the charts, the map
and the plan, as after an import. The profile is read only on the data thread. Nothing is logged.
"""

from __future__ import annotations

from collections.abc import Callable
from zoneinfo import ZoneInfo

from PySide6.QtCore import QObject, Qt, QTimer
from PySide6.QtGui import QAction, QKeySequence, QUndoStack
from PySide6.QtWidgets import QWidget

from ponos_coach.activities.domain import ActivityIdentity
from ponos_coach.ui.activity_deletion_loader import (
    ActivityChange,
    ActivityDeletionLoader,
    DeletedList,
)
from ponos_coach.ui.activity_deletion_undo import (
    DeleteActivitiesCommand,
    RestoreActivitiesCommand,
)
from ponos_coach.ui.deleted_activities_page import DeletedActivitiesPage, title_of
from ponos_coach.ui.errors import ErrorMessage
from ponos_coach.ui.page_window import PageWindow
from ponos_coach.ui.workspace import WorkspaceController


class ActivityDeletionUi(QObject):
    """Owns the deleting and restoring of activities in the window."""

    def __init__(
        self,
        window: QWidget,
        controller: WorkspaceController,
        *,
        selected: Callable[[], tuple[ActivityIdentity, ...]],
        zone: Callable[[], ZoneInfo],
        show_error: Callable[[ErrorMessage], None],
        notify: Callable[[str], None],
    ) -> None:
        super().__init__(window)
        self._controller = controller
        self._selected = selected
        self._zone = zone
        self._show_error = show_error
        self._notify = notify
        self._loader = ActivityDeletionLoader(controller, self)
        self._stack = QUndoStack(self)
        self._delete_action = QAction(self.tr("Delete activity"), self)
        self._delete_action.setObjectName("deleteActivityAction")
        self._delete_action.setShortcuts(QKeySequence.keyBindings(QKeySequence.StandardKey.Delete))
        self._delete_action.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self._delete_action.triggered.connect(lambda _checked=False: self.delete_selected())
        self._undo_action = self._stack.createUndoAction(self, self.tr("Undo"))
        self._undo_action.setShortcuts(QKeySequence.keyBindings(QKeySequence.StandardKey.Undo))
        self._undo_action.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self._redo_action = self._stack.createRedoAction(self, self.tr("Redo"))
        self._redo_action.setShortcuts(QKeySequence.keyBindings(QKeySequence.StandardKey.Redo))
        self._redo_action.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self._deleted_action = QAction(self.tr("Deleted activities..."), self)
        self._deleted_action.setObjectName("deletedActivitiesAction")
        self._deleted_action.triggered.connect(lambda _checked=False: self.show_window())
        self._page = DeletedActivitiesPage()
        self._page.restore_requested.connect(self._on_restore_requested)
        self._window = PageWindow(
            self.tr("Deleted activities"), self._page, window, name="deletedActivitiesWindow"
        )
        self._stack.canUndoChanged.connect(self._update_undo_tips)
        self._stack.canRedoChanged.connect(self._update_undo_tips)
        self._loader.loaded.connect(self._on_loaded)
        self._loader.changed.connect(self._on_changed)
        self._loader.command_failed.connect(self._on_command_failed)
        self._loader.failed.connect(self._on_failed)
        self._update_undo_tips()
        self.refresh_actions()

    # --- Accessors for the window and the tests ---------------------------------------------

    @property
    def delete_action(self) -> QAction:
        """ "Delete activity", with `Del`; also in the row menu of the list."""
        return self._delete_action

    @property
    def undo_action(self) -> QAction:
        """ "Undo <step>", with Ctrl+Z while the Activities list has the focus."""
        return self._undo_action

    @property
    def redo_action(self) -> QAction:
        """ "Redo <step>"."""
        return self._redo_action

    @property
    def deleted_action(self) -> QAction:
        """ "Deleted activities...": opens the window."""
        return self._deleted_action

    @property
    def undo_stack(self) -> QUndoStack:
        """The stack of the deletions and restorations."""
        return self._stack

    @property
    def window(self) -> PageWindow:
        """The window "Deleted activities"."""
        return self._window

    @property
    def page(self) -> DeletedActivitiesPage:
        """The page inside the window."""
        return self._page

    @property
    def loader(self) -> ActivityDeletionLoader:
        """The jobs."""
        return self._loader

    # --- Commands ---------------------------------------------------------------------------

    def delete_selected(self) -> None:
        """Delete the activities selected in the list (no effect without a selection, or while
        the profile cannot be changed)."""
        identities = self._selected()
        if not identities or not self._controller.can_edit_profile:
            return
        self._stack.push(DeleteActivitiesCommand(self._loader, identities, self._on_refused))

    def _on_restore_requested(self, identities: object) -> None:
        if not isinstance(identities, tuple) or not identities:
            return
        self._stack.push(RestoreActivitiesCommand(self._loader, identities, self._on_refused))

    def _on_refused(self) -> None:
        """A job could not even start (no profile, window closing): the stack no longer matches the
        data. Cleared after the command has returned."""
        QTimer.singleShot(0, self._stack.clear)

    # --- The window -------------------------------------------------------------------------

    def show_window(self) -> None:
        """Open the window "Deleted activities", with the list read again."""
        self._window.bring_forward()
        self._loader.load()

    def profile_changed(self) -> None:
        """Another profile is open: the steps of the old one go, the window shows the new one."""
        self._stack.clear()
        self._page.show_deleted((), self._zone())
        if self._window.isVisible():
            self._loader.load()
        self.refresh_actions()

    def data_changed(self) -> None:
        """The profile's data changed without this window (an import brought an activity back):
        the window, if it is open, reads the list again."""
        if self._window.isVisible():
            self._loader.load()

    def refresh_actions(self) -> None:
        """ "Delete activity" with its reason if it cannot be done now, and the name of the step."""
        controller = self._controller
        count = len(self._selected())
        if controller.profile is None:
            allowed, why = False, self.tr("Create or open a profile first.")
        elif count == 0:
            allowed, why = False, self.tr("Select an activity in the Activities list first.")
        elif not controller.can_edit_profile:
            allowed, why = (
                False,
                self.tr("Wait for the current operation to finish, or stop it first."),
            )
        else:
            allowed, why = True, ""
        self._delete_action.setText(
            self.tr("Delete %n activities", "", count) if count > 1 else self.tr("Delete activity")
        )
        self._delete_action.setEnabled(allowed)
        self._delete_action.setToolTip(why)
        self._deleted_action.setEnabled(controller.profile is not None)
        self._deleted_action.setToolTip(
            "" if controller.profile is not None else self.tr("Create or open a profile first.")
        )

    def _update_undo_tips(self) -> None:
        self._undo_action.setToolTip(
            "" if self._stack.canUndo() else self.tr("There is no deletion to take back.")
        )
        self._redo_action.setToolTip(
            "" if self._stack.canRedo() else self.tr("There is no deletion to do again.")
        )

    # --- Outcomes ---------------------------------------------------------------------------

    def _on_loaded(self, loaded: object) -> None:
        if isinstance(loaded, DeletedList):
            self._page.show_deleted(loaded.deleted, self._zone())

    def _on_changed(self, change: object) -> None:
        if not isinstance(change, ActivityChange):
            return
        self._page.show_deleted(change.after.deleted, self._zone())
        self._controller.request_overview_refresh()
        if change.deleted:
            self._notify(self._deleted_message(change))
        elif change.restored:
            self._notify(self._restored_message(change.restored))
        if change.not_restored:
            self._show_error(self._not_restored_message(change.not_restored))

    def _on_command_failed(self, message: object) -> None:
        """A change did not go through: nothing changed, so the stack no longer matches."""
        self._stack.clear()
        if isinstance(message, ErrorMessage):
            self._show_error(message)
        self._loader.load()  # the window shows what is really stored

    def _on_failed(self, message: object) -> None:
        if isinstance(message, ErrorMessage):
            self._show_error(message)

    # --- Texts --------------------------------------------------------------------------------

    def _deleted_message(self, change: ActivityChange) -> str:
        undo = QKeySequence(QKeySequence.StandardKey.Undo).toString(
            QKeySequence.SequenceFormat.NativeText
        )
        if len(change.deleted) == 1:
            # {activity}: the sport and start of the deleted activity; {undo}: the undo shortcut.
            return self.tr("Deleted {activity}. Press {undo} to bring it back.").format(
                activity=title_of(change.deleted[0], self._zone()), undo=undo
            )
        # {undo}: the undo shortcut.
        return self.tr(
            "%n activities deleted. Press {undo} to bring them back.", "", len(change.deleted)
        ).format(undo=undo)

    def _restored_message(self, count: int) -> str:
        if count == 1:
            return self.tr("Activity restored.")
        return self.tr("%n activities restored.", "", count)

    def _not_restored_message(self, count: int) -> ErrorMessage:
        return ErrorMessage(
            self.tr("Could not restore"),
            self.tr("The original file of the activity is missing or damaged.")
            if count == 1
            else self.tr("The original files of %n activities are missing or damaged.", "", count),
            self.tr(
                "It stays in Deleted activities. Choose its file with Import files to bring it "
                "back."
            ),
        )
