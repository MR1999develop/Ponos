# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""View of a chart: an isolated `QWebEngineView` showing the local Plotly page.

Isolation: profile without on-disk storage, page and scripts served only by the app's
scheme, an interceptor that blocks every other request, no `QWebChannel`, no context
menu, no extra windows. Data enters as JSON (`render_script`).

The web engine profile (`ChartEngine`) is unique to the app and serves all charts: each
`ChartView` has its own page and registers with the engine. On close `ChartEngine.dispose`
synchronously destroys the registered pages and then the profile: the order does not depend on that
of the Qt children.
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
    """Block every request that is not of the app's scheme (`is_request_allowed` decides)."""

    def interceptRequest(self, info: QWebEngineUrlRequestInfo) -> None:  # noqa: N802 - Qt
        """Called by the web engine, also off the UI thread."""
        url = info.requestUrl()
        if not is_request_allowed(url):
            _LOG.warning("Blocked web request with scheme %r", url.scheme())
            info.block(True)


class AppSchemeHandler(QWebEngineUrlSchemeHandler):
    """Serve the resources of the closed list in `web_resources`; everything else is 404."""

    def requestStarted(self, job: QWebEngineUrlRequestJob) -> None:  # noqa: N802 - Qt
        """Answer a request of the app's scheme."""
        resource = None
        if job.requestMethod() == b"GET":
            resource = resource_for_path(job.requestUrl().path())
        if resource is None:
            job.fail(QWebEngineUrlRequestJob.Error.UrlNotFound)
            return
        buffer = QBuffer(job)  # lives as long as the request
        buffer.setData(resource.content)
        buffer.open(QIODevice.OpenModeFlag.ReadOnly)
        job.reply(resource.mime_type.encode("ascii"), buffer)


class _AppPage(QWebEnginePage):
    """Page that never navigates outside the app's scheme."""

    def acceptNavigationRequest(  # noqa: N802 - Qt
        self, url: QUrl | str, nav_type: QWebEnginePage.NavigationType, is_main_frame: bool
    ) -> bool:
        """Only `ponos://app/...`."""
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
    """The web engine profile, shared by all the app's charts.

    It is not created in the constructor: it costs about 180 ms on the UI thread, which Chromium
    does not allow
    to be moved. `start` starts it, which the window calls right after the first `show()`; whoever
    draws earlier starts it by itself. It holds the engine objects, because the references keep them
    alive.

    Destruction order: the profile cannot vanish before the pages. Views register
    (`register`); `dispose`, called when the window closes and from `aboutToQuit`, immediately
    destroys
    the pages of the views still alive and then the profile. A view closed earlier (a dialog)
    has already destroyed its own page.
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
        """A view that will use the profile: `dispose` will destroy its page before the profile."""
        self._views = [known for known in self._views if isValid(known)]
        self._views.append(view)

    @property
    def started(self) -> bool:
        """True if the profile has been created (and not yet destroyed)."""
        return self._profile is not None

    @property
    def disposed(self) -> bool:
        """True after `dispose`: no new pages."""
        return self._disposed

    def start(self) -> QWebEngineProfile | None:
        """Create the profile if needed and return it; `None` if the engine has been released."""
        if self._disposed:
            return None
        if self._profile is None:
            profile = QWebEngineProfile(self)  # no name: no data on disk
            self._interceptor = AppOnlyInterceptor(self)
            self._scheme_handler = AppSchemeHandler(self)
            profile.setUrlRequestInterceptor(self._interceptor)
            profile.installUrlSchemeHandler(APP_SCHEME.encode("ascii"), self._scheme_handler)
            profile.downloadRequested.connect(self._on_download_requested)
            _harden(profile.settings())
            self._profile = profile
        return self._profile

    def dispose(self) -> None:
        """Destroy the pages of the registered views still alive and then the profile, immediately.

        Idempotent. To call when the window closes and from `aboutToQuit`; afterwards the engine
        creates no more pages.
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
    """Show a chart. `drawn` signals that Plotly has finished drawing.

    The web page (and the view) are not created in the constructor: `start_engine` starts them.
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
        """True if this view's web page has been created."""
        return self._page is not None

    def start_engine(self) -> None:
        """Create the page and load the document; do nothing if already done or if closed."""
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
        """Last figure shown (or waiting to be shown)."""
        return self._current_figure

    def show_prepared(self, prepared: PreparedChart) -> None:
        """Draw an already prepared chart (as soon as the page is ready)."""
        self._current_figure = prepared.figure
        self.start_engine()
        if self._page is None:  # closed: nothing to draw
            return
        self._page.setBackgroundColor(QColor(prepared.background))
        if self._page_loaded:
            self._page.runJavaScript(prepared.script)
        else:
            self._pending_script = prepared.script

    @property
    def page(self) -> QWebEnginePage:
        """The web page (for diagnostics and integration tests); starts the engine if needed."""
        self.start_engine()
        if self._page is None:
            raise RuntimeError("the chart view was disposed")
        return self._page

    def dispose(self) -> None:
        """Destroy the page immediately (the engine wants it before the profile); idempotent."""
        if self._disposed:
            return
        self._disposed = True
        page, view = self._page, self._view
        self._page = None
        self._view = None
        if page is None or view is None:
            return
        if isValid(view):
            view.setPage(None)  # type: ignore[arg-type]  # stub without Optional
        if isValid(page):
            delete_now(page)
        if isValid(view):
            delete_now(view)

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 - Qt
        """Closing the widget releases the page."""
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
