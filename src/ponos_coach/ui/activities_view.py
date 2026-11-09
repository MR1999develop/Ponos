# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""The Activities section: the table of the activities on the left, the map on the right.

A horizontal splitter the user can drag; where it was left is remembered between sessions. The two
halves are independent views of the same activities (the shared filter and the shared selection
live in `MapUi`), so this widget only places them.
"""

from __future__ import annotations

from PySide6.QtCore import QByteArray, QSettings, Qt
from PySide6.QtWidgets import QSplitter, QWidget

KEY_SPLITTER = "activities/splitter"
_TABLE_WEIGHT = 2
_MAP_WEIGHT = 3


class ActivitiesMapView(QSplitter):
    """Table and map side by side, with the position of the divider remembered."""

    def __init__(
        self,
        table: QWidget,
        map_page: QWidget,
        settings: QSettings,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(Qt.Orientation.Horizontal, parent)
        self._settings = settings
        self.setObjectName("activitiesMapView")
        self.setChildrenCollapsible(False)
        self.addWidget(table)
        self.addWidget(map_page)
        self.setStretchFactor(0, _TABLE_WEIGHT)
        self.setStretchFactor(1, _MAP_WEIGHT)
        saved = settings.value(KEY_SPLITTER)
        if isinstance(saved, QByteArray):
            self.restoreState(saved)
        self.splitterMoved.connect(self._remember)

    def _remember(self) -> None:
        self._settings.setValue(KEY_SPLITTER, self.saveState())
