# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Vista di un grafico: un `QWebEngineView` isolato che mostra la pagina locale di Plotly.

Isolamento: profilo senza archivio su disco, pagina e script serviti solo dallo schema
dell'app, intercettore che blocca ogni altra richiesta, nessun `QWebChannel`, nessun menu
contestuale, nessuna finestra aggiuntiva. I dati entrano come JSON (`render_script`).

Il profilo del motore web (`ChartEngine`) è uno solo per l'app e serve tutti i grafici: ogni
`ChartView` ha la sua pagina e si registra nel motore. Alla chiusura `ChartEngine.dispose`
distrugge in modo sincrono le pagine registrate e poi il profilo: l'ordine non dipende da quello
dei figli di Qt.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

import plotly.graph_objects as go
from PySide6.QtCore import QBuffer, QIODevice, QObject, Qt, QUrl, Signal
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
from shiboken6 import delete as delete_now
from shiboken6 import isValid

from ponos_coach.ui.chart_payload import PreparedChart
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


class ChartEngine(QObject):
    """Il profilo del motore web, condiviso da tutti i grafici dell'app.

    Non si crea nel costruttore: costa circa 180 ms nel thread della UI, che Chromium non permette
    di spostare. Lo avvia `start`, che la finestra chiama subito dopo il primo `show()`; chi
    disegna prima lo avvia da sé. Tiene gli oggetti del motore, perché i riferimenti li tengono
    vivi.

    Ordine di distruzione: il profilo non può sparire prima delle pagine. Le viste si registrano
    (`register`); `dispose`, chiamata alla chiusura della finestra e da `aboutToQuit`, distrugge
    subito le pagine delle viste ancora vive e poi il profilo. Una vista chiusa prima (un dialogo)
    ha già distrutto la sua pagina da sé.
    """

    def __init__(
        self, choose_export_path: ExportPathChooser, parent: QObject | None = None
    ) -> None:
        super().__init__(parent)
        self._choose_export_path = choose_export_path
        self._profile: QWebEngineProfile | None = None
        self._interceptor: AppOnlyInterceptor | None = None
        self._scheme_handler: AppSchemeHandler | None = None
        self._disposed = False
        self._views: list[ChartView] = []

    def register(self, view: ChartView) -> None:
        """Una vista che userà il profilo: `dispose` ne distruggerà la pagina prima del profilo."""
        self._views = [known for known in self._views if isValid(known)]
        self._views.append(view)

    @property
    def started(self) -> bool:
        """Vero se il profilo è stato creato (e non ancora distrutto)."""
        return self._profile is not None

    @property
    def disposed(self) -> bool:
        """Vero dopo `dispose`: nessuna pagina nuova."""
        return self._disposed

    def start(self) -> QWebEngineProfile | None:
        """Crea il profilo se serve e lo dà; `None` se il motore è stato rilasciato."""
        if self._disposed:
            return None
        if self._profile is None:
            profile = QWebEngineProfile(self)  # senza nome: nessun dato su disco
            self._interceptor = AppOnlyInterceptor(self)
            self._scheme_handler = AppSchemeHandler(self)
            profile.setUrlRequestInterceptor(self._interceptor)
            profile.installUrlSchemeHandler(APP_SCHEME.encode("ascii"), self._scheme_handler)
            profile.downloadRequested.connect(self._on_download_requested)
            _harden(profile.settings())
            self._profile = profile
        return self._profile

    def dispose(self) -> None:
        """Distrugge le pagine delle viste registrate ancora vive e poi il profilo, subito.

        Idempotente. Da chiamare alla chiusura della finestra e da `aboutToQuit`; dopo, il motore
        non crea più pagine.
        """
        if self._disposed:
            return
        self._disposed = True
        for view in self._views:
            if isValid(view):
                view.dispose()
        self._views.clear()
        profile, self._profile = self._profile, None
        if profile is not None and isValid(profile):
            delete_now(profile)

    def _on_download_requested(self, request: QWebEngineDownloadRequest) -> None:
        file_name = Path(request.suggestedFileName()).name
        chosen = self._choose_export_path(file_name)
        if chosen is None:
            request.cancel()
            return
        request.setDownloadDirectory(str(chosen.parent))
        request.setDownloadFileName(chosen.name)
        request.accept()


class ChartView(QWidget):
    """Mostra un grafico. `drawn` segnala che Plotly ha finito di disegnare.

    La pagina web (e la vista) non si creano nel costruttore: le avvia `start_engine`.
    """

    drawn = Signal()
    load_failed = Signal()

    def __init__(
        self, engine: ChartEngine, accessible_name: str, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self._engine = engine
        self._accessible_name = accessible_name
        self._current_figure: go.Figure | None = None
        self._page_loaded = False
        self._pending_script: str | None = None
        self._disposed = False
        self._page: _AppPage | None = None
        self._view: QWebEngineView | None = None
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        engine.register(self)

    @property
    def engine_started(self) -> bool:
        """Vero se la pagina web di questa vista è stata creata."""
        return self._page is not None

    def start_engine(self) -> None:
        """Crea la pagina e carica il documento; non fa nulla se già fatto o se si è chiusi."""
        if self._page is not None or self._disposed:
            return
        profile = self._engine.start()
        if profile is None:
            return
        page = _AppPage(profile, self)
        page.loadFinished.connect(self._on_load_finished)
        page.titleChanged.connect(self._on_title_changed)
        view = QWebEngineView(self)
        view.setPage(page)
        view.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        view.setAccessibleName(self._accessible_name)
        self._layout.addWidget(view)
        self._page = page
        self._view = view
        view.setUrl(QUrl(APP_PAGE_URL))

    @property
    def current_figure(self) -> go.Figure | None:
        """Ultima figura mostrata (o in attesa di essere mostrata)."""
        return self._current_figure

    def show_prepared(self, prepared: PreparedChart) -> None:
        """Disegna un grafico già preparato (appena la pagina è pronta)."""
        self._current_figure = prepared.figure
        self.start_engine()
        if self._page is None:  # chiusa: niente da disegnare
            return
        self._page.setBackgroundColor(QColor(prepared.background))
        if self._page_loaded:
            self._page.runJavaScript(prepared.script)
        else:
            self._pending_script = prepared.script

    @property
    def page(self) -> QWebEnginePage:
        """La pagina web (per diagnostica e test di integrazione); avvia il motore se serve."""
        self.start_engine()
        if self._page is None:
            raise RuntimeError("the chart view was disposed")
        return self._page

    def dispose(self) -> None:
        """Distrugge subito la pagina (il motore la vuole prima del profilo); idempotente."""
        if self._disposed:
            return
        self._disposed = True
        page, view = self._page, self._view
        self._page = None
        self._view = None
        if page is None or view is None:
            return
        if isValid(view):
            view.setPage(None)  # type: ignore[arg-type]  # stub senza Optional
        if isValid(page):
            delete_now(page)
        if isValid(view):
            delete_now(view)

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 - Qt
        """Chiudendo il widget si rilascia la pagina."""
        self.dispose()
        super().closeEvent(event)

    def _on_load_finished(self, ok: bool) -> None:
        if not ok:
            _LOG.error("Chart page failed to load")
            self.load_failed.emit()
            return
        self._page_loaded = True
        if self._pending_script is not None and self._page is not None:
            script, self._pending_script = self._pending_script, None
            self._page.runJavaScript(script)

    def _on_title_changed(self, title: str) -> None:
        if title.startswith("drawn-"):
            self.drawn.emit()
