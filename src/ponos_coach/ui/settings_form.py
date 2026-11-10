# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""The content of a Settings page: a widget that tells whether it holds unsaved edits.

The pages have no save button of their own: the window has the only "Save changes" button. The
window needs four things from the content: to be told when the user edits it (`edited`), to ask
whether something typed is still unsaved, whether it can be saved now (and, if not, why) and to ask
it to save. Some categories apply their changes at once and have nothing to save.
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QWidget


class SettingsForm(QWidget):
    """Content of one Settings page."""

    edited = Signal()  # the user changed something: `has_unsaved_changes` may have changed
    save_failed = Signal()  # a save the page started did not complete: the edits are still here

    def has_unsaved_changes(self) -> bool:
        """True while something typed has not been saved (false for pages that apply at once)."""
        return False

    def can_save(self) -> bool:
        """True while the window's "Save changes" button should be enabled. By default it follows
        the unsaved edits; a page overrides it when saving is a choice without edits (suggested
        values) or is not possible now (a problem, a save in progress). Emit `edited` when it
        changes."""
        return self.has_unsaved_changes()

    def save_blocked_reason(self) -> str:
        """Why `can_save` is false, for the tooltip of the button (empty: the window says that
        there is nothing to save)."""
        return ""

    def request_save(self) -> None:
        """Start saving what is typed, as the window's "Save changes" button does."""
