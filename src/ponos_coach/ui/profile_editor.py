# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Editing the profile data: name, dated measurements and sport weights, on the data thread.

Each job (reading the history, saving the measurements, reading and saving the weights)
builds the use case inside the data thread with the open profile's connection and
returns the result as a signal. After a save the overview is recomputed. Weight,
HR and fixed data are health data: never in logs or error messages.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass

from PySide6.QtCore import QObject, Signal

from ponos_coach.activities.domain import Sport
from ponos_coach.load.domain import SportWeight
from ponos_coach.load.use_cases import GetSportWeights, SetSportWeights
from ponos_coach.profiles.domain import ProfileName
from ponos_coach.profiles.measurements import (
    InvalidMeasurementsError,
    MeasurementHistory,
    ReadMeasurementHistory,
    SavedMeasurements,
    UpdateMeasurements,
)
from ponos_coach.ui.data_thread import JobFailed, JobOutcome, JobSucceeded
from ponos_coach.ui.measurements_form import MeasurementsRequest, issue_for_rejection
from ponos_coach.ui.workspace import ProfileAccess, WorkspaceController

_LOG = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SavedEdit:
    """Outcome of a save: the re-read history, the effective validity periods (`None` if only
    the name changed) and the new name (`None` if it did not change)."""

    history: MeasurementHistory
    saved: SavedMeasurements | None
    renamed: ProfileName | None = None


def _read_history(access: ProfileAccess) -> MeasurementHistory:
    session = access.session
    return ReadMeasurementHistory(session.fixed_data, session.weights, session.physiology)()


class ProfileEditor(QObject):
    """Jobs that edit the open profile. Outcomes arrive as signals on the UI thread."""

    measurements_loaded = Signal(object)  # MeasurementHistory
    measurements_saved = Signal(object)  # SavedEdit: re-read history and validity periods
    measurements_rejected = Signal(object)  # EditIssue
    sport_weights_loaded = Signal(object)  # Mapping[Sport, SportWeight]
    sport_weights_saved = Signal()
    failed = Signal(object)  # ErrorMessage

    def __init__(self, controller: WorkspaceController, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._controller = controller

    # --- Measurements -----------------------------------------------------------------------

    def load_measurements(self) -> bool:
        """Read fixed data and history. False if the profile cannot be edited now."""
        if not self._controller.can_edit_profile:
            return False
        return self._controller.run_on_profile(_read_history, self._after_load_measurements)

    def save_measurements(self, request: MeasurementsRequest) -> bool:
        """Save the dated measurements and/or complete the fixed data, then re-read the history."""
        if not self._controller.can_edit_profile:
            return False

        def work(access: ProfileAccess) -> SavedEdit:
            session = access.session
            saved: SavedMeasurements | None = None
            if request.update is not None or request.fixed is not None:
                saved = UpdateMeasurements(
                    session.activities,
                    session.physiology,
                    session.weights,
                    access.today,
                    session.fixed_data,
                )(request.update, request.fixed)
            renamed: ProfileName | None = None
            if request.new_name is not None:  # after the measurements: they can be rejected
                renamed = self._rename(access, request.new_name)
            return SavedEdit(_read_history(access), saved, renamed)

        return self._controller.run_on_profile(work, self._after_save_measurements)

    @staticmethod
    def _rename(access: ProfileAccess, name: ProfileName) -> ProfileName | None:
        """Renames the profile; the new name, or `None` if it already was that."""
        if name == access.session.info.name:
            return None
        result = access.rename(name.value)
        if not result.registry_updated:
            _LOG.warning("The profile list kept the old name: it will realign at the next opening")
        return result.name

    def _after_load_measurements(self, outcome: JobOutcome) -> None:
        if isinstance(outcome, JobSucceeded) and isinstance(outcome.value, MeasurementHistory):
            self.measurements_loaded.emit(outcome.value)
        elif isinstance(outcome, JobFailed):
            self.failed.emit(self._controller.describe_failure(outcome.error))

    def _after_save_measurements(self, outcome: JobOutcome) -> None:
        if isinstance(outcome, JobSucceeded) and isinstance(outcome.value, SavedEdit):
            if outcome.value.renamed is not None:
                self._controller.profile_renamed(outcome.value.renamed)
            self.measurements_saved.emit(outcome.value)
            if outcome.value.saved is not None:
                self._controller.request_overview_refresh()
        elif isinstance(outcome, JobFailed):
            error = outcome.error
            if isinstance(error, InvalidMeasurementsError):
                self.measurements_rejected.emit(issue_for_rejection(error.problem))
            else:
                self.failed.emit(self._controller.describe_failure(error))

    # --- Sport weights -------------------------------------------------------------

    def load_sport_weights(self) -> bool:
        """Read the sport weights. False if the profile cannot be edited now."""
        if not self._controller.can_edit_profile:
            return False

        def work(access: ProfileAccess) -> Mapping[Sport, SportWeight]:
            return dict(GetSportWeights(access.session.sport_weights)())

        return self._controller.run_on_profile(work, self._after_load_weights)

    def save_sport_weights(self, weights: Mapping[Sport, SportWeight]) -> bool:
        """Save the sport weights and recompute the overview."""
        if not self._controller.can_edit_profile:
            return False
        chosen = dict(weights)

        def work(access: ProfileAccess) -> None:
            session = access.session
            SetSportWeights(session.activities, session.sport_weights)(chosen)

        return self._controller.run_on_profile(work, self._after_save_weights)

    def _after_load_weights(self, outcome: JobOutcome) -> None:
        if isinstance(outcome, JobSucceeded) and isinstance(outcome.value, dict):
            self.sport_weights_loaded.emit(outcome.value)
        elif isinstance(outcome, JobFailed):
            self.failed.emit(self._controller.describe_failure(outcome.error))

    def _after_save_weights(self, outcome: JobOutcome) -> None:
        if isinstance(outcome, JobSucceeded):
            self.sport_weights_saved.emit()
            self._controller.request_overview_refresh()
        elif isinstance(outcome, JobFailed):
            self.failed.emit(self._controller.describe_failure(outcome.error))
