# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""The strip over the calendar that says a goal is out of reach.

Humble view: whether there is an alert, its count and its command come ready from
`plan_presentation` (`PlanAlert`), the words from `plan_texts`. The strip is part of the calendar
page, on top, so it stays with the days where the goals are marked and does not depend on how the
panes are sized. It is hidden while there is nothing to say, so it goes away by itself when the
warnings do; it never opens a dialog. The meaning is in the icon and in the word "Warning", the
amber of the left border only underlines it.
"""

from __future__ import annotations

from PySide6.QtCore import QCoreApplication, Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QStyle, QWidget

from ponos_coach.ui.plan_presentation import NoticeAction, PlanAlert
from ponos_coach.ui.plan_texts import notice_prefix, plan_alert_text
from ponos_coach.ui.tokens import SPACE_1, SPACE_2, SPACE_3, Theme, warning_color_for

_BORDER_WIDTH = 4
_ICON_PX = 16


def _action_label(action: NoticeAction) -> str:
    if action is NoticeAction.TRAINING_SETTINGS:
        return QCoreApplication.translate("PlanAlertStrip", "Training settings...")
    return QCoreApplication.translate("PlanAlertStrip", "Show the goals")


class PlanAlertStrip(QFrame):
    """A warning bar: icon, "Warning", what is wrong, and a button with the verb that helps."""

    action_requested = Signal(object)  # NoticeAction

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("planAlertStrip")
        self._action = NoticeAction.NONE
        self._icon = QLabel(self)
        self._icon.setObjectName("planAlertIcon")
        self._icon.setAccessibleName(QCoreApplication.translate("PlanAlertStrip", "Warning"))
        icon = self.style().standardIcon(QStyle.StandardPixmap.SP_MessageBoxWarning)
        self._icon.setPixmap(icon.pixmap(_ICON_PX, _ICON_PX))
        self._prefix = QLabel(notice_prefix(no_plan=False), self)
        self._prefix.setObjectName("planAlertPrefix")
        font = self._prefix.font()
        font.setBold(True)
        self._prefix.setFont(font)
        self._text = QLabel(self)
        self._text.setObjectName("planAlertText")
        self._text.setTextFormat(Qt.TextFormat.PlainText)
        self._text.setWordWrap(True)
        self._button = QPushButton(self)
        self._button.setObjectName("planAlertButton")
        self._button.clicked.connect(
            lambda _checked=False: self.action_requested.emit(self._action)
        )
        row = QHBoxLayout(self)
        row.setContentsMargins(SPACE_3, SPACE_1, SPACE_1, SPACE_1)
        row.setSpacing(SPACE_2)
        row.addWidget(self._icon, alignment=Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(self._prefix, alignment=Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(self._text, stretch=1)
        row.addWidget(self._button, alignment=Qt.AlignmentFlag.AlignVCenter)
        self.hide()

    # --- State for the tests -----------------------------------------------------------------

    @property
    def text(self) -> str:
        """The sentence shown (empty while the strip is hidden)."""
        return "" if self.isHidden() else self._text.text()

    @property
    def button(self) -> QPushButton:
        """The command button."""
        return self._button

    # --- Showing ----------------------------------------------------------------------------

    def show_alert(self, alert: PlanAlert | None, theme: Theme) -> None:
        """Show the alert, or hide the strip when there is none."""
        if alert is None:
            self._action = NoticeAction.NONE
            self.hide()
            return
        self._action = alert.action
        self._text.setText(plan_alert_text(alert))
        self._button.setText(_action_label(alert.action))
        self.setStyleSheet(
            f"QFrame#planAlertStrip {{ border-left: {_BORDER_WIDTH}px solid "
            f"{warning_color_for(theme)}; }}"
        )
        self.show()
