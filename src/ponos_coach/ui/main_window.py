# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Main window: sidebar, menus, status bar and pages.

Humble view: state and decisions live in `WorkspaceController` and in the
presentation models; here there are only widgets, commands and dialogs.
"""

from __future__ import annotations

import html
import time
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date
from functools import partial
from pathlib import Path
from zoneinfo import ZoneInfo

from PySide6.QtCore import QByteArray, QSettings, Qt, QTimer, QUrl
from PySide6.QtGui import (
    QAction,
    QActionGroup,
    QCloseEvent,
    QDesktopServices,
    QDragEnterEvent,
    QDropEvent,
    QGuiApplication,
    QKeySequence,
    QShowEvent,
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
from ponos_coach.activities.domain import ActivityIdentity
from ponos_coach.load.use_cases import ProfileOverview
from ponos_coach.profiles.domain import ProfileId, RegistryEntry
from ponos_coach.profiles.use_cases import FolderWarning, NewProfileRequest
from ponos_coach.ui.activity_detail_loader import ActivityDetailLoader
from ponos_coach.ui.activity_table import ActivitiesPage
from ponos_coach.ui.calendar_page import CalendarPage
from ponos_coach.ui.chart_view import ChartEngine
from ponos_coach.ui.data_thread import SHUTDOWN_WAIT_MS
from ponos_coach.ui.day_page import DayPage
from ponos_coach.ui.errors import ErrorMessage, exclusion_reason_text, unexpected_error_message
from ponos_coach.ui.garmin_ui import GarminUi
from ponos_coach.ui.goals_page import GoalsPage
from ponos_coach.ui.import_report import ImportReport
from ponos_coach.ui.import_report_dialog import ImportReportDialog
from ponos_coach.ui.new_profile_dialog import NewProfileDialog
from ponos_coach.ui.new_profile_form import NewProfileForm
from ponos_coach.ui.nutrition_ui import NutritionUi
from ponos_coach.ui.overview_page import OverviewPage
from ponos_coach.ui.overview_state import (
    Idle,
    OverviewState,
    PrimaryAction,
    Ready,
    summary_of,
)
from ponos_coach.ui.plan_ui import PlanUi
from ponos_coach.ui.profile_dialogs import ProfileDialogs
from ponos_coach.ui.profile_editor import ProfileEditor
from ponos_coach.ui.threshold_presentation import can_be_threshold_test, not_a_run_reason
from ponos_coach.ui.threshold_ui import ThresholdUi
from ponos_coach.ui.tokens import (
    MIN_WINDOW_HEIGHT,
    MIN_WINDOW_WIDTH,
    SIDEBAR_WIDTH,
    SPACE_2,
    SPACE_4,
    STATUS_PROGRESS_WIDTH,
    Theme,
)
from ponos_coach.ui.translations import Language, save_language
from ponos_coach.ui.workspace import (
    DeletionPreview,
    FolderCheck,
    ImportProgress,
    ProfileView,
    WorkspaceController,
)

APP_NAME = "Ponos Coach"
_KEY_GEOMETRY = "window/geometry"
_KEY_SECTION = "window/section"
_KEY_LAST_FOLDER = "import/lastFolder"
_KEY_LAST_FILES_FOLDER = "import/lastFilesFolder"
_KEY_LAST_PROFILE_FOLDER = "profiles/lastOpenFolder"
_KEY_LAST_EXPORT_FOLDER = "overview/lastExportFolder"
_DEFAULT_SIZE = (1100, 720)
_STATUS_MESSAGE_MS = 8000
_SECTION_OVERVIEW = 0
_SECTION_ACTIVITIES = 1
_SECTION_CALENDAR = 2
_SECTION_GOALS = 3
_AVAILABLE_SECTIONS = (
    _SECTION_OVERVIEW,
    _SECTION_ACTIVITIES,
    _SECTION_CALENDAR,
    _SECTION_GOALS,
)
_SHUTDOWN_POLL_MS = 20


FolderPicker = Callable[[QWidget, str, str], str]
"""Folder picker: (parent, title, start) -> chosen folder ("" if cancelled)."""


def native_folder_picker(parent: QWidget, title: str, start: str) -> str:
    """The system folder picker."""
    return QFileDialog.getExistingDirectory(parent, title, start)


@dataclass(frozen=True, slots=True)
class NewProfileDefaults:
    """Dependencies of the "New profile" module, decided by the Composition Root.

    `documents_folder` is only the picker's starting point the first time, not a suggestion.
    """

    documents_folder: Path
    pick_folder: FolderPicker
    timezone_name: str
    timezone_fell_back: bool
    today: Callable[[ZoneInfo], date]


def system_theme() -> Theme:
    """Light or dark theme according to the system colour scheme (light if unknown)."""
    scheme = QGuiApplication.styleHints().colorScheme()
    return Theme.DARK if scheme == Qt.ColorScheme.Dark else Theme.LIGHT


class MainWindow(QMainWindow):
    """Main window. It decides nothing: it shows the controller's state."""

    def __init__(
        self,
        controller: WorkspaceController,
        settings: QSettings,
        defaults: NewProfileDefaults,
        parent: QWidget | None = None,
        *,
        log_folder: Path | None = None,
        language: Language = Language.ENGLISH,
    ) -> None:
        super().__init__(parent)
        self._controller = controller
        self._language = language
        self._settings = settings
        self._defaults = defaults
        self._log_folder = log_folder
        self.shutdown_wait_ms = SHUTDOWN_WAIT_MS  # cap on the wait at close
        self._shutdown_deadline = 0.0
        self._closed = False
        self._engine_requested = False
        self._shutdown_timer = QTimer(self)
        self._shutdown_timer.setInterval(_SHUTDOWN_POLL_MS)
        self._shutdown_timer.timeout.connect(self._poll_shutdown)
        self._engine_timer = QTimer(self)
        self._engine_timer.setSingleShot(True)
        self._engine_timer.timeout.connect(self._start_web_engine)
        self._zone = ZoneInfo("UTC")
        self._failed_form: NewProfileForm | None = None
        self._creating_form: NewProfileForm | None = None
        self._new_dialog: NewProfileDialog | None = None
        self._boxes: list[QMessageBox | ImportReportDialog] = []
        self.setWindowTitle(APP_NAME)
        self.setMinimumSize(MIN_WINDOW_WIDTH, MIN_WINDOW_HEIGHT)
        self.setAcceptDrops(True)
        self._build_actions()
        self._garmin = GarminUi(self, controller.sync, system_theme, self._remember)
        self._build_content()
        self._build_dialogs()
        self._build_menus()
        self._build_status_bar()
        self._restore_geometry()
        self._restore_section()
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
        self._threshold = ThresholdUi(
            self,
            self._controller,
            settings=self._settings,
            zone=lambda: self._zone,
            show_error=self._show_error,
            notify=lambda text: self.statusBar().showMessage(text, _STATUS_MESSAGE_MS),
        )
        self._threshold.retest_text_changed.connect(self._on_retest_text_changed)
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
        self._edit_profile_action = self._action(
            self.tr("Edit profile..."), "Ctrl+E", self.edit_profile
        )
        self._sport_weights_action = self._action(
            self.tr("Sport weights..."), "Ctrl+Shift+W", self.edit_sport_weights
        )
        self._nutrition_action = self._action(
            self.tr("Nutrition settings..."), "Ctrl+Shift+N", self.edit_nutrition_settings
        )
        self._previous_month_action = self._action(
            self.tr("Previous month"), "Ctrl+Left", self._calendar_step_back
        )
        self._next_month_action = self._action(
            self.tr("Next month"), "Ctrl+Right", self._calendar_step_forward
        )
        self._today_action = self._action(self.tr("Today"), "Ctrl+T", self._calendar_today)
        self._open_day_action = self._action(
            self.tr("Open day"), "Ctrl+Return", self._calendar_open_day
        )
        self._back_action = self._action(
            self.tr("Back to calendar"), "Alt+Left", self._back_to_calendar
        )
        self._detail_action = self._action(
            self.tr("Activity detail..."), "Ctrl+D", self.show_selected_activity_detail
        )
        self._threshold_instructions_action = self._action(
            self.tr("Test instructions..."), "Ctrl+Shift+H", self._threshold.show_instructions
        )
        self._use_as_test_action = self._action(
            self.tr("Use as threshold test..."),
            "Ctrl+Shift+U",
            self.use_selected_as_threshold_test,
        )
        self._threshold_history_action = self._action(
            self.tr("Threshold tests..."), "Ctrl+Shift+R", self._threshold.show_history
        )
        self._about_action = self._action(self.tr("About Ponos Coach"), None, self.show_about)
        self._log_action = self._action(
            self.tr("Open the log folder"), "Ctrl+Shift+L", self.open_log_folder
        )
        if self._log_folder is None:
            self._log_action.setEnabled(False)
            self._log_action.setToolTip(self.tr("The log folder is not known."))

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
        profile_menu = self.menuBar().addMenu(self.tr("Profile"))
        profile_menu.addAction(self._edit_profile_action)
        profile_menu.addAction(self._sport_weights_action)
        profile_menu.addAction(self._nutrition_action)
        profile_menu.addSeparator()
        profile_menu.addAction(self._detail_action)
        threshold_menu = self.menuBar().addMenu(self.tr("Threshold test"))
        threshold_menu.addAction(self._threshold_instructions_action)
        threshold_menu.addAction(self._use_as_test_action)
        threshold_menu.addAction(self._threshold_history_action)
        threshold_menu.addSeparator()
        threshold_menu.addAction(self._threshold.undo_action)
        threshold_menu.addAction(self._threshold.redo_action)
        calendar_menu = self.menuBar().addMenu(self.tr("Calendar"))
        for action in (
            self._previous_month_action,
            self._next_month_action,
            self._today_action,
            self._open_day_action,
            self._back_action,
        ):
            calendar_menu.addAction(action)
        self._build_plan_menu()
        self._garmin.build_menu(self.menuBar())
        self._build_language_menu()
        help_menu = self.menuBar().addMenu(self.tr("Help"))
        help_menu.addAction(self._log_action)
        help_menu.addSeparator()
        help_menu.addAction(self._about_action)

    def _build_plan_menu(self) -> None:
        """Menu "Plan": the goals, the training settings, and undo and redo of both."""
        menu = self.menuBar().addMenu(self.tr("Plan"))
        actions = self._plan.actions
        for name in ("add_race", "add_form_goal", "change_goal", "remove_goal"):
            menu.addAction(actions[name])
        menu.addSeparator()
        menu.addAction(actions["training"])
        menu.addSeparator()
        menu.addAction(actions["undo"])
        menu.addAction(actions["redo"])

    def _build_language_menu(self) -> None:
        """Menu "Language": English and Italian, with the choice remembered for the next
        launches."""
        menu = self.menuBar().addMenu(self.tr("Language"))
        group = QActionGroup(self)
        group.setExclusive(True)
        self._language_actions: dict[Language, QAction] = {}
        for language in (Language.ENGLISH, Language.ITALIAN):
            # A language's name in the language itself is not translated: it serves to recognise it.
            action = QAction(language.native_name, self)
            action.setCheckable(True)
            action.setChecked(language is self._language)
            action.triggered.connect(partial(self._on_language_chosen, language))
            group.addAction(action)
            menu.addAction(action)
            self._language_actions[language] = action

    def _build_content(self) -> None:
        self._sidebar = QListWidget(self)
        self._sidebar.setObjectName("sidebar")
        self._sidebar.setAccessibleName(self.tr("Sections"))
        self._sidebar.setFixedWidth(SIDEBAR_WIDTH)
        later = self.tr("Available in a later version")
        sections = (
            (self.tr("Overview"), True),
            (self.tr("Activities"), True),
            (self.tr("Calendar"), True),
            (self.tr("Goals"), True),
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

        self._engine = ChartEngine(self._choose_export_path, self)
        self._overview = OverviewPage(self._engine, self._controller.compute, self)
        self._overview.action_requested.connect(self._on_overview_action)
        self._overview.cancel_requested.connect(self._controller.cancel_current)
        self._activities = ActivitiesPage(self)
        self._activities.import_requested.connect(self.choose_import_files)
        self._activities.selection_changed.connect(self._update_actions)
        self._calendar = CalendarPage(self._today(), self)
        self._goals = GoalsPage(self)
        self._day_page = DayPage(self)
        self._pages = QStackedWidget(self)
        self._section_pages: dict[int, QWidget] = {
            _SECTION_OVERVIEW: self._overview,
            _SECTION_ACTIVITIES: self._activities,
            _SECTION_CALENDAR: self._calendar,
            _SECTION_GOALS: self._goals,
        }
        for page in (*self._section_pages.values(), self._day_page):
            self._pages.addWidget(page)
        self._pages.currentChanged.connect(self._update_actions)
        self._calendar.month_changed.connect(self._update_actions)
        self._calendar.selection_changed.connect(self._update_actions)

        central = QWidget(self)
        row = QHBoxLayout(central)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        row.addWidget(self._sidebar)
        row.addWidget(self._pages, stretch=1)
        self.setCentralWidget(central)

    def _build_dialogs(self) -> None:
        """Edit profile, sport weights and detail: they work on the data thread."""
        self._editor = ProfileEditor(self._controller, self)
        self._detail_loader = ActivityDetailLoader(self._controller, self)
        self._dialogs = ProfileDialogs(
            self,
            self._editor,
            self._detail_loader,
            self._engine,
            theme=system_theme,
            today=lambda: self._defaults.today(self._zone),
            zone=lambda: self._zone,
            profile_name=self._profile_name,
            show_error=self._show_error,
            notify=lambda text: self.statusBar().showMessage(text, _STATUS_MESSAGE_MS),
        )
        self._activities.detail_requested.connect(self._dialogs.show_activity_detail)
        self._dialogs.threshold_test_requested.connect(self._on_detail_threshold_test)
        self._nutrition = NutritionUi(
            self,
            self._controller,
            self._calendar,
            self._day_page,
            today=self._today,
            show_day_page=lambda: self._pages.setCurrentWidget(self._day_page),
            show_calendar_page=self._show_calendar,
            show_error=self._show_error,
        )
        self._day_page.back_requested.connect(self._back_to_calendar)
        self._day_page.edit_profile_requested.connect(self._edit_profile_from_day)
        self._plan = PlanUi(
            self,
            self._controller,
            self._calendar,
            self._goals,
            self._overview,
            today=self._today,
            theme=system_theme,
            show_error=self._show_error,
            show_goals_page=self._show_goals,
            edit_profile=self._edit_profile_from_day,
        )
        self._plan.retest_proposal_changed.connect(self._on_plan_retest_changed)

    def _today(self) -> date:
        return self._defaults.today(self._zone)

    def _profile_name(self) -> str:
        profile = self._controller.profile
        return "" if profile is None else profile.name.value

    def _build_status_bar(self) -> None:
        bar = self.statusBar()
        self._activities_label = self._status_label()
        self._excluded_label = self._status_label()
        self._days_label = self._status_label()
        for label in (self._activities_label, self._excluded_label, self._days_label):
            bar.addWidget(label)
        self._retest_label = self._status_label()
        self._retest_label.setVisible(False)
        bar.addPermanentWidget(self._retest_label)
        self._plan_retest_label = self._status_label()
        self._plan_retest_label.setObjectName("planRetestLabel")
        self._plan_retest_label.setVisible(False)
        bar.addPermanentWidget(self._plan_retest_label)
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

    # --- Accessors for tests and the Composition Root -------------------------------------

    @property
    def garmin(self) -> GarminUi:
        """The menu, the status bar and the Garmin account dialogs."""
        return self._garmin

    @property
    def overview_page(self) -> OverviewPage:
        """The Overview page."""
        return self._overview

    @property
    def activities_page(self) -> ActivitiesPage:
        """The Activities page."""
        return self._activities

    @property
    def calendar_page(self) -> CalendarPage:
        """The Calendar page."""
        return self._calendar

    @property
    def goals_page(self) -> GoalsPage:
        """The Goals page."""
        return self._goals

    @property
    def plan(self) -> PlanUi:
        """The plan flow (goals, training settings, plan state)."""
        return self._plan

    @property
    def plan_retest_text(self) -> str:
        """The proposal of the plan for the next threshold test (empty while hidden)."""
        return self._plan_retest_label.text()

    @property
    def day_page(self) -> DayPage:
        """The page of a day."""
        return self._day_page

    @property
    def nutrition(self) -> NutritionUi:
        """The nutrition flow (day page and settings dialog)."""
        return self._nutrition

    @property
    def nutrition_action(self) -> QAction:
        """Action "Nutrition settings..."."""
        return self._nutrition_action

    @property
    def current_page(self) -> QWidget:
        """The page shown now."""
        page = self._pages.currentWidget()
        return self._overview if page is None else page

    @property
    def sidebar(self) -> QListWidget:
        """The section list."""
        return self._sidebar

    @property
    def calendar_actions(self) -> dict[str, QAction]:
        """The Calendar menu actions by name."""
        return {
            "previous": self._previous_month_action,
            "next": self._next_month_action,
            "today": self._today_action,
            "open_day": self._open_day_action,
            "back": self._back_action,
        }

    @property
    def profile_dialogs(self) -> ProfileDialogs:
        """The profile data dialogs (edit, weights, detail)."""
        return self._dialogs

    @property
    def language_actions(self) -> dict[Language, QAction]:
        """The Language menu entries."""
        return self._language_actions

    @property
    def edit_profile_action(self) -> QAction:
        """Action "Edit profile..."."""
        return self._edit_profile_action

    @property
    def sport_weights_action(self) -> QAction:
        """Action "Sport weights..."."""
        return self._sport_weights_action

    @property
    def detail_action(self) -> QAction:
        """Action "Activity detail..."."""
        return self._detail_action

    @property
    def threshold(self) -> ThresholdUi:
        """The threshold test flow (dialogs, undo stack, retest status)."""
        return self._threshold

    @property
    def threshold_instructions_action(self) -> QAction:
        """Action "Test instructions..."."""
        return self._threshold_instructions_action

    @property
    def use_as_test_action(self) -> QAction:
        """Action "Use as threshold test..."."""
        return self._use_as_test_action

    @property
    def threshold_history_action(self) -> QAction:
        """Action "Threshold tests..."."""
        return self._threshold_history_action

    @property
    def retest_status_text(self) -> str:
        """The retest sentence in the status bar (empty while hidden)."""
        return self._retest_label.text()

    @property
    def controller(self) -> WorkspaceController:
        """The workspace controller."""
        return self._controller

    @property
    def status_texts(self) -> tuple[str, str, str]:
        """Texts of activities, excluded ones and days in the status bar."""
        return (
            self._activities_label.text(),
            self._excluded_label.text(),
            self._days_label.text(),
        )

    @property
    def import_status_text(self) -> str:
        """Import progress text in the status bar."""
        return self._import_label.text()

    @property
    def folder_warning_text(self) -> str:
        """Warning about the open profile's folder (empty if there is none)."""
        return self._warning_label.text()

    @property
    def log_action(self) -> QAction:
        """Action "Open the log folder"."""
        return self._log_action

    @property
    def is_closed(self) -> bool:
        """True when closing is done (the profile is closed and the web engine released)."""
        return self._closed

    @property
    def new_profile_action(self) -> QAction:
        """Action "New profile..."."""
        return self._new_action

    @property
    def import_files_action(self) -> QAction:
        """Action "Import files..."."""
        return self._import_files_action

    @property
    def import_folder_action(self) -> QAction:
        """Action "Import from folder..."."""
        return self._import_folder_action

    @property
    def stop_action(self) -> QAction:
        """Action "Stop"."""
        return self._stop_action

    @property
    def switch_menu(self) -> QMenu:
        """Menu "Switch profile"."""
        return self._switch_menu

    @property
    def remove_menu(self) -> QMenu:
        """Menu "Remove profile from list"."""
        return self._remove_menu

    @property
    def delete_menu(self) -> QMenu:
        """Menu "Delete profile folder"."""
        return self._delete_menu

    @property
    def new_profile_dialog(self) -> NewProfileDialog | None:
        """The open "New profile" dialog, if any."""
        return self._new_dialog

    @property
    def open_boxes(self) -> list[QMessageBox | ImportReportDialog]:
        """Open message and summary windows (the most recent last)."""
        return [box for box in self._boxes if box.isVisible()]

    # --- Avvio ---------------------------------------------------------------------------

    def start(self) -> None:
        """Start the workspace: open the last profile or ask for the guided first start."""
        self._controller.start()

    # --- Lingua ----------------------------------------------------------------------------

    def _on_language_chosen(self, language: Language) -> None:
        """Remember the chosen language. It applies at the next launch: retranslating every window,
        menu
        and chart live would duplicate every text, for a change made once."""
        save_language(self._settings, language)
        if language is self._language:
            return
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Information)
        box.setWindowTitle(self.tr("Language"))
        box.setTextFormat(Qt.TextFormat.PlainText)
        box.setText(self.tr("The language changes the next time Ponos Coach starts."))
        box.setObjectName("languageChanged")
        self._remember(box)
        box.open()

    # --- Profile data ------------------------------------------------------------------

    def edit_profile(self) -> None:
        """Open "Edit profile" (no effect if it cannot be done now)."""
        if self._controller.can_edit_profile:
            self._dialogs.edit_profile()

    def edit_sport_weights(self) -> None:
        """Open "Sport weights" (no effect if it cannot be done now)."""
        if self._controller.can_edit_profile:
            self._dialogs.edit_sport_weights()

    def edit_nutrition_settings(self) -> None:
        """Open "Nutrition settings" (no effect without an open profile)."""
        if self._controller.profile is not None:
            self._nutrition.show_settings()

    def use_selected_as_threshold_test(self) -> None:
        """Evaluate the activity selected in the list as the threshold test (no effect if the
        command is not available)."""
        activity = self._activities.selected_activity()
        if activity is not None and self._use_as_test_action.isEnabled():
            self._threshold.start_test(activity.identity)

    def _on_detail_threshold_test(self, identity: object) -> None:
        if isinstance(identity, ActivityIdentity) and self._threshold_available():
            self._threshold.start_test(identity)

    def _on_retest_text_changed(self, text: str) -> None:
        self._retest_label.setText(text)
        self._retest_label.setVisible(bool(text))

    def _on_plan_retest_changed(self, text: str) -> None:
        self._plan_retest_label.setText(text)
        self._plan_retest_label.setVisible(bool(text))

    def _show_goals(self) -> None:
        self._sidebar.setCurrentRow(_SECTION_GOALS)

    def _edit_profile_from_day(self) -> None:
        """Open "Edit profile" from the day page's missing-data message."""
        if self._controller.can_edit_profile:
            self._dialogs.edit_profile()
        else:
            reason = self._unavailable_reason(False)
            self.statusBar().showMessage(reason, _STATUS_MESSAGE_MS)

    def _show_calendar(self) -> None:
        self._sidebar.setCurrentRow(_SECTION_CALENDAR)
        self._pages.setCurrentWidget(self._calendar)

    def _back_to_calendar(self) -> None:
        self._nutrition.back_to_calendar()

    def _calendar_step_back(self) -> None:
        self._calendar.previous_month()

    def _calendar_step_forward(self) -> None:
        self._calendar.next_month()

    def _calendar_today(self) -> None:
        self._calendar.go_today()

    def _calendar_open_day(self) -> None:
        self._calendar.request_selected_day()

    def show_selected_activity_detail(self) -> None:
        """Open the detail of the activity selected in the table."""
        self._activities.request_detail()

    # --- Profili -------------------------------------------------------------------------

    def show_new_profile(self, form: NewProfileForm | None = None) -> None:
        """Open the "New profile" dialog (no effect if one is already open)."""
        if self._new_dialog is not None or not self._controller.can_change_profile:
            return
        if form is None:
            form = NewProfileForm(self._defaults.timezone_name)
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

    def _choose_new_profile_folder(self) -> str:
        """Ask for the profile folder, starting from the last one used (or from Documents)."""
        start = str(self._settings.value(_KEY_LAST_PROFILE_FOLDER, "", type=str))
        if not start:
            start = str(self._defaults.documents_folder)
        chosen = self._defaults.pick_folder(self, self.tr("Choose the profile folder"), start)
        if chosen:
            self._settings.setValue(_KEY_LAST_PROFILE_FOLDER, str(Path(chosen).parent))
        return chosen

    def choose_profile_folder(self) -> None:
        """Ask for a folder with a profile and open it."""
        start = str(self._settings.value(_KEY_LAST_PROFILE_FOLDER, "", type=str))
        chosen = QFileDialog.getExistingDirectory(self, self.tr("Open profile from folder"), start)
        if chosen:
            self.open_profile_folder(Path(chosen))

    def open_profile_folder(self, folder: Path) -> None:
        """Remember the folder and open the profile it contains."""
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
        self._nutrition.profile_changed()
        self._threshold.profile_changed()
        self._plan.profile_changed()
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
            count_text = self.tr("Activities that will be deleted: %n.", "", preview.activities)
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
        """Ask for the files to import (starting from the last folder used)."""
        start = str(self._settings.value(_KEY_LAST_FILES_FOLDER, "", type=str))
        activity_files = self.tr("Activity files (*.fit *.gpx *.zip)")
        all_files = self.tr("All files (*)")
        chosen, _ = QFileDialog.getOpenFileNames(
            self, self.tr("Import files"), start, f"{activity_files};;{all_files}"
        )
        if chosen:
            self.import_files([Path(name) for name in chosen])

    def choose_import_folder(self) -> None:
        """Ask for the folder to import (starting from the last one used)."""
        start = str(self._settings.value(_KEY_LAST_FOLDER, "", type=str))
        chosen = QFileDialog.getExistingDirectory(self, self.tr("Import from folder"), start)
        if chosen:
            self.import_folder(Path(chosen))

    def import_files(self, paths: Sequence[Path]) -> None:
        """Import the files and remember their folder."""
        if paths and self._controller.import_files(paths):
            self._settings.setValue(_KEY_LAST_FILES_FOLDER, str(paths[0].parent))

    def import_folder(self, folder: Path) -> None:
        """Import the folder and remember it."""
        if self._controller.import_folder(folder):
            self._settings.setValue(_KEY_LAST_FOLDER, str(folder))

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802 - Qt
        """Accept dragged files only if an import is possible."""
        if self._controller.can_import and event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802 - Qt
        """Import the files (or the folder) dropped on the window."""
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
            self.tr("Files imported: {done} of %n", "", progress.total).format(done=progress.done)
        )
        self._import_bar.setRange(0, progress.total)
        self._import_bar.setValue(progress.done)

    def _on_import_finished(self, report: ImportReport) -> None:
        self.statusBar().showMessage(
            self.tr("Activities imported: %n", "", report.imported), _STATUS_MESSAGE_MS
        )
        dialog = ImportReportDialog(report, self)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self._remember(dialog)
        dialog.open()

    # --- Errors and messages ---------------------------------------------------------------

    def _show_error(self, message: ErrorMessage, name: str = "errorMessage") -> None:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle(message.title)
        box.setTextFormat(Qt.TextFormat.PlainText)
        box.setText(message.title)
        box.setInformativeText(f"{message.what}\n\n{message.remedy}")
        box.setStandardButtons(QMessageBox.StandardButton.Ok)
        box.setObjectName(name)
        self._remember(box)
        box.open()

    def show_unexpected_error(self) -> None:
        """Generic message of the last-resort handler (only one at a time)."""
        if any(b.objectName() == "unexpectedError" and b.isVisible() for b in self._boxes):
            return
        self._show_error(unexpected_error_message(), "unexpectedError")

    def open_log_folder(self) -> None:
        """Open the log folder with the system file manager."""
        folder = self._log_folder
        if folder is None:
            return
        try:
            folder.mkdir(parents=True, exist_ok=True)
            opened = QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))
        except OSError:
            opened = False
        if not opened:
            # {folder}: where the log files are (a folder of the application).
            where = self.tr("The log files are in: {folder}").format(folder=folder)
            self._show_error(
                ErrorMessage(
                    self.tr("The log folder cannot be opened"),
                    self.tr("Ponos Coach could not open the folder with the log files."),
                    where,
                ),
                "logFolderError",
            )

    def _on_operation_failed(self, message: ErrorMessage) -> None:
        self._show_error(message)
        if self._creating_form is not None:
            form, self._creating_form = self._creating_form, None
            self.show_new_profile(form)  # the typed data is not lost

    def _remember(self, box: QMessageBox | ImportReportDialog) -> None:
        self._boxes = [item for item in self._boxes if item.isVisible()]
        self._boxes.append(box)

    def show_about(self) -> None:
        """About: version, licence and notice that it is not a medical device."""
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
        page = self._section_pages.get(row)
        if page is not None:
            self._pages.setCurrentWidget(page)
            self._settings.setValue(_KEY_SECTION, row)

    def _restore_section(self) -> None:
        """Reopen the section of last time; an invalid saved value means the first one."""
        try:
            row = int(str(self._settings.value(_KEY_SECTION)))
        except ValueError:
            row = _SECTION_OVERVIEW
        self._sidebar.setCurrentRow(row if row in _AVAILABLE_SECTIONS else _SECTION_OVERVIEW)

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
            self._calendar.show_data(self._today(), state.overview.activities, system_theme())
            self._nutrition.data_changed()
            self._threshold.data_changed()
        elif isinstance(state, Idle):
            self._activities.show_activities((), self._zone)
            self._calendar.show_data(self._today(), (), system_theme())
        self._update_status_summary(state)

    def _on_color_scheme_changed(self) -> None:
        self._overview.apply_state(self._controller.overview_state, system_theme(), self._zone)
        self._calendar.apply_theme(system_theme())
        self._plan.theme_changed()

    def _unavailable_reason(self, allowed: bool) -> str:
        """The reason, for the tooltip, why a command on the profile cannot be given now."""
        if allowed:
            return ""
        if self._controller.profile is None:
            return self.tr("Create or open a profile first.")
        return self.tr("Wait for the current operation to finish, or stop it first.")

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
        self._update_profile_data_actions()
        self._stop_action.setEnabled(controller.can_stop)
        self._stop_action.setToolTip(
            "" if controller.can_stop else self.tr("There is nothing to stop.")
        )

    def _update_profile_data_actions(self) -> None:
        """Import, edit profile, weights and detail: each with its reason if it cannot be done."""
        controller = self._controller
        for action, allowed in (
            (self._import_files_action, controller.can_import),
            (self._import_folder_action, controller.can_import),
            (self._edit_profile_action, controller.can_edit_profile),
            (self._sport_weights_action, controller.can_edit_profile),
            (self._nutrition_action, controller.profile is not None),
        ):
            action.setEnabled(allowed)
            action.setToolTip(self._unavailable_reason(allowed))
        self._update_calendar_actions()
        self._update_threshold_actions()
        has_selection = self._activities.selected_activity() is not None
        self._detail_action.setEnabled(has_selection)
        self._detail_action.setToolTip(
            "" if has_selection else self.tr("Select an activity in the Activities list first.")
        )

    def _threshold_available(self) -> bool:
        """The threshold test needs an open profile and a window that is not closing."""
        return self._controller.profile is not None and not self._controller.is_closing

    def _update_threshold_actions(self) -> None:
        """Use as threshold test and Threshold tests, each with its reason if it cannot be
        done."""
        available = self._threshold_available()
        if available:
            reason = ""
        elif self._controller.profile is None:
            reason = self._unavailable_reason(False)
        else:
            reason = self.tr("Ponos Coach is closing.")
        self._threshold_history_action.setEnabled(available)
        self._threshold_history_action.setToolTip(reason)
        selected = self._activities.selected_activity()
        if not available:
            allowed, why = False, reason
        elif selected is None:
            allowed, why = False, self.tr("Select an activity in the Activities list first.")
        elif not can_be_threshold_test(selected.sport):
            allowed, why = False, not_a_run_reason()
        else:
            allowed, why = True, ""
        self._use_as_test_action.setEnabled(allowed)
        self._use_as_test_action.setToolTip(why)

    def _update_calendar_actions(self) -> None:
        """Calendar commands work on the Calendar page; the day page only has "Back"."""
        on_calendar = self._pages.currentWidget() is self._calendar
        on_day = self._pages.currentWidget() is self._day_page
        not_here = self.tr("Open the Calendar section first.")
        for action in (self._previous_month_action, self._today_action):
            action.setEnabled(on_calendar)
            action.setToolTip("" if on_calendar else not_here)
        selected = self._calendar.selected_cell()
        can_open = on_calendar and selected is not None and selected.can_open
        self._open_day_action.setEnabled(can_open)
        if can_open:
            open_reason = ""
        elif on_calendar:
            open_reason = self.tr("Select a day up to today, or a day of the plan, first.")
        else:
            open_reason = not_here
        self._open_day_action.setToolTip(open_reason)
        can_go_forward = on_calendar and self._calendar.next_button.isEnabled()
        self._next_month_action.setEnabled(can_go_forward)
        self._next_month_action.setToolTip(
            "" if can_go_forward else self._calendar.next_button.toolTip() or not_here
        )
        self._back_action.setEnabled(on_day)
        self._back_action.setToolTip("" if on_day else self.tr("No day is open."))

    def _update_status_summary(self, state: OverviewState) -> None:
        summary = summary_of(state)
        labels = (self._activities_label, self._excluded_label, self._days_label)
        for label in labels:
            label.setVisible(summary is not None)
        if summary is None or not isinstance(state, Ready):
            return
        self._activities_label.setText(self.tr("Activities: %n", "", summary.activities))
        self._excluded_label.setText(self.tr("Excluded activities: %n", "", summary.excluded))
        self._excluded_label.setVisible(summary.excluded > 0)
        self._days_label.setText(self.tr("Days in the chart: %n", "", summary.pmc_days))
        self._excluded_label.setToolTip(self._excluded_tooltip(state.overview))

    def _excluded_tooltip(self, overview: ProfileOverview) -> str:
        """Why activities are excluded: one reason per line, with the count."""
        counts = Counter(item.reason for item in overview.excluded)
        lines = [
            f"{html.escape(exclusion_reason_text(reason))}: {count}"
            for reason, count in counts.items()
        ]
        return "<br>".join(lines)

    # --- Chiusura ------------------------------------------------------------------------

    def _restore_geometry(self) -> None:
        """Restore the saved geometry; a value that is not a valid byte block means
        the default size (settings edited by hand or damaged)."""
        saved = self._settings.value(_KEY_GEOMETRY)
        if isinstance(saved, QByteArray | bytes | bytearray) and self.restoreGeometry(saved):
            return
        self.resize(*_DEFAULT_SIZE)

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802 - Qt
        """After the first `show()` start the web engine: the window appears first."""
        super().showEvent(event)
        if not self._engine_requested:
            self._engine_requested = True
            self._engine_timer.start(0)

    def _start_web_engine(self) -> None:
        """Start the three web pages right after the first `show()`, not "on first need".

        Measured (offscreen, cold Chromium): the first start costs about 185 ms on the
        UI thread, each additional view about 3 ms. Deferring it to the first draw or to the start
        of the
        computation would move 185 ms into the import and the drawing, beyond the 100 ms limit per
        event (T-UI-02); deferring only the two secondary views saves 6. Here the user does not
        have an action in progress yet.
        """
        self._overview.start_engines()

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 - Qt
        """Two-step close, without blocking the UI thread.

        The first step cancels the jobs and asks the data thread to close the profile; the
        window stays open and a timer checks whether the thread has finished. At the second step
        (thread finished) it really closes. Beyond the wait cap it warns and leaves the window
        open: a running thread is never destroyed.

        Dialogs and the web engine (pages, then profile) are released already at the first step, at
        once:
        the window can be destroyed at any time after the first `close()` (`qtbot` does
        so too), and Qt destroys children in the order they were born, not the one that
        the web engine needs. If the wait expires the charts stay empty.
        """
        if not self._closed:
            self._remember_window_state()
            self._controller.begin_shutdown()
            self._dialogs.close_all()
            self._nutrition.close_dialogs()
            self._threshold.close_dialogs()
            self._plan.close_dialogs()
            self.release_web_engine()
            if not self._controller.has_stopped:
                event.ignore()
                self._wait_for_shutdown()
                return
            self._finish_closing()
        super().closeEvent(event)

    def _remember_window_state(self) -> None:
        self._settings.setValue(_KEY_GEOMETRY, self.saveGeometry())
        self._settings.sync()

    def _wait_for_shutdown(self) -> None:
        if self._shutdown_timer.isActive():
            return
        self._shutdown_deadline = time.monotonic() + self.shutdown_wait_ms / 1000
        self.statusBar().showMessage(self.tr("Closing the profile..."))
        self._shutdown_timer.start()

    def _poll_shutdown(self) -> None:
        if self._controller.has_stopped:
            self._shutdown_timer.stop()
            self._finish_closing()
            self.close()  # now closing is complete
        elif time.monotonic() >= self._shutdown_deadline:
            self._shutdown_timer.stop()
            self.statusBar().clearMessage()
            self._show_error(self._not_closed_message(), "shutdownTimeout")

    def _not_closed_message(self) -> ErrorMessage:
        return ErrorMessage(
            self.tr("Ponos Coach is still closing the profile"),
            self.tr("The profile is still being saved, so the window stays open to protect it."),
            self.tr("Wait a moment, then close the window again."),
        )

    def release_web_engine(self) -> None:
        """Destroy the web engine's pages and profile (idempotent): when the window closes
        and from `aboutToQuit`, before Qt destroys the objects."""
        self._engine.dispose()

    def _finish_closing(self) -> None:
        self.statusBar().clearMessage()
        self._closed = True
