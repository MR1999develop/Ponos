# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Vista del grafico PMC: un `QWebEngineView` isolato che mostra la pagina locale di Plotly.

Isolamento: profilo senza archivio su disco, pagina e script serviti solo dallo schema
dell'app, intercettore che blocca ogni altra richiesta, nessun `QWebChannel`, nessun menu
contestuale, nessuna finestra aggiuntiva. I dati entrano come JSON (`render_script`).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QBuffer, QIODevice, Qt, QUrl, Signal
from PySide6.QtGui import QCloseEvent, QColor
from PySide6.QtWebEngineCore import (
    QWebEngineDownloadRequest,
    QWebEnginePage,
    QWebEngineProfile,
    QWebEngineSettings,
    QWebEngineUrlRequestInfo,
    QWebEngineUrlRequestInterceptor,
    QWebEngineUrlRequestJob,
    QWebEngineUrlSchemeHandler,
)
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QVBoxLayout, QWidget

from ponos_coach.ui.pmc_figure import PmcChartLabels, PmcFigure, PreparedChart, render_script
from ponos_coach.ui.web_policy import APP_PAGE_URL, APP_SCHEME, is_request_allowed
from ponos_coach.ui.web_resources import resource_for_path

_LOG = logging.getLogger(__name__)

type ExportPathChooser = Callable[[str], Path | None]


class AppOnlyInterceptor(QWebEngineUrlRequestInterceptor):
    """Blocca ogni richiesta che non sia dello schema dell'app (decide `is_request_allowed`)."""

    def interceptRequest(self, info: QWebEngineUrlRequestInfo) -> None:  # noqa: N802 - Qt
        """Chiamata dal motore web, anche fuori dal thread della UI."""
        url = info.requestUrl()
        if not is_request_allowed(url):
            _LOG.warning("Blocked web request with scheme %r", url.scheme())
            info.block(True)


class AppSchemeHandler(QWebEngineUrlSchemeHandler):
    """Serve le risorse dell'elenco chiuso di `web_resources`; tutto il resto e 404."""

    def requestStarted(self, job: QWebEngineUrlRequestJob) -> None:  # noqa: N802 - Qt
        """Risponde a una richiesta dello schema dell'app."""
        resource = None
        if job.requestMethod() == b"GET":
            resource = resource_for_path(job.requestUrl().path())
        if resource is None:
            job.fail(QWebEngineUrlRequestJob.Error.UrlNotFound)
            return
        buffer = QBuffer(job)  # vive quanto la richiesta
        buffer.setData(resource.content)
        buffer.open(QIODevice.OpenModeFlag.ReadOnly)
        job.reply(resource.mime_type.encode("ascii"), buffer)


class _AppPage(QWebEnginePage):
    """Pagina che non naviga mai fuori dallo schema dell'app."""

    def acceptNavigationRequest(  # noqa: N802 - Qt
        self, url: QUrl | str, nav_type: QWebEnginePage.NavigationType, is_main_frame: bool
    ) -> bool:
        """Solo `ponos://app/...`."""
        del nav_type, is_main_frame
        return is_request_allowed(QUrl(url))


def _harden(settings: QWebEngineSettings) -> None:
    attribute = QWebEngineSettings.WebAttribute
    for name in (
        attribute.LocalContentCanAccessRemoteUrls,
        attribute.LocalContentCanAccessFileUrls,
        attribute.AllowRunningInsecureContent,
        attribute.JavascriptCanOpenWindows,
        attribute.JavascriptCanAccessClipboard,
        attribute.JavascriptCanPaste,
        attribute.PluginsEnabled,
        attribute.WebGLEnabled,
        attribute.ScreenCaptureEnabled,
        attribute.DnsPrefetchEnabled,
        attribute.AutoLoadIconsForPage,
        attribute.ErrorPageEnabled,
        attribute.WebRTCPublicInterfacesOnly,
        attribute.PdfViewerEnabled,
    ):
        settings.setAttribute(name, False)
    settings.setAttribute(attribute.JavascriptEnabled, True)


@dataclass(frozen=True, slots=True)
class _Engine:
    """Gli oggetti del motore web, creati insieme (i riferimenti li tengono vivi)."""

    profile: QWebEngineProfile
    interceptor: AppOnlyInterceptor
    scheme_handler: AppSchemeHandler
    page: _AppPage
    view: QWebEngineView


