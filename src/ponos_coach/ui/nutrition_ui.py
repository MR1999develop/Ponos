# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Nutrition in the window: the day windows and the Settings > Nutrition page.

Connects the calendar, the windows of the days and the "Nutrition" settings page to the jobs of
`NutritionLoader`. Every day clicked in the calendar opens a window of its own, in the "calculating"
state, and fills in when the data thread answers; a day already open comes forward instead of
opening twice. Closing a window drops the answer that is on its way for that day only. After a data
or settings change every open day is calculated again and the calendar's goal colours are
refreshed; another profile closes every day window and empties the page. The profile is read only
on the data thread.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from functools import partial

from PySide6.QtCore import QObject, QPoint, Qt
from PySide6.QtWidgets import QWidget

from ponos_coach.activities.domain import ActivityIdentity
from ponos_coach.nutrition.domain import LeanMassEntry
from ponos_coach.nutrition.use_cases import NutritionSettings
from ponos_coach.ui.calendar_page import CalendarPage
from ponos_coach.ui.day_page import DayState
from ponos_coach.ui.day_presentation import day_title, present_day
from ponos_coach.ui.day_window import DayWindow
from ponos_coach.ui.errors import ErrorMessage
from ponos_coach.ui.nutrition_loader import (
    DayFailed,
    DayLoaded,
    LeanMassApplied,
    NutritionLoader,
)
from ponos_coach.ui.nutrition_settings_dialog import NutritionSettingsDialog
from ponos_coach.ui.nutrition_texts import fixed_notice_text
from ponos_coach.ui.settings_page import SettingsPage
from ponos_coach.ui.workspace import WorkspaceController

_CASCADE_PX = 32
_CASCADE_STEPS = 8  # after this many windows the cascade starts again from the first position


def _bring_forward(window: QWidget) -> None:
    window.show()
    window.raise_()
    window.activateWindow()


