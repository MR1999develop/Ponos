# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Finestra principale: barra laterale, menu, barra di stato e pagine.

Vista umile: lo stato e le decisioni stanno in `WorkspaceController` e nei modelli di
presentazione; qui ci sono solo widget, comandi e dialoghi.
"""

from __future__ import annotations

import html
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date
from functools import partial
from pathlib import Path
from zoneinfo import ZoneInfo

from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import (
    QAction,
    QCloseEvent,
    QDragEnterEvent,
    QDropEvent,
    QGuiApplication,
    QKeySequence,
)
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QStackedWidget,
    QWidget,
)

import ponos_coach
from ponos_coach.load.use_cases import ProfileOverview
from ponos_coach.profiles.domain import ProfileId, RegistryEntry
from ponos_coach.profiles.use_cases import FolderWarning, NewProfileRequest
from ponos_coach.ui.activity_table import ActivitiesPage
from ponos_coach.ui.errors import ErrorMessage, exclusion_reason_text
from ponos_coach.ui.import_report import ImportReport
from ponos_coach.ui.import_report_dialog import ImportReportDialog
from ponos_coach.ui.new_profile_dialog import NewProfileDialog
from ponos_coach.ui.new_profile_form import NewProfileForm
from ponos_coach.ui.overview_page import OverviewPage
from ponos_coach.ui.overview_state import (
    Idle,
    OverviewState,
    PrimaryAction,
    Ready,
    summary_of,
)
from ponos_coach.ui.tokens import (
    MIN_WINDOW_HEIGHT,
    MIN_WINDOW_WIDTH,
    SIDEBAR_WIDTH,
    SPACE_2,
    SPACE_4,
    STATUS_PROGRESS_WIDTH,
    Theme,
)
from ponos_coach.ui.workspace import (
    DeletionPreview,
    FolderCheck,
    ImportProgress,
    ProfileView,
    WorkspaceController,
)

APP_NAME = "Ponos Coach"
_KEY_GEOMETRY = "window/geometry"
_KEY_LAST_FOLDER = "import/lastFolder"
_KEY_LAST_FILES_FOLDER = "import/lastFilesFolder"
_KEY_LAST_PROFILE_FOLDER = "profiles/lastOpenFolder"
_KEY_LAST_EXPORT_FOLDER = "overview/lastExportFolder"
_DEFAULT_SIZE = (1100, 720)
_STATUS_MESSAGE_MS = 8000
_SECTION_OVERVIEW = 0
_SECTION_ACTIVITIES = 1


@dataclass(frozen=True, slots=True)
class NewProfileDefaults:
    """Proposte del modulo "Nuovo profilo", decise dal Composition Root."""

    parent_folder: Path
    timezone_name: str
    timezone_fell_back: bool
    today: Callable[[ZoneInfo], date]


def system_theme() -> Theme:
    """Tema chiaro o scuro secondo lo schema di colori del sistema (chiaro se ignoto)."""
    scheme = QGuiApplication.styleHints().colorScheme()
    return Theme.DARK if scheme == Qt.ColorScheme.Dark else Theme.LIGHT


class MainWindow(QMainWindow):
    """Finestra principale. Non decide nulla: mostra lo stato del controller."""

    def __init__(
        self,
        controller: WorkspaceController,
        settings: QSettings,
        defaults: NewProfileDefaults,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._controller = controller
        self._settings = settings
        self._defaults = defaults
        self._zone = ZoneInfo("UTC")
        self._failed_form: NewProfileForm | None = None
        self._creating_form: NewProfileForm | None = None
        self._new_dialog: NewProfileDialog | None = None
        self._boxes: list[QMessageBox | ImportReportDialog] = []
        self.setWindowTitle(APP_NAME)
        self.setMinimumSize(MIN_WINDOW_WIDTH, MIN_WINDOW_HEIGHT)
        self.setAcceptDrops(True)
        self._build_actions()
        self._build_menus()
        self._build_content()
        self._build_status_bar()
        self._restore_geometry()
        self._connect_controller()
        QGuiApplication.styleHints().colorSchemeChanged.connect(self._on_color_scheme_changed)
        self._on_overview_changed(controller.overview_state)
        self._update_actions()

    # --- Costruzione -------------------------------------------------------------------

    def _action(self, text: str, shortcut: str | None, slot: Callable[[], object]) -> QAction:
        action = QAction(text, self)
        if shortcut is not None:
            action.setShortcut(QKeySequence(shortcut))
        action.triggered.connect(slot)
        return action

    def _build_actions(self) -> None:
        self._new_action = self._action(self.tr("New profile..."), "Ctrl+N", self.show_new_profile)
        self._open_action = self._action(
            self.tr("Open profile from folder..."), "Ctrl+O", self.choose_profile_folder
        )
        self._import_files_action = self._action(
            self.tr("Import files..."), "Ctrl+I", self.choose_import_files
        )
        self._import_folder_action = self._action(
            self.tr("Import from folder..."), "Ctrl+Shift+I", self.choose_import_folder
        )
        self._stop_action = self._action(self.tr("Stop"), "Esc", self._controller.cancel_current)
        self._quit_action = self._action(self.tr("Quit"), "Ctrl+Q", self.close)
        self._about_action = self._action(self.tr("About Ponos Coach"), None, self.show_about)

    def _build_menus(self) -> None:
        file_menu = self.menuBar().addMenu(self.tr("File"))
        file_menu.addAction(self._new_action)
        file_menu.addAction(self._open_action)
        self._switch_menu = file_menu.addMenu(self.tr("Switch profile"))
        self._remove_menu = file_menu.addMenu(self.tr("Remove profile from list"))
        self._delete_menu = file_menu.addMenu(self.tr("Delete profile folder"))
        file_menu.addSeparator()
        file_menu.addAction(self._import_files_action)
        file_menu.addAction(self._import_folder_action)
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
            (self.tr("Activities"), True),
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
        self._sidebar.setCurrentRow(_SECTION_OVERVIEW)
        self._sidebar.currentRowChanged.connect(self._on_section_changed)

        self._overview = OverviewPage(self._choose_export_path, self)
        self._overview.action_requested.connect(self._on_overview_action)
        self._overview.cancel_requested.connect(self._controller.cancel_current)
        self._activities = ActivitiesPage(self)
        self._activities.import_requested.connect(self.choose_import_files)
        self._pages = QStackedWidget(self)
        self._pages.addWidget(self._overview)
        self._pages.addWidget(self._activities)

        central = QWidget(self)
        row = QHBoxLayout(central)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        row.addWidget(self._sidebar)
        row.addWidget(self._pages, stretch=1)
        self.setCentralWidget(central)

    def _build_status_bar(self) -> None:
        bar = self.statusBar()
        self._activities_label = self._status_label()
        self._excluded_label = self._status_label()
        self._days_label = self._status_label()
        for label in (self._activities_label, self._excluded_label, self._days_label):
            bar.addWidget(label)
        self._warning_label = self._status_label()
        bar.addPermanentWidget(self._warning_label)
        self._import_label = self._status_label()
        self._import_bar = QProgressBar(self)
        self._import_bar.setTextVisible(False)
        self._import_bar.setFixedWidth(STATUS_PROGRESS_WIDTH)
        self._import_cancel = QPushButton(self.tr("Stop import"), self)
        self._import_cancel.clicked.connect(self._controller.cancel_current)
        self._import_widgets: tuple[QWidget, ...] = (
            self._import_label,
            self._import_bar,
            self._import_cancel,
        )
        for widget in self._import_widgets:
            bar.addPermanentWidget(widget)
            widget.setVisible(False)
        self._update_status_summary(Idle())

    def _status_label(self) -> QLabel:
        label = QLabel(self)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setContentsMargins(SPACE_2, 0, SPACE_4, 0)
        return label

    def _connect_controller(self) -> None:
        controller = self._controller
        controller.profiles_changed.connect(self._rebuild_profile_menus)
        controller.profile_changed.connect(self._on_profile_changed)
        controller.overview_changed.connect(self._on_overview_changed)
        controller.import_progress_changed.connect(self._on_import_progress)
        controller.import_finished.connect(self._on_import_finished)
        controller.operation_failed.connect(self._on_operation_failed)
        controller.deletion_requested.connect(self._on_deletion_requested)
        controller.folder_inspected.connect(self._on_folder_inspected)
        controller.first_run_requested.connect(self.show_new_profile)
        controller.busy_changed.connect(self._update_actions)

    # --- Accessori per i test e il Composition Root -------------------------------------

    @property
    def overview_page(self) -> OverviewPage:
        """La pagina Panoramica."""
        return self._overview

    @property
    def activities_page(self) -> ActivitiesPage:
        """La pagina Attività."""
        return self._activities

    @property
    def controller(self) -> WorkspaceController:
        """Il controller dell'area di lavoro."""
        return self._controller

    @property
    def status_texts(self) -> tuple[str, str, str]:
        """Testi di attività, escluse e giorni nella barra di stato."""
        return (
            self._activities_label.text(),
            self._excluded_label.text(),
            self._days_label.text(),
        )

    @property
    def import_status_text(self) -> str:
        """Testo dell'avanzamento dell'import nella barra di stato."""
        return self._import_label.text()

    @property
    def folder_warning_text(self) -> str:
        """Avviso sulla cartella del profilo aperto (vuoto se non ce n'è)."""
        return self._warning_label.text()

    @property
    def new_profile_action(self) -> QAction:
        """Azione "Nuovo profilo..."."""
        return self._new_action

    @property
    def import_files_action(self) -> QAction:
        """Azione "Importa file..."."""
        return self._import_files_action

    @property
    def import_folder_action(self) -> QAction:
        """Azione "Importa da cartella..."."""
        return self._import_folder_action

    @property
    def stop_action(self) -> QAction:
        """Azione "Interrompi"."""
        return self._stop_action

    @property
    def switch_menu(self) -> QMenu:
        """Menu "Cambia profilo"."""
        return self._switch_menu

    @property
    def remove_menu(self) -> QMenu:
        """Menu "Rimuovi profilo dall'elenco"."""
        return self._remove_menu

    @property
    def delete_menu(self) -> QMenu:
        """Menu "Elimina cartella del profilo"."""
        return self._delete_menu

    @property
    def new_profile_dialog(self) -> NewProfileDialog | None:
        """Il dialogo "Nuovo profilo" aperto, se c'è."""
        return self._new_dialog

    @property
    def open_boxes(self) -> list[QMessageBox | ImportReportDialog]:
        """Finestre di messaggio e riepilogo aperte (le più recenti in fondo)."""
        return [box for box in self._boxes if box.isVisible()]

    # --- Avvio ---------------------------------------------------------------------------

    def start(self) -> None:
        """Avvia l'area di lavoro: apre l'ultimo profilo o chiede il primo avvio guidato."""
        self._controller.start()

    # --- Profili -------------------------------------------------------------------------

    def show_new_profile(self, form: NewProfileForm | None = None) -> None:
        """Apre il dialogo "Nuovo profilo" (nessun effetto se ne è già aperto uno)."""
        if self._new_dialog is not None or not self._controller.can_change_profile:
            return
        if form is None:
            form = NewProfileForm(self._defaults.parent_folder, self._defaults.timezone_name)
        dialog = NewProfileDialog(
            form,
            self._defaults.today(ZoneInfo(self._defaults.timezone_name)),
            self._controller.inspect_folder,
            self._choose_new_profile_folder,
            system_theme(),
            self,
        )
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.create_requested.connect(partial(self._create_profile, form))
        dialog.finished.connect(self._on_new_dialog_closed)
        self._new_dialog = dialog
        dialog.open()

    def _create_profile(self, form: NewProfileForm, request: object) -> None:
        if isinstance(request, NewProfileRequest):
            self._creating_form = form
            self._controller.create_profile(request)

    def _on_new_dialog_closed(self) -> None:
        self._new_dialog = None

    def _choose_new_profile_folder(self, start: str) -> str:
        return QFileDialog.getExistingDirectory(self, self.tr("Choose the profile folder"), start)

    def choose_profile_folder(self) -> None:
        """Chiede una cartella con un profilo e lo apre."""
        start = str(self._settings.value(_KEY_LAST_PROFILE_FOLDER, "", type=str))
        chosen = QFileDialog.getExistingDirectory(self, self.tr("Open profile from folder"), start)
        if chosen:
            self.open_profile_folder(Path(chosen))

    def open_profile_folder(self, folder: Path) -> None:
        """Ricorda la cartella e apre il profilo che contiene."""
        self._settings.setValue(_KEY_LAST_PROFILE_FOLDER, str(folder.parent))
        self._controller.open_profile_folder(folder)

    def _rebuild_profile_menus(self, entries: Sequence[RegistryEntry]) -> None:
        current = self._controller.profile
        current_id = None if current is None else current.profile_id
        self._fill_menu(self._switch_menu, entries, self._controller.switch_profile, current_id)
        self._fill_menu(self._remove_menu, entries, self._controller.remove_profile, None)
        self._fill_menu(self._delete_menu, entries, self._controller.request_deletion, current_id)
        self._update_actions()

    def _fill_menu(
        self,
        menu: QMenu,
        entries: Sequence[RegistryEntry],
        command: Callable[[ProfileId], bool],
        disabled_id: ProfileId | None,
    ) -> None:
        menu.clear()
        for entry in entries:
            action = menu.addAction(entry.cached_name.value.replace("&", "&&"))
            action.setToolTip(str(entry.folder))
            action.setEnabled(entry.profile_id != disabled_id)
            action.triggered.connect(partial(command, entry.profile_id))

    def _on_profile_changed(self, view: ProfileView) -> None:
        self._zone = view.zone
        self._creating_form = None
        self.setWindowTitle(self.tr("{profile} - Ponos Coach").format(profile=view.name.value))
        self._warning_label.setText(self._warning_text(view.warnings))
        self._warning_label.setVisible(bool(view.warnings))
        self._rebuild_profile_menus(self._controller.profiles)

    def _warning_text(self, warnings: Sequence[FolderWarning]) -> str:
        texts = {
            FolderWarning.NETWORK: self.tr(
                "Network folder: the database may be slow or unreliable."
            ),
            FolderWarning.CLOUD_SYNCED: self.tr(
                "Synchronised folder: the database may become damaged."
            ),
        }
        return " ".join(texts[warning] for warning in warnings)

    def _on_folder_inspected(self, check: FolderCheck) -> None:
        if self._new_dialog is not None:
            self._new_dialog.show_folder_check(check)

    def _on_deletion_requested(self, preview: DeletionPreview) -> None:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle(self.tr("Delete profile folder"))
        box.setTextFormat(Qt.TextFormat.PlainText)
        box.setText(
            self.tr('Delete the folder of the profile "{name}"?').format(
                name=preview.entry.cached_name.value
            )
        )
        if preview.activities is None:
            count_text = self.tr("The number of activities could not be read.")
        else:
            count_text = self.tr("%n activity(ies) will be deleted.", "", preview.activities)
        box.setInformativeText(
            "\n".join(
                (
                    self.tr("Folder: {folder}").format(folder=preview.entry.folder),
                    count_text,
                    self.tr("All data in the folder is deleted for good. This cannot be undone."),
                )
            )
        )
        box.setObjectName("deleteConfirmation")
        delete = box.addButton(self.tr("Delete folder"), QMessageBox.ButtonRole.DestructiveRole)
        cancel = box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(cancel)
        box.setEscapeButton(cancel)
        profile_id = preview.entry.profile_id
        box.buttonClicked.connect(
            lambda clicked, d=delete, pid=profile_id: self._on_delete_answer(clicked, d, pid)
        )
        self._remember(box)
        box.open()

    def _on_delete_answer(self, clicked: object, delete: object, profile_id: ProfileId) -> None:
        if clicked is delete:
            self._controller.delete_profile_folder(profile_id)

    # --- Import --------------------------------------------------------------------------

    def choose_import_files(self) -> None:
        """Chiede i file da importare (parte dall'ultima cartella usata)."""
        start = str(self._settings.value(_KEY_LAST_FILES_FOLDER, "", type=str))
        activity_files = self.tr("Activity files (*.fit *.gpx *.zip)")
        all_files = self.tr("All files (*)")
        chosen, _ = QFileDialog.getOpenFileNames(
            self, self.tr("Import files"), start, f"{activity_files};;{all_files}"
        )
        if chosen:
            self.import_files([Path(name) for name in chosen])

    def choose_import_folder(self) -> None:
        """Chiede la cartella da importare (parte dall'ultima usata)."""
        start = str(self._settings.value(_KEY_LAST_FOLDER, "", type=str))
        chosen = QFileDialog.getExistingDirectory(self, self.tr("Import from folder"), start)
        if chosen:
            self.import_folder(Path(chosen))

    def import_files(self, paths: Sequence[Path]) -> None:
        """Importa i file e ricorda la loro cartella."""
        if paths and self._controller.import_files(paths):
            self._settings.setValue(_KEY_LAST_FILES_FOLDER, str(paths[0].parent))

    def import_folder(self, folder: Path) -> None:
        """Importa la cartella e la ricorda."""
        if self._controller.import_folder(folder):
            self._settings.setValue(_KEY_LAST_FOLDER, str(folder))

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802 - Qt
        """Accetta file trascinati solo se un import è possibile."""
        if self._controller.can_import and event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802 - Qt
        """Importa i file (o la cartella) lasciati sulla finestra."""
        paths = [Path(url.toLocalFile()) for url in event.mimeData().urls() if url.isLocalFile()]
        if not paths or not self._controller.can_import:
            event.ignore()
            return
        event.acceptProposedAction()
        if len(paths) == 1 and paths[0].is_dir():
            self.import_folder(paths[0])
        else:
            self.import_files(paths)

    def _on_import_progress(self, progress: ImportProgress | None) -> None:
        for widget in self._import_widgets:
            widget.setVisible(progress is not None)
        if progress is None:
            return
        if progress.total <= 0:
            self._import_label.setText(self.tr("Looking for activity files..."))
            self._import_bar.setRange(0, 0)
            return
        # {done}: files already handled; %n: files to import.
        self._import_label.setText(
            self.tr("Imported {done} of %n file(s)", "", progress.total).format(done=progress.done)
        )
        self._import_bar.setRange(0, progress.total)
        self._import_bar.setValue(progress.done)

    def _on_import_finished(self, report: ImportReport) -> None:
        self.statusBar().showMessage(
            self.tr("%n activity(ies) imported", "", report.imported), _STATUS_MESSAGE_MS
        )
        dialog = ImportReportDialog(report, self)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self._remember(dialog)
        dialog.open()

    # --- Errori e messaggi ---------------------------------------------------------------

    def _on_operation_failed(self, message: ErrorMessage) -> None:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle(message.title)
        box.setTextFormat(Qt.TextFormat.PlainText)
        box.setText(message.title)
        box.setInformativeText(f"{message.what}\n\n{message.remedy}")
        box.setStandardButtons(QMessageBox.StandardButton.Ok)
        box.setObjectName("errorMessage")
        self._remember(box)
        box.open()
        if self._creating_form is not None:
            form, self._creating_form = self._creating_form, None
            self.show_new_profile(form)  # i dati scritti non si perdono

    def _remember(self, box: QMessageBox | ImportReportDialog) -> None:
        self._boxes = [item for item in self._boxes if item.isVisible()]
        self._boxes.append(box)

    def show_about(self) -> None:
        """Informazioni: versione, licenza e avviso che non è un dispositivo medico."""
        text = "\n\n".join(
            (
                self.tr("Version {version}").format(version=ponos_coach.__version__),
                self.tr("Adaptive training coach with Garmin and heart-rate data."),
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
        self._remember(box)
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

    # --- Reazione agli stati -------------------------------------------------------------

    def _on_section_changed(self, row: int) -> None:
        if 0 <= row < self._pages.count():
            self._pages.setCurrentIndex(row)

    def _on_overview_action(self, action: object) -> None:
        if action is PrimaryAction.NEW_PROFILE:
            self.show_new_profile()
        elif action is PrimaryAction.IMPORT_FILES:
            self.choose_import_files()
        elif action is PrimaryAction.RECALCULATE:
            self._controller.recalculate_overview()

    def _on_overview_changed(self, state: OverviewState) -> None:
        self._overview.apply_state(state, system_theme(), self._zone)
        if isinstance(state, Ready):
            self._activities.show_activities(state.overview.activities, self._zone)
        elif isinstance(state, Idle):
            self._activities.show_activities((), self._zone)
        self._update_status_summary(state)

    def _on_color_scheme_changed(self) -> None:
        self._overview.apply_state(self._controller.overview_state, system_theme(), self._zone)

    def _update_actions(self) -> None:
        controller = self._controller
        can_change = controller.can_change_profile
        busy_reason = self.tr("Wait for the current operation to finish, or stop it first.")
        for action in (self._new_action, self._open_action):
            action.setEnabled(can_change)
            action.setToolTip("" if can_change else busy_reason)
        for menu in (self._switch_menu, self._remove_menu, self._delete_menu):
            menu.setEnabled(can_change and not menu.isEmpty())
            menu.menuAction().setToolTip("" if can_change else busy_reason)
        can_import = controller.can_import
        if can_import:
            import_tip = ""
        elif controller.profile is None:
            import_tip = self.tr("Create or open a profile first.")
        else:
            import_tip = busy_reason
        for action in (self._import_files_action, self._import_folder_action):
            action.setEnabled(can_import)
            action.setToolTip(import_tip)
        self._stop_action.setEnabled(controller.can_stop)
        self._stop_action.setToolTip(
            "" if controller.can_stop else self.tr("There is nothing to stop.")
        )

    def _update_status_summary(self, state: OverviewState) -> None:
        summary = summary_of(state)
        labels = (self._activities_label, self._excluded_label, self._days_label)
        for label in labels:
            label.setVisible(summary is not None)
        if summary is None or not isinstance(state, Ready):
            return
        self._activities_label.setText(self.tr("%n activity(ies)", "", summary.activities))
        self._excluded_label.setText(self.tr("%n excluded activity(ies)", "", summary.excluded))
        self._excluded_label.setVisible(summary.excluded > 0)
        self._days_label.setText(self.tr("%n day(s) in the chart", "", summary.pmc_days))
        self._excluded_label.setToolTip(self._excluded_tooltip(state.overview))

    def _excluded_tooltip(self, overview: ProfileOverview) -> str:
        """Perché le attività sono escluse: un motivo per riga, con il conteggio."""
        counts = Counter(item.reason for item in overview.excluded)
        lines = [
            f"{html.escape(exclusion_reason_text(reason))}: {count}"
            for reason, count in counts.items()
        ]
        return "<br>".join(lines)

    # --- Chiusura ------------------------------------------------------------------------

    def _restore_geometry(self) -> None:
        saved = self._settings.value(_KEY_GEOMETRY)
        if saved is None or not self.restoreGeometry(saved):
            self.resize(*_DEFAULT_SIZE)

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 - Qt
        """Ricorda la geometria, ferma il thread dei dati e lo attende, rilascia il motore web."""
        self._settings.setValue(_KEY_GEOMETRY, self.saveGeometry())
        self._settings.sync()
        self._controller.shutdown()
        self._overview.chart.dispose()
        super().closeEvent(event)