class PmcChartView(QWidget):
    """Mostra il grafico PMC. `drawn` segnala che Plotly ha finito di disegnare.

    Il motore web (profilo, pagina e vista) non si crea nel costruttore: costa circa 180 ms nel
    thread della UI, che Chromium non permette di spostare. Lo avvia `start_engine`, che la
    finestra chiama subito dopo il primo `show()`; chi disegna prima lo avvia da sé.
    """

    drawn = Signal()
    load_failed = Signal()

    def __init__(
        self, choose_export_path: ExportPathChooser, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self._choose_export_path = choose_export_path
        self._current_figure: PmcFigure | None = None
        self._page_loaded = False
        self._pending_script: str | None = None
        self._disposed = False
        self._engine: _Engine | None = None
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)

    @property
    def engine_started(self) -> bool:
        """Vero se il motore web è stato creato."""
        return self._engine is not None

    def start_engine(self) -> None:
        """Crea il motore web e carica la pagina; non fa nulla se già fatto o se si è chiusi."""
        if self._engine is not None or self._disposed:
            return
        profile = QWebEngineProfile(self)  # senza nome: nessun dato su disco
        interceptor = AppOnlyInterceptor(self)
        scheme_handler = AppSchemeHandler(self)
        profile.setUrlRequestInterceptor(interceptor)
        profile.installUrlSchemeHandler(APP_SCHEME.encode("ascii"), scheme_handler)
        profile.downloadRequested.connect(self._on_download_requested)
        _harden(profile.settings())
        page = _AppPage(profile, self)
        page.loadFinished.connect(self._on_load_finished)
        page.titleChanged.connect(self._on_title_changed)
        view = QWebEngineView(self)
        view.setPage(page)
        view.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        view.setAccessibleName(self.tr("Fitness and fatigue chart"))
        self._layout.addWidget(view)
        self._engine = _Engine(profile, interceptor, scheme_handler, page, view)
        view.setUrl(QUrl(APP_PAGE_URL))

    @property
    def current_figure(self) -> PmcFigure | None:
        """Ultima figura mostrata (o in attesa di essere mostrata)."""
        return self._current_figure

    def show_prepared(self, prepared: PreparedChart) -> None:
        """Disegna un grafico già preparato (appena la pagina è pronta)."""
        self._current_figure = prepared.figure
        self.start_engine()
        if self._engine is None:  # chiusa: niente da disegnare
            return
        self._engine.page.setBackgroundColor(QColor(prepared.background))
        if self._page_loaded:
            self._engine.page.runJavaScript(prepared.script)
        else:
            self._pending_script = prepared.script

    def show_figure(self, figure: PmcFigure, labels: PmcChartLabels, background: str) -> None:
        """Prepara e disegna una figura (nel thread chiamante: per i test e gli usi semplici)."""
        self.show_prepared(PreparedChart(figure, render_script(figure, labels), background))

    @property
    def page(self) -> QWebEnginePage:
        """La pagina web (per diagnostica e test di integrazione); avvia il motore se serve."""
        self.start_engine()
        if self._engine is None:
            raise RuntimeError("the chart view was disposed")
        return self._engine.page

    def dispose(self) -> None:
        """Distrugge la pagina prima del profilo (ordine richiesto dal motore web); idempotente."""
        if self._disposed:
            return
        self._disposed = True
        if self._engine is None:
            return
        self._engine.view.setPage(None)  # type: ignore[arg-type]  # stub senza Optional
        self._engine.page.deleteLater()  # prima del profilo: il motore la vuole già distrutta

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 - Qt
        """Chiudendo il widget si rilascia il motore web."""
        self.dispose()
        super().closeEvent(event)

    def _on_load_finished(self, ok: bool) -> None:
        if not ok:
            _LOG.error("Chart page failed to load")
            self.load_failed.emit()
            return
        self._page_loaded = True
        if self._pending_script is not None and self._engine is not None:
            script, self._pending_script = self._pending_script, None
            self._engine.page.runJavaScript(script)

    def _on_title_changed(self, title: str) -> None:
        if title.startswith("drawn-"):
            self.drawn.emit()

    def _on_download_requested(self, request: QWebEngineDownloadRequest) -> None:
        file_name = Path(request.suggestedFileName()).name
        chosen = self._choose_export_path(file_name)
        if chosen is None:
            request.cancel()
            return
        request.setDownloadDirectory(str(chosen.parent))
        request.setDownloadFileName(chosen.name)
        request.accept()
