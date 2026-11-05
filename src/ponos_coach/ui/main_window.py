# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Finestra principale: barra laterale, menu, barra di stato e pagina Panoramica."""

from __future__ import annotations

import html
from collections.abc import Sequence
from pathlib import Path

from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QAction, QCloseEvent, QGuiApplication, QKeySequence
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QStackedWidget,
    QWidget,
)

import ponos_coach
from ponos_coach.ui.errors import rejection_reason_text
from ponos_coach.ui.overview_controller import OverviewController
from ponos_coach.ui.overview_page import OverviewPage
from ponos_coach.ui.overview_state import Loading, OverviewState, Ready, summary_of
from ponos_coach.ui.tokens import (
    MIN_WINDOW_HEIGHT,
    MIN_WINDOW_WIDTH,
    SIDEBAR_WIDTH,
    SPACE_2,
    SPACE_4,
    Theme,
)

APP_NAME = "Ponos Coach"
_KEY_GEOMETRY = "window/geometry"
_KEY_LAST_FOLDER = "overview/lastFolder"
_KEY_LAST_EXPORT_FOLDER = "overview/lastExportFolder"
_DEFAULT_SIZE = (1100, 720)
_MAX_LISTED_REJECTED = 10  # file rifiutati elencati nel suggerimento


def system_theme() -> Theme:
    """Tema chiaro o scuro secondo lo schema di colori del sistema (chiaro se ignoto)."""
    scheme = QGuiApplication.styleHints().colorScheme()
    return Theme.DARK if scheme == Qt.ColorScheme.Dark else Theme.LIGHT