class NutritionUi(QObject):
    """Owns the nutrition flow of the window; the window gives it the calendar and callbacks."""

    def __init__(  # noqa: PLR0913 - each argument is a piece of the window it works with
        self,
        window: QWidget,
        controller: WorkspaceController,
        calendar: CalendarPage,
        *,
        today: Callable[[], date],
        edit_profile: Callable[[], None],
        show_activity: Callable[[ActivityIdentity], None],
        show_error: Callable[[ErrorMessage], None],
        settings_page: SettingsPage,
        show_settings: Callable[[], None],
        no_profile_text: Callable[[], str],
    ) -> None:
        super().__init__(window)
        self._window = window
        self._controller = controller
        self._calendar = calendar
        self._today = today
        self._edit_profile = edit_profile
        self._show_activity = show_activity
        self._show_error = show_error
        self._loader = NutritionLoader(controller, controller.foods, self)
        self._days: dict[date, DayWindow] = {}  # the open day windows, oldest first
        self._opened = 0  # windows opened so far, for the cascade
        self._page = settings_page
        self._show_settings = show_settings
        self._no_profile_text = no_profile_text
        self._waiting_for_settings = False
        self._lean_masses: tuple[LeanMassEntry, ...] = ()
        self._loader.day_loaded.connect(self._on_day_loaded)
        self._loader.day_failed.connect(self._on_day_failed)
        self._loader.settings_loaded.connect(self._on_settings_loaded)
        self._loader.settings_changed.connect(self._on_settings_changed)
        self._loader.lean_masses_loaded.connect(self._on_lean_masses_loaded)
        self._loader.lean_mass_applied.connect(self._on_lean_mass_applied)
        self._loader.lean_mass_refused.connect(self._on_lean_mass_refused)
        self._loader.command_failed.connect(self._on_command_failed)
        self._loader.failed.connect(self._on_failed)
        calendar.day_requested.connect(self._on_day_requested)
        settings_page.shown.connect(self.load_settings_page)

    # --- Test accessors --------------------------------------------------------------------

    @property
    def loader(self) -> NutritionLoader:
        """The nutrition jobs."""
        return self._loader

    @property
    def settings_dialog(self) -> NutritionSettingsDialog | None:
        """The content of the Nutrition page, once it has been read."""
        form = self._page.form
        return form if isinstance(form, NutritionSettingsDialog) else None

    @property
    def day_windows(self) -> tuple[DayWindow, ...]:
        """The open day windows, oldest first."""
        return tuple(self._days.values())

    def day_window(self, day: date) -> DayWindow | None:
        """The window of a day, if it is open."""
        return self._days.get(day)

    # --- Days ----------------------------------------------------------------------------------

    def _on_day_requested(self, day: object) -> None:
        if isinstance(day, date):
            self.open_day(day)

    def open_day(self, day: date) -> None:
        """Open the window of a day and start calculating its guidance; a day that is already
        open comes forward (and is calculated again if the last calculation failed)."""
        if self._controller.profile is None:
            return
        known = self._days.get(day)
        if known is not None:
            _bring_forward(known)
            if known.page.state is DayState.FAILED:
                known.page.show_loading(day_title(day), fixed_notice_text())
                if not self._loader.load_day(day):
                    known.close()
            return
        window = DayWindow(day, self._window)
        window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        page = window.page
        page.edit_profile_requested.connect(self._edit_profile)
        page.activity_requested.connect(self._on_activity_requested)
        window.finished.connect(partial(self._on_day_closed, day))
        self._cascade(window)
        self._days[day] = window
        page.show_loading(day_title(day), fixed_notice_text())
        _bring_forward(window)
        if not self._loader.load_day(day):
            window.close()

    def _on_activity_requested(self, identity: object) -> None:
        """An activity of a day window: its own window opens, or comes forward if open."""
        if isinstance(identity, ActivityIdentity):
            self._show_activity(identity)

    def _cascade(self, window: DayWindow) -> None:
        """A new window sits a little below and to the right of the main window's corner, one
        step further each time, so none hides another completely."""
        step = self._opened % _CASCADE_STEPS
        self._opened += 1
        offset = _CASCADE_PX * (step + 1)
        window.move(self._window.pos() + QPoint(offset, offset))

    def _on_day_closed(self, day: date, _result: int = 0) -> None:
        self._days.pop(day, None)
        self._loader.cancel_day(day)

    def close_days(self) -> None:
        """Close every day window."""
        for window in tuple(self._days.values()):
            window.close()

    def reload_days(self) -> None:
        """Calculate the open days again (the data they depend on changed)."""
        if self._controller.profile is None:
            return
        for day in self._days:
            self._loader.load_day(day)

    def _on_day_loaded(self, loaded: object) -> None:
        if not isinstance(loaded, DayLoaded):
            return
        window = self._days.get(loaded.day)
        if window is not None:
            window.page.show_content(
                present_day(
                    loaded.day,
                    loaded.result,
                    today=self._today(),
                    foods_verified=loaded.foods_verified,
                )
            )

    def _on_day_failed(self, failed: object) -> None:
        if not isinstance(failed, DayFailed):
            return
        window = self._days.get(failed.day)
        if window is None:
            return
        if window.page.state is DayState.LOADING:
            window.page.show_failure(failed.message)
        else:
            self._show_error(failed.message)

    # --- Profile and data changes ------------------------------------------------------------

    def profile_changed(self) -> None:
        """Another profile opened: close the days and empty the page, read the new goal periods
        (and fill the page again if it is in view)."""
        self._loader.cancel_days()
        self.close_days()
        self._waiting_for_settings = False
        self._lean_masses = ()
        self._page.show_notice("")
        if self._page.isVisible():
            self.load_settings_page()
        else:
            self.refresh_periods()

    def refresh_periods(self) -> None:
        """Read the goal periods for the calendar's colours."""
        self._loader.load_settings()

    def data_changed(self) -> None:
        """Activities or the profile's data changed: the open days are calculated again."""
        self.reload_days()

    def close_dialogs(self) -> None:
        """Close the day windows (the window is closing)."""
        self.close_days()

    # --- Settings ----------------------------------------------------------------------------

    def show_settings(self) -> None:
        """Open Settings on the Nutrition page."""
        self._show_settings()

    def load_settings_page(self) -> None:
        """Read the settings and fill the Nutrition page (the form in view is brought up to
        date)."""
        if self._controller.profile is None:
            self._page.show_notice(self._no_profile_text())
            return
        if not self._waiting_for_settings:
            self._waiting_for_settings = self._loader.load_settings()

    def _on_settings_loaded(self, settings: object) -> None:
        if not isinstance(settings, NutritionSettings):
            return
        self._calendar.set_periods(settings.goal_periods)
        form = self.settings_dialog
        if form is not None:
            form.apply(settings)
        elif self._waiting_for_settings:
            self._page.show_form(
                NutritionSettingsDialog(
                    settings, self._loader, self._today(), lean_masses=self._lean_masses
                )
            )
        self._waiting_for_settings = False

    def _on_lean_masses_loaded(self, entries: object) -> None:
        if not isinstance(entries, tuple):
            return
        self._lean_masses = entries
        form = self.settings_dialog
        if form is not None:
            form.apply_lean_masses(entries)

    def _on_lean_mass_applied(self, applied: object) -> None:
        form = self.settings_dialog
        if isinstance(applied, LeanMassApplied) and form is not None:
            form.lean_mass_applied(applied)

    def _on_lean_mass_refused(self, message: object) -> None:
        form = self.settings_dialog
        if isinstance(message, ErrorMessage) and form is not None:
            form.show_lean_mass_problem(message)

    def _on_settings_changed(self, _settings: object) -> None:
        self.reload_days()

    def _on_command_failed(self, message: object) -> None:
        if not isinstance(message, ErrorMessage):
            return
        form = self.settings_dialog
        if form is not None:
            form.show_failure(message)
        else:
            self._show_error(message)
        self.refresh_periods()  # the dialog and the calendar show what is really stored

    def _on_failed(self, message: object) -> None:
        if not isinstance(message, ErrorMessage):
            return
        if self._waiting_for_settings:
            self._waiting_for_settings = False
        self._show_error(message)
