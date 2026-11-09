# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""View of a chart: an isolated `QWebEngineView` showing the local Plotly page.

Isolation: profile without on-disk storage, page and scripts served only by the app's
scheme, an interceptor that blocks every other request, no `QWebChannel` (the map page, a
subclass, is the one that has it), no context menu, no extra windows. Data enters as JSON
(`render_script`). The same engine also answers the map's tile requests (`ponos://tiles/...`)
through the tile service, never from the network.

The web engine profile (`ChartEngine`) is unique to the app and serves all charts: each
`ChartView` has its own page and registers with the engine. On close `ChartEngine.dispose`
synchronously destroys the registered pages and then the profile: the order does not depend on that
of the Qt children.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
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
from ponos_coach.ui.tile_service import TileService
from ponos_coach.ui.web_policy import (
    APP_PAGE_URL,
    APP_SCHEME,
    TILES_HOST,
    is_navigation_allowed,
    is_request_allowed,
)
from ponos_coach.ui.web_resources import resource_for_path

_LOG = logging.getLogger(__name__)

type ExportPathChooser = Callable[[str], Path | None]

_HOVER_PARTS = 3  # "hover", the index, a counter that makes every title new
_MAX_INDEX_DIGITS = 6
_RANGE_PARTS = 4  # "xrange", start, end, a counter
_AUTO_RANGE_PARTS = 3  # "xrange", "auto", a counter
_MAX_DATE_CHARS = 32
_DATE_CHARS = frozenset("0123456789-: .T")


@dataclass(frozen=True, slots=True)
class DateRange:
    """Dates shown on a chart's x axis, from `start` to `end` (naive: the chart's own days)."""

    start: datetime
    end: datetime


def _parse_date(text: str) -> datetime | None:
    """A date written by Plotly (`2026-03-01` or `2026-03-01 12:30:15.25`); None otherwise."""
    if not 0 < len(text) <= _MAX_DATE_CHARS or not set(text) <= _DATE_CHARS:
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


@dataclass(frozen=True, slots=True)
class RangeReport:
    """What the page reported after a zoom or pan: the dates shown, or None for the whole series."""

    shown: DateRange | None


def parse_range_title(title: str) -> RangeReport | None:
    """The report in a page title `xrange|START|END|N` or `xrange|auto|N`; None if the title is
    not a valid range report (a page title is untrusted text: only well-formed dates pass)."""
    parts = title.split("|")
    if parts[0] != "xrange":
        return None
    if len(parts) == _AUTO_RANGE_PARTS and parts[1] == "auto":
        return RangeReport(None)
    if len(parts) != _RANGE_PARTS:
        return None
    start, end = _parse_date(parts[1]), _parse_date(parts[2])
    if start is None or end is None or not start < end:
        return None
    return RangeReport(DateRange(start, end))


class AppOnlyInterceptor(QWebEngineUrlRequestInterceptor):
    """Block every request that is not of the app's scheme (`is_request_allowed` decides)."""

    def interceptRequest(self, info: QWebEngineUrlRequestInfo) -> None:  # noqa: N802 - Qt
        """Called by the web engine, also off the UI thread."""
        url = info.requestUrl()
        if not is_request_allowed(url):
            _LOG.warning("Blocked web request with scheme %r", url.scheme())
            info.block(True)


