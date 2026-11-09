# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""The content of a Settings page: a widget that tells whether it holds unsaved edits.

Each category of the Settings window keeps its own way of saving (a "Save" button on the page, or
changes that apply at once). The window only needs three things from the content: to be told when
the user edits it, to ask whether something typed is still unsaved, and to ask it to save.
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

    def request_save(self) -> None:
        """Start saving what is typed, as the page's own "Save" button does."""
