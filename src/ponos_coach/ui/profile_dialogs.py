# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""The profile data in the window: the Settings > Profile and > Sport weights pages, and the
activity detail windows.

Every activity opened gets its own detail window with its own loader, so two activities can be
compared side by side; opening an activity whose window is already open brings that window forward.

Each settings page is filled when the data thread has read what it shows (measurement history,
weights): the UI never reads the profile. After a save the page is built again from what was
saved; if the use case rejects the measurements, the problem appears on the right field and the
page keeps the typed data.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import date
from functools import partial
from zoneinfo import ZoneInfo

from PySide6.QtCore import QCoreApplication, QObject, QPoint, Qt, Signal
from PySide6.QtWidgets import QWidget

from ponos_coach.activities.domain import ActivityIdentity, Sport
from ponos_coach.load.domain import SportWeight
from ponos_coach.profiles.measurements import MeasurementHistory
from ponos_coach.ui.activity_detail_dialog import ActivityDetailDialog
from ponos_coach.ui.activity_detail_loader import ActivityDetailLoader
from ponos_coach.ui.chart_texts import chart_chrome
from ponos_coach.ui.chart_view import ChartEngine
from ponos_coach.ui.errors import ErrorMessage
from ponos_coach.ui.hr_figure import HeartRateLabels
from ponos_coach.ui.measurement_texts import saved_period_text
from ponos_coach.ui.measurements_form import (
    EditIssue,
    MeasurementSeries,
    MeasurementsForm,
    MeasurementsRequest,
)
from ponos_coach.ui.profile_edit_dialog import ProfileEditDialog
from ponos_coach.ui.profile_editor import ProfileEditor, SavedEdit
from ponos_coach.ui.settings_page import SettingsCategory, SettingsPage
from ponos_coach.ui.sport_weights_dialog import SportWeightsDialog
from ponos_coach.ui.sport_weights_form import SportWeightsForm
from ponos_coach.ui.tokens import Theme, tokens_for

_CASCADE_PX = 32


def _bring_forward(dialog: QWidget) -> None:
    dialog.show()
    dialog.raise_()
    dialog.activateWindow()


