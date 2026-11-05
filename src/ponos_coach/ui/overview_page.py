# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Pagina "Panoramica": vista umile che mostra lo stato deciso da `overview_state`."""

from __future__ import annotations

from zoneinfo import ZoneInfo

from PySide6.QtCore import QLocale, Qt, QTime, Signal
from PySide6.QtWidgets import (
    QLabel,
    QProgressBar,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ponos_coach.load.use_cases import ProfileActivity
from ponos_coach.ui.activity_text import sport_text
from ponos_coach.ui.chart_view import ExportPathChooser, PmcChartView
from ponos_coach.ui.overview_state import (
    Cancelled,
    Failed,
    Loading,
    OverviewState,
    Page,
    PrimaryAction,
    Ready,
    page_of,
    primary_action_of,
)
from ponos_coach.ui.pmc_figure import ChartActivity, PmcChartLabels, build_pmc_figure
from ponos_coach.ui.state_page import StatePage
from ponos_coach.ui.tokens import (
    SPACE_2,
    SPACE_4,
    SPACE_6,
    STATE_PAGE_MAX_WIDTH,
    Theme,
    tokens_for,
)


def chart_activity_of(activity: ProfileActivity, zone: ZoneInfo) -> ChartActivity:
    """Attività per il grafico: sport e ora locale come nome nel suggerimento di una barra."""
    local = activity.start.astimezone(zone)
    time_text = QLocale().toString(QTime(local.hour, local.minute), QLocale.FormatType.ShortFormat)
    return ChartActivity(
        activity.local_day, activity.load.load, f"{sport_text(activity.sport)} {time_text}"
    )


class LoadingPage(QWidget):
    """Avanzamento del calcolo, con Annulla."""

    cancel_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._label = QLabel(self)
        self._label.setTextFormat(Qt.TextFormat.PlainText)
        self._bar = QProgressBar(self)
        self._bar.setTextVisible(False)
        self._bar.setMaximumWidth(STATE_PAGE_MAX_WIDTH)
        self._cancel = QPushButton(self.tr("Stop"), self)
        self._cancel.clicked.connect(self.cancel_requested)
        column = QWidget(self)
        column.setMaximumWidth(STATE_PAGE_MAX_WIDTH)
        inner = QVBoxLayout(column)
        inner.setSpacing(SPACE_2)
        inner.setContentsMargins(SPACE_6, SPACE_6, SPACE_6, SPACE_6)
        inner.addWidget(self._label)
        inner.addWidget(self._bar)
        inner.addSpacing(SPACE_4)
        inner.addWidget(self._cancel, alignment=Qt.AlignmentFlag.AlignLeft)
        outer = QVBoxLayout(self)
        outer.addStretch(1)
        outer.addWidget(column, alignment=Qt.AlignmentFlag.AlignHCenter)
        outer.addStretch(2)

    def show_progress(self, done: int, total: int) -> None:
        """Aggiorna testo e barra; con totale ignoto la barra è indeterminata."""
        if total <= 0:
            self._label.setText(self.tr("Getting ready..."))
            self._bar.setRange(0, 0)
            return
        # {done}: activities already calculated; %n: number of activities in the profile.
        self._label.setText(
            self.tr("Calculated {done} of %n activity(ies)", "", total).format(done=done)
        )
        self._bar.setRange(0, total)
        self._bar.setValue(done)


class OverviewPage(QWidget):
    """Stack delle pagine della panoramica."""

    action_requested = Signal(object)  # PrimaryAction
    cancel_requested = Signal()

    def __init__(
        self, choose_export_path: ExportPathChooser, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self._page_action: PrimaryAction | None = None
        self._state_page = StatePage(self)
        self._state_page.action_requested.connect(self._on_state_action)
        self._loading_page = LoadingPage(self)
        self._loading_page.cancel_requested.connect(self.cancel_requested)
        self._chart = PmcChartView(choose_export_path, self)
        self._stack = QStackedWidget(self)
        for widget in (self._state_page, self._loading_page, self._chart):
            self._stack.addWidget(widget)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._stack)

    @property
    def chart(self) -> PmcChartView:
        """Vista del grafico."""
        return self._chart

    @property
    def state_page(self) -> StatePage:
        """Pagina di stato (nessun profilo, vuoto, annullato, errore)."""
        return self._state_page

    def current_widget(self) -> QWidget | None:
        """Widget attualmente visibile nello stack."""
        return self._stack.currentWidget()

    def apply_state(self, state: OverviewState, theme: Theme, zone: ZoneInfo) -> None:
        """Mostra la pagina decisa da `page_of` per lo stato; `zone` è il fuso del profilo."""
        page = page_of(state)
        self._page_action = primary_action_of(page)
        if page is Page.LOADING and isinstance(state, Loading):
            self._loading_page.show_progress(state.done, state.total)
            self._stack.setCurrentWidget(self._loading_page)
        elif page is Page.CHART and isinstance(state, Ready):
            tokens = tokens_for(theme)
            activities = [chart_activity_of(item, zone) for item in state.overview.activities]
            figure = build_pmc_figure(state.overview.pmc, activities, self._chart_labels(), tokens)
            self._chart.show_figure(figure, self._chart_labels(), tokens.background)
            self._stack.setCurrentWidget(self._chart)
        else:
            self._show_state_page(state)
            self._stack.setCurrentWidget(self._state_page)

    def _on_state_action(self) -> None:
        if self._page_action is not None:
            self.action_requested.emit(self._page_action)

    def _show_state_page(self, state: OverviewState) -> None:
        match state:
            case Failed(message):
                self._state_page.set_content(
                    message.title,
                    f"{message.what}\n\n{message.remedy}",
                    self.tr("Calculate again"),
                )
            case Cancelled():
                self._state_page.set_content(
                    self.tr("Calculation stopped"),
                    self.tr("The overview was not calculated."),
                    self.tr("Calculate again"),
                )
            case Ready():
                self._state_page.set_content(
                    self.tr("No activities yet"),
                    self.tr("To get started, import FIT, GPX or ZIP files."),
                    self.tr("Import files..."),
                )
            case _:
                self._state_page.set_content(
                    self.tr("Welcome to Ponos Coach"),
                    self.tr("To get started, create a profile for your training data."),
                    self.tr("New profile..."),
                )

    def _chart_labels(self) -> PmcChartLabels:
        return PmcChartLabels(
            x_axis=self.tr("Date"),
            load_axis=self.tr("Load"),
            form_axis=self.tr("Form"),
            ctl=self.tr("Fitness (CTL)"),
            atl=self.tr("Fatigue (ATL)"),
            tsb=self.tr("Form (TSB)"),
            daily_load=self.tr("Daily load"),
            export_png=self.tr("Save chart as PNG"),
            export_svg=self.tr("Save chart as SVG"),
            modebar={
                "Zoom": self.tr("Zoom"),
                "Pan": self.tr("Pan"),
                "Zoom in": self.tr("Zoom in"),
                "Zoom out": self.tr("Zoom out"),
                "Autoscale": self.tr("Autoscale"),
                "Reset axes": self.tr("Reset axes"),
                "Toggle Spike Lines": self.tr("Toggle spike lines"),
                "Show closest data on hover": self.tr("Show closest data on hover"),
                "Compare data on hover": self.tr("Compare data on hover"),
            },
        )
