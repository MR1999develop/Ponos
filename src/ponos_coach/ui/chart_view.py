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

from ponos_coach.ui.pmc_figure import PmcChartLabels, PmcFigure, render_script
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


class PmcChartView(QWidget):
    """Mostra il grafico PMC. `drawn` segnala che Plotly ha finito di disegnare."""

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

        self._profile = QWebEngineProfile(self)  # senza nome: nessun dato su disco
        self._interceptor = AppOnlyInterceptor(self)
        self._scheme_handler = AppSchemeHandler(self)
        self._profile.setUrlRequestInterceptor(self._interceptor)
        self._profile.installUrlSchemeHandler(APP_SCHEME.encode("ascii"), self._scheme_handler)
        self._profile.downloadRequested.connect(self._on_download_requested)
        _harden(self._profile.settings())

        self._page = _AppPage(self._profile, self)
        self._page.loadFinished.connect(self._on_load_finished)
        self._page.titleChanged.connect(self._on_title_changed)
        self._view = QWebEngineView(self)
        self._view.setPage(self._page)
        self._view.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        self._view.setAccessibleName(self.tr("Fitness and fatigue chart"))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._view)
        self._view.setUrl(QUrl(APP_PAGE_URL))

    @property
    def current_figure(self) -> PmcFigure | None:
        """Ultima figura mostrata (o in attesa di essere mostrata)."""
        return self._current_figure

    def show_figure(self, figure: PmcFigure, labels: PmcChartLabels, background: str) -> None:
        """Disegna la figura (appena la pagina e pronta)."""
        self._current_figure = figure
        self._page.setBackgroundColor(QColor(background))
        script = render_script(figure, labels)
        if self._page_loaded:
            self._page.runJavaScript(script)
        else:
            self._pending_script = script

    @property
    def page(self) -> QWebEnginePage:
        """La pagina web (per diagnostica e test di integrazione)."""
        return self._page

    def dispose(self) -> None:
        """Distrugge la pagina prima del profilo (ordine richiesto dal motore web); idempotente."""
        if self._disposed:
            return
        self._disposed = True
        self._view.setPage(None)  # type: ignore[arg-type]  # stub senza Optional; Qt accetta null
        self._page.deleteLater()  # prima del profilo: il motore web vuole la pagina gia distrutta

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
        if self._pending_script is not None:
            script, self._pending_script = self._pending_script, None
            self._page.runJavaScript(script)

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
