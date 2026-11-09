# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""The menu of a calendar day: what can be done on it, in one place.

Opened by the right button on a day, or by the Menu key or Shift+F10 on the selected day. It holds
the window's own actions (the same ones as the menus, with the same shortcuts, enabled state and
reason in the tooltip), so a command is never different here from what it is in the menu bar. The
commands on a goal or on an injury appear only on a day that has one; adding is always offered, and
a command that cannot be given says why in its tooltip. The commands of the whole plan (they used to
be in a menu "Plan") close the menu, in a group of their own, after those of the day.
"""

from __future__ import annotations

from collections.abc import Mapping

from PySide6.QtCore import QObject, QPoint, Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMenu

from ponos_coach.ui.calendar_model import CalendarDay
from ponos_coach.ui.calendar_page import CalendarPage

_GOAL_COMMANDS = ("change_goal", "remove_goal")
_INJURY_COMMANDS = ("check_in", "review", "close")


class CalendarDayMenu(QObject):
    """Builds and shows the menu of a day of the calendar."""

    def __init__(
        self,
        calendar: CalendarPage,
        *,
        open_day: QAction,
        plan_actions: Mapping[str, QAction],
        goals_window: QAction,
        injury_actions: Mapping[str, QAction],
    ) -> None:
        super().__init__(calendar)
        self._calendar = calendar
        self._open_day = open_day
        self._plan = plan_actions
        self._goals_window = goals_window
        self._injuries = injury_actions
        self._menu: QMenu | None = None
        calendar.day_menu_requested.connect(self._on_requested)

    @property
    def menu(self) -> QMenu | None:
        """The menu shown last, while it is open."""
        return self._menu

    def build(self, cell: CalendarDay) -> QMenu:
        """The menu of a day: open it, the goals and injuries of the day, then the commands of the
        whole plan (goals and status, training settings, undo and redo)."""
        menu = QMenu(self._calendar)
        menu.setObjectName("calendarDayMenu")
        menu.setToolTipsVisible(True)
        menu.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        menu.addAction(self._open_day)
        menu.addSeparator()
        for name in ("add_race", "add_form_goal"):
            menu.addAction(self._plan[name])
        if cell.goals:
            for name in _GOAL_COMMANDS:
                menu.addAction(self._plan[name])
        menu.addSeparator()
        for name in ("report_injury", "report_illness"):
            menu.addAction(self._injuries[name])
        if cell.problems:
            for name in _INJURY_COMMANDS:
                menu.addAction(self._injuries[name])
        self._add_plan_commands(menu)
        return menu

    def _add_plan_commands(self, menu: QMenu) -> None:
        """The commands of the whole plan, in a group of their own."""
        menu.addSeparator()
        menu.addAction(self._goals_window)
        menu.addAction(self._plan["training"])
        menu.addSeparator()
        menu.addAction(self._plan["undo"])
        menu.addAction(self._plan["redo"])

    def _on_requested(self, cell: object, position: object) -> None:
        if not isinstance(cell, CalendarDay) or not isinstance(position, QPoint):
            return
        menu = self.build(cell)
        menu.aboutToHide.connect(self._on_hidden)
        self._menu = menu
        menu.popup(position)

    def _on_hidden(self) -> None:
        self._menu = None