class ProfileDialogs(QObject):
    """Own the profile data pages and windows and connect them to the data thread jobs."""

    threshold_test_requested = Signal(object)  # ActivityIdentity, from the detail window

    def __init__(  # noqa: PLR0913 - each argument is a dependency of the parent window
        self,
        window: QWidget,
        editor: ProfileEditor,
        new_loader: Callable[[QObject], ActivityDetailLoader],
        engine: ChartEngine,
        *,
        theme: Callable[[], Theme],
        today: Callable[[], date],
        zone: Callable[[], ZoneInfo],
        profile_name: Callable[[], str],
        show_error: Callable[[ErrorMessage], None],
        notify: Callable[[str], None],
        profile_page: SettingsPage,
        weights_page: SettingsPage,
        show_settings: Callable[[SettingsCategory], None],
        unavailable_reason: Callable[[], str],
    ) -> None:
        super().__init__(window)
        self._window = window
        self._editor = editor
        self._new_loader = new_loader
        self._engine = engine
        self._theme = theme
        self._today = today
        self._zone = zone
        self._profile_name = profile_name
        self._show_error = show_error
        self._notify = notify
        self._profile_page = profile_page
        self._weights_page = weights_page
        self._show_settings = show_settings
        self._unavailable_reason = unavailable_reason
        self._saving_weights: dict[Sport, SportWeight] = {}
        # open detail windows, oldest first, each with the loader that fills it
        self._details: dict[
            ActivityIdentity, tuple[ActivityDetailDialog, ActivityDetailLoader]
        ] = {}
        self._waiting_for_edit = False
        self._waiting_for_weights = False
        editor.measurements_loaded.connect(self._on_measurements_loaded)
        editor.measurements_saved.connect(self._on_measurements_saved)
        editor.measurements_rejected.connect(self._on_measurements_rejected)
        editor.sport_weights_loaded.connect(self._on_weights_loaded)
        editor.sport_weights_saved.connect(self._on_weights_saved)
        editor.failed.connect(self._on_failed)
        profile_page.shown.connect(self.load_profile_page)
        weights_page.shown.connect(self.load_weights_page)

    # --- Test accessors ------

    @property
    def edit_dialog(self) -> ProfileEditDialog | None:
        """The content of the Profile page, once it has been read."""
        form = self._profile_page.form
        return form if isinstance(form, ProfileEditDialog) else None

    @property
    def weights_dialog(self) -> SportWeightsDialog | None:
        """The content of the Sport weights page, once it has been read."""
        form = self._weights_page.form
        return form if isinstance(form, SportWeightsDialog) else None

    @property
    def detail_dialog(self) -> ActivityDetailDialog | None:
        """The "Activity detail" window opened last and still open, if any."""
        if not self._details:
            return None
        return next(reversed(self._details.values()))[0]

    @property
    def detail_dialogs(self) -> tuple[ActivityDetailDialog, ...]:
        """Every open "Activity detail" window, oldest first."""
        return tuple(dialog for dialog, _loader in self._details.values())

    def close_all(self) -> None:
        """Close the open detail windows (the window closes or the profile changes): the web pages
        are released and their loaders cancelled."""
        for dialog in self.detail_dialogs:
            dialog.close()

    # --- Settings > Profile ------

    def edit_profile(self) -> None:
        """Open Settings on the Profile page."""
        self._show_settings(SettingsCategory.PROFILE)

    def load_profile_page(self) -> None:
        """Read the history and fill the Profile page. Edits not saved yet are never replaced."""
        page = self._profile_page
        if page.is_dirty or self._waiting_for_edit:
            return
        reason = self._unavailable_reason()
        if reason:
            page.show_notice(reason)
            return
        self._waiting_for_edit = self._editor.load_measurements()

    def _on_measurements_loaded(self, history: object) -> None:
        self._waiting_for_edit = False
        if isinstance(history, MeasurementHistory) and not self._profile_page.is_dirty:
            self._show_profile_form(history)

    def _show_profile_form(self, history: MeasurementHistory) -> None:
        form = MeasurementsForm(history, self._today(), self._profile_name())
        dialog = ProfileEditDialog(form, history, self._theme())
        dialog.save_requested.connect(self._on_save_measurements)
        self._profile_page.show_form(dialog)

    def _on_save_measurements(self, request: object) -> None:
        if isinstance(request, MeasurementsRequest) and not self._editor.save_measurements(request):
            self._not_now()

    def _on_measurements_saved(self, edit: object) -> None:
        if not isinstance(edit, SavedEdit):
            return
        self._show_profile_form(edit.history)
        periods = [
            saved_period_text(series, period)
            for series, period in (
                (MeasurementSeries.WEIGHT, None if edit.saved is None else edit.saved.weight),
                (
                    MeasurementSeries.HEART_RATE,
                    None if edit.saved is None else edit.saved.heart_rate,
                ),
                (
                    MeasurementSeries.THRESHOLD_PACE,
                    None if edit.saved is None else edit.saved.threshold_pace,
                ),
            )
            if period is not None
        ]
        recalculating = (
            [QCoreApplication.translate("ProfileDialogs", "The load is being recalculated.")]
            if edit.saved is not None
            else []
        )
        self._notify(
            " ".join(
                [
                    QCoreApplication.translate("ProfileDialogs", "Profile updated."),
                    *periods,
                    *recalculating,
                ]
            )
        )

    def _on_measurements_rejected(self, issue: object) -> None:
        dialog = self.edit_dialog
        if dialog is not None and isinstance(issue, EditIssue):
            dialog.show_rejection(issue)

    # --- Settings > Sport weights ------

    def edit_sport_weights(self) -> None:
        """Open Settings on the Sport weights page."""
        self._show_settings(SettingsCategory.SPORT_WEIGHTS)

    def load_weights_page(self) -> None:
        """Read the weights and fill the Sport weights page. Edits not saved yet are never
        replaced."""
        page = self._weights_page
        if page.is_dirty or self._waiting_for_weights:
            return
        reason = self._unavailable_reason()
        if reason:
            page.show_notice(reason)
            return
        self._waiting_for_weights = self._editor.load_sport_weights()

    def _on_weights_loaded(self, weights: object) -> None:
        self._waiting_for_weights = False
        if not isinstance(weights, Mapping) or self._weights_page.is_dirty:
            return
        self._show_weights_form(_weights_of(weights))

    def _show_weights_form(self, current: Mapping[Sport, SportWeight]) -> None:
        dialog = SportWeightsDialog(SportWeightsForm(current))
        dialog.save_requested.connect(self._on_save_weights)
        self._weights_page.show_form(dialog)

    def _on_save_weights(self, weights: object) -> None:
        if not isinstance(weights, Mapping):
            return
        self._saving_weights = _weights_of(weights)
        if not self._editor.save_sport_weights(self._saving_weights):
            self._not_now()

    def _on_weights_saved(self) -> None:
        if self._saving_weights:
            self._show_weights_form(self._saving_weights)
            self._saving_weights = {}
            self._notify(
                QCoreApplication.translate(
                    "ProfileDialogs", "Sport weights saved. The load is being recalculated."
                )
            )

    # --- The profile changes or becomes busy ------

    def profile_changed(self) -> None:
        """Another profile opened: what the pages show belongs to the old one."""
        self._waiting_for_edit = False
        self._waiting_for_weights = False
        self.close_all()  # health data and a GPS track of the old profile must not stay open
        for page, load in (
            (self._profile_page, self.load_profile_page),
            (self._weights_page, self.load_weights_page),
        ):
            page.show_notice("")
            if page.isVisible():
                load()

    def availability_changed(self) -> None:
        """Whether the profile can be edited now changed: a page that could not be filled
        tries again."""
        for page, load in (
            (self._profile_page, self.load_profile_page),
            (self._weights_page, self.load_weights_page),
        ):
            if page.form is None and page.isVisible():
                load()

    # --- Activity detail ------

    def show_activity_detail(self, identity: ActivityIdentity) -> None:
        """Open a detail window for the activity and start loading it; if the activity already
        has its window, bring that one forward."""
        known = self._details.get(identity)
        if known is not None:
            _bring_forward(known[0])
            return
        dialog = ActivityDetailDialog(self._engine, self._zone(), self._window, theme=self._theme)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        loader = self._new_loader(dialog)
        loader.loaded.connect(dialog.show_outcome_of)
        loader.failed.connect(dialog.show_error_of)
        dialog.finished.connect(partial(self._on_detail_closed, identity))
        dialog.threshold_test_requested.connect(self.threshold_test_requested)
        self._cascade(dialog)
        self._details[identity] = (dialog, loader)
        _bring_forward(dialog)
        labels = HeartRateLabels(
            x_axis=QCoreApplication.translate("ProfileDialogs", "Time"),
            y_axis=QCoreApplication.translate("ProfileDialogs", "Heart rate"),
            heart_rate=QCoreApplication.translate("ProfileDialogs", "Heart rate"),
            discarded=QCoreApplication.translate("ProfileDialogs", "Samples outside your limits"),
            chrome=chart_chrome(),
        )
        if not loader.load(identity, labels, tokens_for(self._theme())):
            dialog.close()

    def _cascade(self, dialog: ActivityDetailDialog) -> None:
        """A new window sits a little below and to the right of the last one, so none hides
        another completely."""
        last = self.detail_dialog
        if last is not None:
            dialog.move(last.pos() + QPoint(_CASCADE_PX, _CASCADE_PX))

    def _on_detail_closed(self, identity: ActivityIdentity, _result: int = 0) -> None:
        known = self._details.pop(identity, None)
        if known is not None:
            known[1].cancel()

    # --- Errori ------

    def _not_now(self) -> None:
        """Saving did not start (the profile is busy): the typed data stays."""
        self._unlock_all()
        self._notify(
            QCoreApplication.translate(
                "ProfileDialogs", "The profile is busy. Save again when the operation has finished."
            )
        )

    def _unlock_all(self) -> None:
        for page in (self.edit_dialog, self.weights_dialog):
            if page is not None:
                page.set_busy(False)

    def _on_failed(self, message: object) -> None:
        """A job failed: the pages become editable again and the error is shown."""
        self._waiting_for_edit = False
        self._waiting_for_weights = False
        self._unlock_all()
        if isinstance(message, ErrorMessage):
            self._show_error(message)


def _weights_of(weights: Mapping[object, object]) -> dict[Sport, SportWeight]:
    """The sport weights in a mapping that came through a signal (typed `object`)."""
    return {
        sport: value
        for sport, value in weights.items()
        if isinstance(sport, Sport) and isinstance(value, SportWeight)
    }
