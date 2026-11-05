# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Pagina "Panoramica": vista umile che mostra lo stato deciso da `overview_state`."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QLabel,
    QProgressBar,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ponos_coach.ui.chart_view import ExportPathChooser, PmcChartView
from ponos_coach.ui.overview_state import (
    Cancelled,
    Failed,
    Loading,
    OverviewState,
    Page,
    Ready,
    page_of,
)
from ponos_coach.ui.pmc_figure import PmcChartLabels, build_pmc_figure
from ponos_coach.ui.tokens import (
    SPACE_2,
    SPACE_4,
    SPACE_6,
    STATE_PAGE_MAX_WIDTH,
    TITLE_SCALE,
    Theme,
    tokens_for,
)


class StatePage(QWidget):
    """Pagina di stato: titolo, una riga di spiegazione e il bottone del primo passo."""

    action_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._title = QLabel(self)
        self._title.setTextFormat(Qt.TextFormat.PlainText)
        self._title.setWordWrap(True)
        font = self._title.font()
        font.setPointSizeF(font.pointSizeF() * TITLE_SCALE)
        font.setWeight(font.Weight.DemiBold)
        self._title.setFont(font)
        self._body = QLabel(self)
        self._body.setTextFormat(Qt.TextFormat.PlainText)
        self._body.setWordWrap(True)
        self._button = QPushButton(self)
        self._button.setDefault(True)
        self._button.clicked.connect(self.action_requested)
        column = QWidget(self)
        column.setMaximumWidth(STATE_PAGE_MAX_WIDTH)
        inner = QVBoxLayout(column)
        inner.setSpacing(SPACE_2)
        inner.setContentsMargins(SPACE_6, SPACE_6, SPACE_6, SPACE_6)
        inner.addWidget(self._title)
        inner.addWidget(self._body)
        inner.addSpacing(SPACE_4)
        inner.addWidget(self._button, alignment=Qt.AlignmentFlag.AlignLeft)
        outer = QVBoxLayout(self)
        outer.addStretch(1)
        outer.addWidget(column, alignment=Qt.AlignmentFlag.AlignHCenter)
        outer.addStretch(2)

    def set_content(self, title: str, body: str, button_text: str) -> None:
        """Imposta i testi (sempre testo semplice, mai HTML)."""
        self._title.setText(title)
        self._body.setText(body)
        self._button.setText(button_text)

    @property
    def button(self) -> QPushButton:
        """Il bottone del primo passo."""
        return self._button


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
        self._cancel = QPushButton(self.tr("Stop loading"), self)
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
        """Aggiorna testo e barra; con totale ignoto la barra e indeterminata."""
        if total <= 0:
            self._label.setText(self.tr("Looking for FIT files..."))
            self._bar.setRange(0, 0)
            return
        # {done}: file already read; %n: number of files in the folder.
        self._label.setText(self.tr("Read {done} of %n file(s)", "", total).format(done=done))
        self._bar.setRange(0, total)
        self._bar.setValue(done)


class OverviewPage(QWidget):
    """Stack delle pagine della panoramica."""

    open_folder_requested = Signal()
    cancel_requested = Signal()

    def __init__(
        self, choose_export_path: ExportPathChooser, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self._state_page = StatePage(self)
        self._state_page.action_requested.connect(self.open_folder_requested)
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
        """Pagina di stato (avvio, vuoto, annullato, errore)."""
        return self._state_page

    def current_widget(self) -> QWidget | None:
        """Widget attualmente visibile nello stack."""
        return self._stack.currentWidget()

    def apply_state(self, state: OverviewState, theme: Theme) -> None:
        """Mostra la pagina decisa da `page_of` per lo stato."""
        page = page_of(state)
        if page is Page.LOADING and isinstance(state, Loading):
            self._loading_page.show_progress(state.done, state.total)
            self._stack.setCurrentWidget(self._loading_page)
        elif page is Page.CHART and isinstance(state, Ready):
            tokens = tokens_for(theme)
            figure = build_pmc_figure(
                state.overview.pmc, state.overview.activities, self._chart_labels(), tokens
            )
            self._chart.show_figure(figure, self._chart_labels(), tokens.background)
            self._stack.setCurrentWidget(self._chart)
        else:
            self._show_state_page(state)
            self._stack.setCurrentWidget(self._state_page)

    def _show_state_page(self, state: OverviewState) -> None:
        open_text = self.tr("Open FIT folder...")
        match state:
            case Failed(_, message):
                self._state_page.set_content(
                    message.title, f"{message.what}\n\n{message.remedy}", open_text
                )
            case Cancelled(folder):
                self._state_page.set_content(
                    self.tr("Loading stopped"),
                    self.tr('Nothing was loaded from "{folder}".').format(folder=folder.name),
                    open_text,
                )
            case Ready(folder, _):
                self._state_page.set_content(
                    self.tr("No activities found"),
                    self.tr('The folder "{folder}" has no usable FIT activities.').format(
                        folder=folder.name
                    ),
                    self.tr("Open another folder..."),
                )
            case _:
                self._state_page.set_content(
                    self.tr("No FIT files loaded"),
                    self.tr("To get started, open a folder of FIT files."),
                    open_text,
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