class MainWindow(QMainWindow):
    """Finestra principale. Non decide nulla: mostra lo stato del controller."""

    def __init__(
        self,
        controller: OverviewController,
        settings: QSettings,
        notices: Sequence[str] = (),
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._controller = controller
        self._settings = settings
        self.setWindowTitle(APP_NAME)
        self.setMinimumSize(MIN_WINDOW_WIDTH, MIN_WINDOW_HEIGHT)
        self._build_actions()
        self._build_menus()
        self._build_content()
        self._build_status_bar(notices)
        self._restore_geometry()
        self._controller.state_changed.connect(self._on_state_changed)
        QGuiApplication.styleHints().colorSchemeChanged.connect(self._on_color_scheme_changed)
        self._on_state_changed(self._controller.state)

    # --- Costruzione -------------------------------------------------------------------

    def _build_actions(self) -> None:
        self._open_action = QAction(self.tr("Open FIT folder..."), self)
        self._open_action.setShortcut(QKeySequence("Ctrl+O"))
        self._open_action.triggered.connect(self.choose_folder)
        self._stop_action = QAction(self.tr("Stop loading"), self)
        self._stop_action.setShortcut(QKeySequence("Esc"))
        self._stop_action.triggered.connect(self._controller.cancel)
        self._quit_action = QAction(self.tr("Quit"), self)
        self._quit_action.setShortcut(QKeySequence("Ctrl+Q"))
        self._quit_action.triggered.connect(self.close)
        self._about_action = QAction(self.tr("About Ponos Coach"), self)
        self._about_action.triggered.connect(self.show_about)

    def _build_menus(self) -> None:
        file_menu = self.menuBar().addMenu(self.tr("File"))
        file_menu.addAction(self._open_action)
        file_menu.addAction(self._stop_action)
        file_menu.addSeparator()
        file_menu.addAction(self._quit_action)
        help_menu = self.menuBar().addMenu(self.tr("Help"))
        help_menu.addAction(self._about_action)

    def _build_content(self) -> None:
        self._sidebar = QListWidget(self)
        self._sidebar.setObjectName("sidebar")
        self._sidebar.setAccessibleName(self.tr("Sections"))
        self._sidebar.setFixedWidth(SIDEBAR_WIDTH)
        later = self.tr("Available in a later version")
        sections = (
            (self.tr("Overview"), True),
            (self.tr("Activities"), False),
            (self.tr("Calendar"), False),
            (self.tr("Goals"), False),
            (self.tr("Injuries"), False),
            (self.tr("Tests"), False),
            (self.tr("Profile settings"), False),
        )
        for label, available in sections:
            item = QListWidgetItem(label, self._sidebar)
            if not available:
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
                item.setToolTip(later)
        self._sidebar.setCurrentRow(0)

        self._overview = OverviewPage(self._choose_export_path, self)
        self._overview.open_folder_requested.connect(self.choose_folder)
        self._overview.cancel_requested.connect(self._controller.cancel)
        self._pages = QStackedWidget(self)
        self._pages.addWidget(self._overview)

        central = QWidget(self)
        row = QHBoxLayout(central)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        row.addWidget(self._sidebar)
        row.addWidget(self._pages, stretch=1)
        self.setCentralWidget(central)

    def _build_status_bar(self, notices: Sequence[str]) -> None:
        bar = self.statusBar()
        self._activities_label = self._status_label()
        self._rejected_label = self._status_label()
        self._days_label = self._status_label()
        for label in (self._activities_label, self._rejected_label, self._days_label):
            bar.addWidget(label)
        for notice in notices:
            label = self._status_label()
            label.setText(notice)
            bar.addPermanentWidget(label)

    def _status_label(self) -> QLabel:
        label = QLabel(self)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setContentsMargins(SPACE_2, 0, SPACE_4, 0)
        return label

    # --- Dati e accessori per i test e il Composition Root -------------------------------

    @property
    def overview_page(self) -> OverviewPage:
        """La pagina Panoramica."""
        return self._overview

    @property
    def controller(self) -> OverviewController:
        """Il controller della panoramica."""
        return self._controller

    @property
    def status_texts(self) -> tuple[str, str, str]:
        """Testi di attivita, file rifiutati e giorni nella barra di stato."""
        return (
            self._activities_label.text(),
            self._rejected_label.text(),
            self._days_label.text(),
        )

    @property
    def open_action(self) -> QAction:
        """Azione "Apri cartella FIT..."."""
        return self._open_action

    @property
    def stop_action(self) -> QAction:
        """Azione "Interrompi caricamento"."""
        return self._stop_action

    # --- Comandi -----------------------------------------------------------------------

    def choose_folder(self) -> None:
        """Chiede una cartella all'utente (parte dall'ultima usata) e la carica."""
        start = str(self._settings.value(_KEY_LAST_FOLDER, "", type=str))
        chosen = QFileDialog.getExistingDirectory(self, self.tr("Open FIT folder"), start)
        if chosen:
            self.open_folder(Path(chosen))

    def open_folder(self, folder: Path) -> None:
        """Ricorda la cartella e avvia il calcolo (nessun effetto se ne e in corso uno)."""
        if self._controller.load(folder):
            self._settings.setValue(_KEY_LAST_FOLDER, str(folder))

    def show_about(self) -> None:
        """Informazioni: versione, licenza e avviso che non e un dispositivo medico."""
        text = "\n\n".join(
            (
                self.tr("Version {version}").format(version=ponos_coach.__version__),
                self.tr("Running training with Garmin and heart-rate data."),
                self.tr("License: GPL-3.0-only."),
                self.tr(
                    "Ponos Coach is not a medical device and gives no diagnosis. "
                    "If you feel pain or unusual symptoms, see a doctor."
                ),
            )
        )
        box = QMessageBox(self)
        box.setWindowTitle(self.tr("About Ponos Coach"))
        box.setTextFormat(Qt.TextFormat.PlainText)
        box.setText(text)
        box.setIcon(QMessageBox.Icon.Information)
        box.open()

    def _choose_export_path(self, suggested_name: str) -> Path | None:
        start_folder = str(self._settings.value(_KEY_LAST_EXPORT_FOLDER, "", type=str))
        start = str(Path(start_folder) / suggested_name) if start_folder else suggested_name
        chosen, _ = QFileDialog.getSaveFileName(self, self.tr("Save chart"), start)
        if not chosen:
            return None
        path = Path(chosen)
        self._settings.setValue(_KEY_LAST_EXPORT_FOLDER, str(path.parent))
        return path

    # --- Reazione agli stati -----------------------------------------------------------

    def _on_state_changed(self, state: OverviewState) -> None:
        self._overview.apply_state(state, system_theme())
        loading = isinstance(state, Loading)
        self._open_action.setEnabled(not loading)
        self._open_action.setToolTip(
            self.tr("Wait for the current loading to finish, or stop it first.") if loading else ""
        )
        self._stop_action.setEnabled(loading)
        self._stop_action.setToolTip(
            "" if loading else self.tr("There is nothing to stop: no folder is being loaded.")
        )
        self._update_title(state)
        self._update_status(state)

    def _on_color_scheme_changed(self) -> None:
        self._overview.apply_state(self._controller.state, system_theme())

    def _update_title(self, state: OverviewState) -> None:
        if isinstance(state, Ready | Loading):
            self.setWindowTitle(
                self.tr("{folder} - Ponos Coach").format(folder=state.folder.name or APP_NAME)
            )
        else:
            self.setWindowTitle(APP_NAME)

    def _update_status(self, state: OverviewState) -> None:
        summary = summary_of(state)
        for label in (self._activities_label, self._rejected_label, self._days_label):
            label.setVisible(summary is not None)
        if summary is None:
            return
        self._activities_label.setText(self.tr("%n activity(ies)", "", summary.activities))
        self._rejected_label.setText(self.tr("%n rejected file(s)", "", summary.rejected))
        self._days_label.setText(self.tr("%n day(s) in the chart", "", summary.pmc_days))
        self._rejected_label.setToolTip(self._rejected_tooltip(state))

    def _rejected_tooltip(self, state: OverviewState) -> str:
        """Elenco dei file rifiutati. Il suggerimento e testo ricco: i nomi sono escapati."""
        if not isinstance(state, Ready) or not state.overview.rejected:
            return ""
        rejected = state.overview.rejected
        lines = [
            f"{html.escape(item.file_name)}: {html.escape(rejection_reason_text(item.reason))}"
            for item in rejected[:_MAX_LISTED_REJECTED]
        ]
        hidden = len(rejected) - _MAX_LISTED_REJECTED
        if hidden > 0:
            lines.append(html.escape(self.tr("and %n more", "", hidden)))
        return "<br>".join(lines)

    # --- Chiusura ----------------------------------------------------------------------

    def _restore_geometry(self) -> None:
        saved = self._settings.value(_KEY_GEOMETRY)
        if saved is None or not self.restoreGeometry(saved):
            self.resize(*_DEFAULT_SIZE)

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 - Qt
        """Ricorda la geometria, ferma il worker e lo attende, rilascia il motore web."""
        self._settings.setValue(_KEY_GEOMETRY, self.saveGeometry())
        self._settings.sync()
        self._controller.shutdown()
        self._overview.chart.dispose()
        super().closeEvent(event)
