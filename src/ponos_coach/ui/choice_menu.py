# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""A small menu that asks which one, when a command on a day fits more than one record.

A day can hold two goals or two injuries; "Change goal" then pops up this menu at the pointer, one
entry per record, named as the record is everywhere else. Choosing runs the command for that record;
Esc or a click elsewhere runs nothing.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from PySide6.QtCore import Qt
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import QMenu, QWidget


def popup_choices(parent: QWidget, choices: Sequence[tuple[str, Callable[[], object]]]) -> QMenu:
    """Pop up a menu with one entry per choice (text, what to do) and return it."""
    menu = QMenu(parent)
    menu.setObjectName("choiceMenu")
    menu.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
    for text, command in choices:
        action = menu.addAction(text.replace("&", "&&"))
        action.triggered.connect(lambda _checked=False, run=command: run())
    menu.popup(QCursor.pos())
    return menu
