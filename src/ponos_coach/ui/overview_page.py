# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Pagina "Panoramica": vista umile che mostra lo stato deciso da `overview_state`.

Quando il calcolo è pronto mostra tre grafici uno sotto l'altro: PMC, carico settimanale per
sport e tempo nelle zone. Le tre figure (e i loro JSON) si costruiscono insieme fuori dal thread
della UI.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from zoneinfo import ZoneInfo

from PySide6.QtCore import QLocale, Qt, QTime, Signal
from PySide6.QtWidgets import (
    QLabel,
    QProgressBar,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ponos_coach.activities.domain import Sport
from ponos_coach.load.domain import PmcSeries
from ponos_coach.load.use_cases import ProfileActivity
from ponos_coach.ui.activity_text import sport_text
from ponos_coach.ui.chart_payload import ChartChrome, PreparedChart
from ponos_coach.ui.chart_texts import chart_chrome
from ponos_coach.ui.chart_view import ChartEngine, ChartView
from ponos_coach.ui.errors import ErrorMessage
from ponos_coach.ui.load_figures import (
    WeeklyLoadLabels,
    WeeklyZonesLabels,
    prepare_weekly_load_chart,
    prepare_weekly_zones_chart,
)
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
from ponos_coach.ui.pmc_figure import ChartActivity, PmcChartLabels, prepare_chart
from ponos_coach.ui.state_page import StatePage
from ponos_coach.ui.tokens import (
    SPACE_2,
    SPACE_4,
    SPACE_6,
    STATE_PAGE_MAX_WIDTH,
    Theme,
    tokens_for,
)

type ChartScheduler = Callable[
    [Callable[[], object], Callable[[object], None], Callable[[ErrorMessage], None]], bool
]
"""Esegue un lavoro fuori dal thread della UI e ne consegna l'esito nel thread della UI."""


# Altezze relative dei tre grafici: il PMC ha due pannelli, gli altri uno.
_CHART_STRETCH = (4, 3, 3)


@dataclass(frozen=True, slots=True)
class OverviewCharts:
    """I tre grafici della panoramica, pronti da mostrare."""

    pmc: PreparedChart
    weekly_load: PreparedChart
    weekly_zones: PreparedChart


def last_day_of(pmc: PmcSeries) -> date:
    """L'ultimo giorno della serie PMC (di norma oggi); il primo se è vuota."""
    return pmc.first_day + timedelta(days=max(len(pmc) - 1, 0))


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
            self.tr("Activities calculated: {done} of %n", "", total).format(done=done)
        )
        self._bar.setRange(0, total)
        self._bar.setValue(done)


