# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Selettore della data di nascita: limiti del dominio e nessuna data inventata.

Il minimo del controllo è un giorno prima del primo giorno ammesso e vale "non ancora scelta"
(testo speciale): l'utente vede un invito, non una data finta. Il massimo viene dal dominio
(`latest_birth_date`). Quando il calendario si apre su una data non scelta, parte da qualche anno
fa invece che dal valore speciale (dicembre 1899).
"""

from __future__ import annotations

from datetime import date, timedelta

from PySide6.QtCore import QDate, QEvent, QLocale, QObject, Signal
from PySide6.QtWidgets import QDateEdit, QWidget

from ponos_coach.profiles.domain import MIN_BIRTH_DATE
from ponos_coach.ui.body_input import latest_birth_date

NOT_SET_DATE = QDate(MIN_BIRTH_DATE.year - 1, 12, 31)  # il valore speciale: "non scelta"
_CALENDAR_START_YEARS = 30  # anni fa della pagina del calendario quando la data non c'è ancora
_DAYS_PER_YEAR = 365


def to_qdate(value: date) -> QDate:
    """La stessa data come `QDate`."""
    return QDate(value.year, value.month, value.day)


class _CalendarStartPage(QObject):
    """Alla comparsa del calendario, se la data non c'è ancora, ne sceglie la pagina."""

    def __init__(self, edit: QDateEdit, page: QDate) -> None:
        super().__init__(edit)
        self._edit = edit
        self._page = page

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802 - Qt
        """Imposta la pagina del calendario quando compare e la data non è scelta."""
        if event.type() == QEvent.Type.Show and self._edit.date() == NOT_SET_DATE:
            self._edit.calendarWidget().setCurrentPage(self._page.year(), self._page.month())
        return super().eventFilter(watched, event)


class BirthDateEdit(QDateEdit):
    """Data di nascita, vuota finché l'utente non la sceglie. `selection_changed` porta la data
    scelta o `None`."""

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
        """La data scelta, o `None` se non ancora."""
        value = self.date()
        return None if value == NOT_SET_DATE else date(value.year(), value.month(), value.day())

    def select(self, value: date | None) -> None:
        """Mostra una data (o "non scelta") senza che l'utente l'abbia toccata."""
        self.setDate(NOT_SET_DATE if value is None else to_qdate(value))

    def _on_date_changed(self, _value: QDate) -> None:
        self.selection_changed.emit(self.selected_date())
