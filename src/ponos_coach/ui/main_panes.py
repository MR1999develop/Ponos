# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""The four panes of the main window, always visible and all resizable.

Overview and Calendar in the left column, the activities (under their filter bar) and the map in the
right one. A horizontal splitter divides the columns and a vertical one each column; the user drags
any of them, no pane can be collapsed away, and where the dividers were left is remembered between
sessions. The panes are independent views of the same data, so this widget only places them.
"""

from __future__ import annotations

from enum import Enum

from PySide6.QtCore import QByteArray, QSettings, Qt
from PySide6.QtGui import QShowEvent
from PySide6.QtWidgets import QSplitter, QWidget

KEY_COLUMNS = "panes/columns"
KEY_LEFT = "panes/left2"  # "2": the Overview taller than the Calendar from 2026-11-10
KEY_RIGHT = "panes/right"
_HALF = (1, 1)  # columns, and Activities over Map: about half each
OVERVIEW_SHARE = (3, 2)  # Overview over Calendar: the charts get the larger part


class Pane(Enum):
    """The four panes, in the order of the keyboard focus."""

    OVERVIEW = 0
    CALENDAR = 1
    ACTIVITIES = 2
    MAP = 3


class MainPanes(QSplitter):
    """Two columns of two panes, with the position of every divider remembered."""

    def __init__(
        self,
        *,
        overview: QWidget,
        calendar: QWidget,
        activities: QWidget,
        map_page: QWidget,
        settings: QSettings,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(Qt.Orientation.Horizontal, parent)
        self._settings = settings
        self.setObjectName("mainPanes")
        self._left = self._column("leftColumn", overview, calendar)
        self._right = self._column("rightColumn", activities, map_page)
        self._panes = {
            Pane.OVERVIEW: overview,
            Pane.CALENDAR: calendar,
            Pane.ACTIVITIES: activities,
            Pane.MAP: map_page,
        }
        self._prepare(self, (self._left, self._right))
        self._proportions = {self: _HALF, self._left: OVERVIEW_SHARE, self._right: _HALF}
        self._to_size: list[QSplitter] = []  # the splitters with nothing saved, sized at the show
        for splitter, key in (
            (self, KEY_COLUMNS),
            (self._left, KEY_LEFT),
            (self._right, KEY_RIGHT),
        ):
            saved = settings.value(key)
            if not (isinstance(saved, QByteArray) and splitter.restoreState(saved)):
                self._to_size.append(splitter)
            splitter.splitterMoved.connect(self._remember)

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802 (Qt name)
        """At the first show, the splitters with nothing saved take their default proportions of
        the room they really have (before the show the sizes are not known)."""
        super().showEvent(event)
        to_size, self._to_size = self._to_size, []
        for splitter in to_size:
            room = sum(splitter.sizes())
            weights = self._proportions[splitter]
            splitter.setSizes([room * w // sum(weights) for w in weights])

    @property
    def columns(self) -> QSplitter:
        """The horizontal splitter between the two columns."""
        return self

    @property
    def left_column(self) -> QSplitter:
        """Overview above, Calendar below."""
        return self._left

    @property
    def right_column(self) -> QSplitter:
        """Activities above, Map below."""
        return self._right

    def pane(self, which: Pane) -> QWidget:
        """The widget placed in a pane."""
        return self._panes[which]

    def _column(self, name: str, top: QWidget, bottom: QWidget) -> QSplitter:
        column = QSplitter(Qt.Orientation.Vertical, self)
        column.setObjectName(name)
        self._prepare(column, (top, bottom))
        return column

    @staticmethod
    def _prepare(splitter: QSplitter, children: tuple[QWidget, QWidget]) -> None:
        """Place two children, none collapsible, equal at first with visible, wide handles."""
        for child in children:
            splitter.addWidget(child)
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(6)
        splitter.setOpaqueResize(False)  # the charts and the map redraw at the release
        for index, weight in enumerate(_HALF):
            splitter.setStretchFactor(index, weight)
        splitter.setSizes(list(_HALF))  # proportions, scaled to the room at the first show

    def _remember(self) -> None:
        for splitter, key in (
            (self, KEY_COLUMNS),
            (self._left, KEY_LEFT),
            (self._right, KEY_RIGHT),
        ):
            self._settings.setValue(key, splitter.saveState())