class OverviewPage(QWidget):
    """Stack delle pagine della panoramica."""

    action_requested = Signal(object)  # PrimaryAction
    cancel_requested = Signal()

    def __init__(
        self,
        engine: ChartEngine,
        schedule_chart: ChartScheduler,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._schedule_chart = schedule_chart
        self._chart_request = 0  # un esito di una richiesta superata si ignora
        self._page_action: PrimaryAction | None = None
        self._state_page = StatePage(self)
        self._state_page.action_requested.connect(self._on_state_action)
        self._loading_page = LoadingPage(self)
        self._loading_page.cancel_requested.connect(self.cancel_requested)
        self._chart = ChartView(engine, self.tr("Fitness and fatigue chart"), self)
        self._load_chart = ChartView(engine, self.tr("Weekly load chart"), self)
        self._zones_chart = ChartView(engine, self.tr("Time in zones chart"), self)
        self._charts = QSplitter(Qt.Orientation.Vertical, self)
        self._charts.setChildrenCollapsible(False)
        for index, view in enumerate((self._chart, self._load_chart, self._zones_chart)):
            self._charts.addWidget(view)
            self._charts.setStretchFactor(index, _CHART_STRETCH[index])
        self._stack = QStackedWidget(self)
        for widget in (self._state_page, self._loading_page, self._charts):
            self._stack.addWidget(widget)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._stack)

    @property
    def chart(self) -> ChartView:
        """Vista del grafico PMC."""
        return self._chart

    @property
    def load_chart(self) -> ChartView:
        """Vista del carico settimanale per sport."""
        return self._load_chart

    @property
    def zones_chart(self) -> ChartView:
        """Vista del tempo nelle zone di FC."""
        return self._zones_chart

    @property
    def charts_widget(self) -> QWidget:
        """Il pannello con i tre grafici (la pagina mostrata a calcolo pronto)."""
        return self._charts

    def start_engines(self) -> None:
        """Avvia le pagine web dei tre grafici (dopo il primo `show()` della finestra)."""
        for view in self._views():
            view.start_engine()

    def _views(self) -> tuple[ChartView, ChartView, ChartView]:
        return (self._chart, self._load_chart, self._zones_chart)

    @property
    def state_page(self) -> StatePage:
        """Pagina di stato (nessun profilo, vuoto, annullato, errore)."""
        return self._state_page

    def current_widget(self) -> QWidget | None:
        """Widget attualmente visibile nello stack."""
        return self._stack.currentWidget()

    def apply_state(self, state: OverviewState, theme: Theme, zone: ZoneInfo) -> None:
        """Mostra la pagina decisa da `page_of` per lo stato; `zone` è il fuso del profilo.

        La figura del grafico si costruisce fuori dal thread della UI: finché non è pronta resta
        la pagina di prima (di solito l'avanzamento).
        """
        self._chart_request += 1
        page = page_of(state)
        self._page_action = primary_action_of(page)
        if page is Page.LOADING and isinstance(state, Loading):
            self._loading_page.show_progress(state.done, state.total)
            self._stack.setCurrentWidget(self._loading_page)
        elif page is Page.CHART and isinstance(state, Ready):
            self._request_chart(state, theme, zone)
        else:
            self._show_state_page(state)
            self._stack.setCurrentWidget(self._state_page)

    def _request_chart(self, state: Ready, theme: Theme, zone: ZoneInfo) -> None:
        tokens = tokens_for(theme)
        chrome = self._chrome()
        labels = self._chart_labels(chrome)
        load_labels = self._load_labels(chrome)
        zones_labels = self._zones_labels(chrome)
        overview = state.overview
        pmc = overview.pmc
        activities = [chart_activity_of(item, zone) for item in overview.activities]
        request = self._chart_request

        def build() -> object:
            through = last_day_of(pmc)
            return OverviewCharts(
                prepare_chart(pmc, activities, labels, tokens),
                prepare_weekly_load_chart(overview.weekly_load(through), load_labels, tokens),
                prepare_weekly_zones_chart(overview.weekly_zones(through), zones_labels, tokens),
            )

        def show(prepared: object) -> None:
            if request == self._chart_request and isinstance(prepared, OverviewCharts):
                self._chart.show_prepared(prepared.pmc)
                self._load_chart.show_prepared(prepared.weekly_load)
                self._zones_chart.show_prepared(prepared.weekly_zones)
                self._stack.setCurrentWidget(self._charts)

        def failed(message: ErrorMessage) -> None:
            if request == self._chart_request:
                self._page_action = primary_action_of(Page.ERROR)
                self._show_state_page(Failed(message))
                self._stack.setCurrentWidget(self._state_page)

        if not self._schedule_chart(build, show, failed):
            self._chart_request += 1  # si sta chiudendo: nessun disegno

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

    def _chrome(self) -> ChartChrome:
        return chart_chrome()

    def _chart_labels(self, chrome: ChartChrome) -> PmcChartLabels:
        return PmcChartLabels(
            x_axis=self.tr("Date"),
            load_axis=self.tr("Load"),
            form_axis=self.tr("Form"),
            ctl=self.tr("Fitness (CTL)"),
            atl=self.tr("Fatigue (ATL)"),
            tsb=self.tr("Form (TSB)"),
            daily_load=self.tr("Daily load"),
            chrome=chrome,
            load_unit=self.tr("hrTSS per day"),
        )

    def _load_labels(self, chrome: ChartChrome) -> WeeklyLoadLabels:
        return WeeklyLoadLabels(
            x_axis=self.tr("Week (starting Monday)"),
            y_axis=self.tr("Load"),
            sports={sport: sport_text(sport) for sport in Sport},
            chrome=chrome,
            unit=self.tr("hrTSS per week"),
        )

    def _zones_labels(self, chrome: ChartChrome) -> WeeklyZonesLabels:
        return WeeklyZonesLabels(
            x_axis=self.tr("Week (starting Monday)"),
            y_axis=self.tr("Time in zone"),
            legend_title=self.tr("Heart-rate zone (% of threshold)"),
            chrome=chrome,
            unit=self.tr("hours per week"),
        )