class AppSchemeHandler(QWebEngineUrlSchemeHandler):
    """Serve the resources of the closed list in `web_resources` and, from the tile service, the
    map tiles; everything else is 404."""

    def __init__(self, tiles: TileService | None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._tiles = tiles

    def requestStarted(self, job: QWebEngineUrlRequestJob) -> None:  # noqa: N802 - Qt
        """Answer a request of the app's scheme."""
        if job.requestMethod() != b"GET":
            job.fail(QWebEngineUrlRequestJob.Error.RequestDenied)
        elif job.requestUrl().host() == TILES_HOST:
            self._serve_tile(job)
        else:
            self._serve_resource(job)

    def _serve_resource(self, job: QWebEngineUrlRequestJob) -> None:
        resource = resource_for_path(job.requestUrl().path())
        if resource is None:
            job.fail(QWebEngineUrlRequestJob.Error.UrlNotFound)
            return
        buffer = QBuffer(job)  # lives as long as the request
        buffer.setData(resource.content)
        buffer.open(QIODevice.OpenModeFlag.ReadOnly)
        job.reply(resource.mime_type.encode("ascii"), buffer)

    def _serve_tile(self, job: QWebEngineUrlRequestJob) -> None:
        """Ask the tile service; the job waits (the page may close it meanwhile)."""
        alive = [True]
        job.destroyed.connect(lambda *_: alive.__setitem__(0, False))

        def reply(png: bytes | None) -> None:
            if not alive[0] or not isValid(job):
                return
            if png is None:
                job.fail(QWebEngineUrlRequestJob.Error.UrlNotFound)
                return
            buffer = QBuffer(job)
            buffer.setData(png)
            buffer.open(QIODevice.OpenModeFlag.ReadOnly)
            job.reply(b"image/png", buffer)

        if self._tiles is None or not self._tiles.request(job.requestUrl().path(), reply):
            job.fail(QWebEngineUrlRequestJob.Error.UrlNotFound)


class _AppPage(QWebEnginePage):
    """Page that never navigates outside the app's scheme."""

    def acceptNavigationRequest(  # noqa: N802 - Qt
        self, url: QUrl | str, nav_type: QWebEnginePage.NavigationType, is_main_frame: bool
    ) -> bool:
        """Only `ponos://app/...`."""
        del nav_type, is_main_frame
        return is_navigation_allowed(QUrl(url))


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
        self,
        choose_export_path: ExportPathChooser,
        parent: QObject | None = None,
        *,
        tiles: TileService | None = None,
    ) -> None:
        super().__init__(parent)
        self._choose_export_path = choose_export_path
        self._tiles = tiles
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
    def tiles(self) -> TileService | None:
        """The tile service the pages' tile requests go to (None where there is no map)."""
        return self._tiles

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
            self._scheme_handler = AppSchemeHandler(self._tiles, self)
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
        if self._tiles is not None:
            self._tiles.shutdown()
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
    hovered = Signal(int)  # index of the point under the pointer (the chart asked for it)
    hover_left = Signal()
    x_range_changed = Signal(object)  # DateRange, or None for the whole series (user's zoom/pan)

    _page_url = APP_PAGE_URL  # a subclass shows another page of the same scheme

    def __init__(
        self, engine: ChartEngine, accessible_name: str, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self._engine = engine
        self._accessible_name = accessible_name
        self._current_figure: go.Figure | None = None
        self._page_loaded = False
        self._pending_script: str | None = None
        self._queued: list[str] = []
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
        self._prepare_page(page)
        view = QWebEngineView(self)
        view.setPage(page)
        view.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        view.setAccessibleName(self._accessible_name)
        self._layout.addWidget(view)
        self._page = page
        self._view = view
        view.setUrl(QUrl(self._page_url))

    def _prepare_page(self, page: QWebEnginePage) -> None:
        """Hook: a subclass attaches what its page needs (before the document is loaded)."""
        del page

    def run_script(self, script: str) -> None:
        """Run a script in the page once it is loaded (kept if it is not yet; dropped if the
        view is closed). Only strings made by the application."""
        self.start_engine()
        if self._page is None:
            return
        if self._page_loaded:
            self._page.runJavaScript(script)
        else:
            self._queued.append(script)

    def set_x_range(self, shown: DateRange | None) -> None:
        """Show these dates on the x axis (None: the whole series), without reporting it back."""
        if shown is None:
            arguments = "null, null"
        else:
            arguments = ", ".join(
                json.dumps(moment.isoformat(sep=" ")) for moment in (shown.start, shown.end)
            )
        self.run_script(f"ponosSetXRange({arguments});")

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
        self._flush_scripts()

    def _flush_scripts(self) -> None:
        if self._page is None:
            return
        scripts, self._queued = self._queued, []
        if self._pending_script is not None:
            scripts.insert(0, self._pending_script)
            self._pending_script = None
        for script in scripts:
            self._page.runJavaScript(script)

    def _on_title_changed(self, title: str) -> None:
        """The page talks through its title (no channel): `drawn-N`, `hover-I-N`, `leave-N`,
        `xrange|START|END|N`, `xrange|auto|N`.

        The index is parsed as digits only; the caller checks it against its data.
        """
        if title.startswith("drawn-"):
            self.drawn.emit()
            return
        if title.startswith("xrange|"):
            report = parse_range_title(title)
            if report is not None:
                self.x_range_changed.emit(report.shown)
            return
        parts = title.split("-")
        if parts[0] == "leave":
            self.hover_left.emit()
        elif (
            parts[0] == "hover"
            and len(parts) == _HOVER_PARTS
            and parts[1].isdecimal()
            and len(parts[1]) <= _MAX_INDEX_DIGITS
        ):
            self.hovered.emit(int(parts[1]))
