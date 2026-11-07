# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Birth date picker: domain limits and no invented date.

The control's minimum is one day before the first allowed day and means "not yet chosen"
(special text): the user sees a prompt, not a fake date. The maximum comes from the domain
(`latest_birth_date`). When the calendar opens on an unchosen date, it starts a few years
ago rather than at the special value (December 1899).
"""

from __future__ import annotations

from datetime import date, timedelta

from PySide6.QtCore import QDate, QEvent, QLocale, QObject, Signal
from PySide6.QtWidgets import QDateEdit, QWidget

from ponos_coach.profiles.domain import MIN_BIRTH_DATE
from ponos_coach.ui.body_input import latest_birth_date

NOT_SET_DATE = QDate(MIN_BIRTH_DATE.year - 1, 12, 31)  # the special value: "not chosen"
_CALENDAR_START_YEARS = 30  # years ago of the calendar page when there is no date yet
_DAYS_PER_YEAR = 365


def to_qdate(value: date) -> QDate:
    """The same date as a `QDate`."""
    return QDate(value.year, value.month, value.day)


class _CalendarStartPage(QObject):
    """When the calendar appears and there is no date yet, choose its page."""

    def __init__(self, edit: QDateEdit, page: QDate) -> None:
        super().__init__(edit)
        self._edit = edit
        self._page = page

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802 - Qt
        """Set the calendar page when it appears and the date is not chosen."""
        if event.type() == QEvent.Type.Show and self._edit.date() == NOT_SET_DATE:
            self._edit.calendarWidget().setCurrentPage(self._page.year(), self._page.month())
        return super().eventFilter(watched, event)


class BirthDateEdit(QDateEdit):
    """Birth date, empty until the user chooses it. `selection_changed` carries the date
    chosen, or `None`."""

    selection_changed = Signal(object)  # date | None

    def __init__(self, today: date, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("profileBirthDate")
        self.setAccessibleName(self.tr("Date of birth"))
        self.setCalendarPopup(True)
        self.setDisplayFormat(QLocale().dateFormat(QLocale.FormatType.ShortFormat))
        self.setMinimumDate(NOT_SET_DATE)
        self.setMaximumDate(to_qdate(latest_birth_date(today)))
        self.setSpecialValueText(self.tr("Select the date"))
        self.setDate(NOT_SET_DATE)
        start = today - timedelta(days=_DAYS_PER_YEAR * _CALENDAR_START_YEARS)
        self.calendarWidget().installEventFilter(_CalendarStartPage(self, to_qdate(start)))
        self.dateChanged.connect(self._on_date_changed)

    def selected_date(self) -> date | None:
        """The chosen date, or `None` if not yet."""
        value = self.date()
        return None if value == NOT_SET_DATE else date(value.year(), value.month(), value.day())

    def select(self, value: date | None) -> None:
        """Show a date (or "not chosen") without the user having touched it."""
        self.setDate(NOT_SET_DATE if value is None else to_qdate(value))

    def _on_date_changed(self, _value: QDate) -> None:
        self.selection_changed.emit(self.selected_date())
