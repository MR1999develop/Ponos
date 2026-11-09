# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Page "Overview": humble view that shows the state decided by `overview_state`.

When the computation is ready it shows three charts one below the other: PMC, weekly load per
sport and time in zones. They share the dates: the wheel zooms and a left drag pans one chart,
and the other two follow. The three figures (and their JSON) are built together off the
UI thread.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from functools import partial
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
from ponos_coach.ui.chart_view import ChartEngine, ChartView, DateRange
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
from ponos_coach.ui.pmc_figure import ChartActivity, PmcChartLabels, PmcProjection, prepare_chart
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
"""Run a job off the UI thread and deliver its outcome on the UI thread."""


# Relative heights of the three charts: the PMC has two panels, the others one.
_CHART_STRETCH = (4, 3, 3)


@dataclass(frozen=True, slots=True)
class OverviewCharts:
    """The overview's three charts, ready to show."""

    pmc: PreparedChart
    weekly_load: PreparedChart
    weekly_zones: PreparedChart


def last_day_of(pmc: PmcSeries) -> date:
    """The last day of the PMC series (normally today); the first if it is empty."""
    return pmc.first_day + timedelta(days=max(len(pmc) - 1, 0))


def chart_activity_of(activity: ProfileActivity, zone: ZoneInfo) -> ChartActivity:
    """Activities for the chart: sport and local time as the name in a bar's hint."""
    local = activity.start.astimezone(zone)
    time_text = QLocale().toString(QTime(local.hour, local.minute), QLocale.FormatType.ShortFormat)
    return ChartActivity(
        activity.local_day, activity.load.load, f"{sport_text(activity.sport)} {time_text}"
    )


class LoadingPage(QWidget):
    """Progress of the computation, with Cancel."""

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
        """Update text and bar; with unknown total the bar is indeterminate."""
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
    """Stack of the overview pages."""

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
        self._chart_request = 0  # an outcome of a superseded request is ignored
        self._projection: PmcProjection | None = None  # the plan's part of the PMC, if any
        self._shown: tuple[Ready, Theme, ZoneInfo] | None = None  # what the charts show now
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
            view.x_range_changed.connect(partial(self._follow_dates, view))
        self._stack = QStackedWidget(self)
        for widget in (self._state_page, self._loading_page, self._charts):
            self._stack.addWidget(widget)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._stack)

    @property
    def chart(self) -> ChartView:
        """The PMC chart view."""
        return self._chart

    @property
    def load_chart(self) -> ChartView:
        """The weekly load per sport view."""
        return self._load_chart

    @property
    def zones_chart(self) -> ChartView:
        """The time in HR zones view."""
        return self._zones_chart

    @property
    def charts_widget(self) -> QWidget:
        """The panel with the three charts (the page shown when the computation is ready)."""
        return self._charts

    def start_engines(self) -> None:
        """Start the three charts' web pages (after the window's first `show()`)."""
        for view in self._views():
            view.start_engine()

    def _views(self) -> tuple[ChartView, ChartView, ChartView]:
        return (self._chart, self._load_chart, self._zones_chart)

    def _follow_dates(self, source: ChartView, shown: DateRange | None) -> None:
        """The user zoomed or panned one chart: the other two show the same dates."""
        for view in self._views():
            if view is not source:
                view.set_x_range(shown)

    @property
    def state_page(self) -> StatePage:
        """State page (no profile, empty, cancelled, error)."""
        return self._state_page

    def current_widget(self) -> QWidget | None:
        """Widget currently visible in the stack."""
        return self._stack.currentWidget()

    def apply_state(self, state: OverviewState, theme: Theme, zone: ZoneInfo) -> None:
        """Show the page decided by `page_of` for the state; `zone` is the profile's time zone.

        The chart figure is built off the UI thread: until it is ready the previous
        page stays (usually the progress).
        """
        self._chart_request += 1
        page = page_of(state)
        self._page_action = primary_action_of(page)
        if page is Page.LOADING and isinstance(state, Loading):
            self._shown = None
            self._loading_page.show_progress(state.done, state.total)
            self._stack.setCurrentWidget(self._loading_page)
        elif page is Page.CHART and isinstance(state, Ready):
            self._shown = (state, theme, zone)
            self._request_chart(state, theme, zone)
        else:
            self._shown = None
            self._show_state_page(state)
            self._stack.setCurrentWidget(self._state_page)

    def set_projection(self, projection: PmcProjection | None) -> None:
        """Show the plan's part of the PMC (dotted, with the races as markers); `None` takes it
        away. The charts are drawn again only if the projection is not the one already shown."""
        if projection == self._projection:
            return
        self._projection = projection
        if self._shown is not None:
            self._chart_request += 1
            self._request_chart(*self._shown)

    def _request_chart(self, state: Ready, theme: Theme, zone: ZoneInfo) -> None:
        projection = self._projection
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
                prepare_chart(pmc, activities, labels, tokens, projection),
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
            self._chart_request += 1  # closing: no drawing

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
                    self.tr("To get started, import FIT files."),
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
            ctl_plan=self.tr("Fitness (CTL), plan"),
            atl_plan=self.tr("Fatigue (ATL), plan"),
            tsb_plan=self.tr("Form (TSB), plan"),
            races=self.tr("Races"),
            # {priority}: A, B or C; {name}: the race, for example the distance.
            race_hint=self.tr("Race {priority}: {name}"),
            today=self.tr("Today"),
            # {share}: the day's fitness as a percentage of the user's highest fitness of the
            # last two years (a whole number).
            peak_share=self.tr("{share}% of your best of the last two years"),
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
