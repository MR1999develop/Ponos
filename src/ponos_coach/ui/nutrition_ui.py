# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Nutrition in the window: the day page flow and the settings dialog.

Connects the calendar, the day page and the "Nutrition settings" dialog to the jobs of
`NutritionLoader`. A day opens at once in the "calculating" state and fills in when the data thread
answers; leaving the day (or opening another) drops the answer that is on its way. After a settings
change the calendar's goal colours and the open day are refreshed. The profile is read only on the
data thread.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date

from PySide6.QtCore import QObject, Qt
from PySide6.QtWidgets import QWidget

from ponos_coach.nutrition.use_cases import NutritionSettings
from ponos_coach.ui.calendar_page import CalendarPage
from ponos_coach.ui.day_page import DayPage, DayState
from ponos_coach.ui.day_presentation import day_title, present_day
from ponos_coach.ui.errors import ErrorMessage
from ponos_coach.ui.nutrition_loader import DayLoaded, NutritionLoader
from ponos_coach.ui.nutrition_settings_dialog import NutritionSettingsDialog
from ponos_coach.ui.nutrition_texts import fixed_notice_text
from ponos_coach.ui.workspace import WorkspaceController


class NutritionUi(QObject):
    """Owns the nutrition flow of the window; the window only gives it pages and callbacks."""

    def __init__(  # noqa: PLR0913 - each argument is a piece of the window it works with
        self,
        window: QWidget,
        controller: WorkspaceController,
        calendar: CalendarPage,
        day_page: DayPage,
        *,
        today: Callable[[], date],
        show_day_page: Callable[[], None],
        show_calendar_page: Callable[[], None],
        show_error: Callable[[ErrorMessage], None],
    ) -> None:
        super().__init__(window)
        self._window = window
        self._controller = controller
        self._calendar = calendar
        self._day_page = day_page
        self._today = today
        self._show_day_page = show_day_page
        self._show_calendar_page = show_calendar_page
        self._show_error = show_error
        self._loader = NutritionLoader(controller, controller.foods, self)
        self._day: date | None = None
        self._dialog: NutritionSettingsDialog | None = None
        self._waiting_for_settings = False
        self._loader.day_loaded.connect(self._on_day_loaded)
        self._loader.settings_loaded.connect(self._on_settings_loaded)
        self._loader.settings_changed.connect(self._on_settings_changed)
        self._loader.command_failed.connect(self._on_command_failed)
        self._loader.failed.connect(self._on_failed)
        calendar.day_requested.connect(self._on_day_requested)

    # --- Test accessors --------------------------------------------------------------------

    @property
    def loader(self) -> NutritionLoader:
        """The nutrition jobs."""
        return self._loader

    @property
    def settings_dialog(self) -> NutritionSettingsDialog | None:
        """The open "Nutrition settings" dialog, if any."""
        return self._dialog

    @property
    def current_day(self) -> date | None:
        """The day the day page is showing, if it is open."""
        return self._day

    @property
    def is_day_open(self) -> bool:
        """True while the day page is the page shown."""
        return self._day is not None

    # --- A day -------------------------------------------------------------------------------

    def _on_day_requested(self, day: object) -> None:
        if isinstance(day, date):
            self.open_day(day)

    def open_day(self, day: date) -> None:
        """Open the page of a past day and start calculating its guidance."""
        if self._controller.profile is None:
            return
        self._day = day
        self._day_page.show_loading(day_title(day), fixed_notice_text())
        self._show_day_page()
        if not self._loader.load_day(day):
            self.back_to_calendar()

    def back_to_calendar(self) -> None:
        """Leave the day page and show the month again."""
        self._loader.cancel_day()
        self._day = None
        self._show_calendar_page()

    def reload_day(self) -> None:
        """Calculate the open day again (the data it depends on changed)."""
        if self._day is not None and self._controller.profile is not None:
            self._loader.load_day(self._day)

    def _on_day_loaded(self, loaded: object) -> None:
        if isinstance(loaded, DayLoaded) and loaded.day == self._day:
            self._day_page.show_content(
                present_day(
                    loaded.day,
                    loaded.result,
                    today=self._today(),
                    foods_verified=loaded.foods_verified,
                )
            )

    # --- Profile and data changes ------------------------------------------------------------

    def profile_changed(self) -> None:
        """Another profile opened: forget the day and the dialog, read the new goal periods."""
        was_open = self._day is not None
        self._loader.cancel_day()
        self._day = None
        self._day_page.clear()
        if self._dialog is not None:
            self._dialog.close()
        if was_open:
            self._show_calendar_page()
        self.refresh_periods()

    def refresh_periods(self) -> None:
        """Read the goal periods for the calendar's colours."""
        self._loader.load_settings()

    def data_changed(self) -> None:
        """Activities or the profile's data changed: the open day is calculated again."""
        self.reload_day()

    def close_dialogs(self) -> None:
        """Close the settings dialog (the window is closing)."""
        if self._dialog is not None:
            self._dialog.close()

    # --- Settings ----------------------------------------------------------------------------

    def show_settings(self) -> None:
        """Read the settings and open "Nutrition settings" (no effect if already open)."""
        if self._dialog is not None:
            self._dialog.raise_()
            self._dialog.activateWindow()
            return
        if self._waiting_for_settings or self._controller.profile is None:
            return
        self._waiting_for_settings = self._loader.load_settings()

    def _on_settings_loaded(self, settings: object) -> None:
        if not isinstance(settings, NutritionSettings):
            return
        self._calendar.set_periods(settings.goal_periods)
        if self._dialog is not None:
            self._dialog.apply(settings)
        elif self._waiting_for_settings:
            self._waiting_for_settings = False
            self._open_dialog(settings)

    def _open_dialog(self, settings: NutritionSettings) -> None:
        dialog = NutritionSettingsDialog(settings, self._loader, self._today(), self._window)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.finished.connect(self._on_dialog_closed)
        self._dialog = dialog
        dialog.open()

    def _on_dialog_closed(self) -> None:
        self._dialog = None

    def _on_settings_changed(self, _settings: object) -> None:
        self.reload_day()

    def _on_command_failed(self, message: object) -> None:
        if not isinstance(message, ErrorMessage):
            return
        if self._dialog is not None:
            self._dialog.show_failure(message)
        else:
            self._show_error(message)
        self.refresh_periods()  # the dialog and the calendar show what is really stored

    def _on_failed(self, message: object) -> None:
        if not isinstance(message, ErrorMessage):
            return
        if self._waiting_for_settings:
            self._waiting_for_settings = False
            self._show_error(message)
        elif self._day is not None and self._day_page.state is DayState.LOADING:
            self._day_page.show_failure(message)
        else:
            self._show_error(message)
