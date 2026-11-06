# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Modifica dei dati del profilo: misure datate e pesi degli sport, nel thread dei dati.

Ogni lavoro (lettura dello storico, salvataggio delle misure, lettura e salvataggio dei pesi)
costruisce il caso d'uso dentro il thread dei dati con la connessione del profilo aperto e
restituisce il risultato come segnale. Dopo un salvataggio la panoramica si ricalcola. Peso,
FC e dati fissi sono dati di salute: mai nei log né nei messaggi d'errore.
"""

from __future__ import annotations

from collections.abc import Mapping

from PySide6.QtCore import QObject, Signal

from ponos_coach.activities.domain import Sport
from ponos_coach.load.domain import SportWeight
from ponos_coach.load.use_cases import GetSportWeights, SetSportWeights
from ponos_coach.profiles.measurements import (
    InvalidMeasurementsError,
    MeasurementHistory,
    ReadMeasurementHistory,
    UpdateMeasurements,
)
from ponos_coach.ui.data_thread import JobFailed, JobOutcome, JobSucceeded
from ponos_coach.ui.measurements_form import MeasurementsRequest, issue_for_rejection
from ponos_coach.ui.workspace import ProfileAccess, WorkspaceController


def _read_history(access: ProfileAccess) -> MeasurementHistory:
    session = access.session
    return ReadMeasurementHistory(session.fixed_data, session.weights, session.physiology)()


class ProfileEditor(QObject):
    """Lavori di modifica del profilo aperto. Gli esiti arrivano come segnali nel thread UI."""

    measurements_loaded = Signal(object)  # MeasurementHistory
    measurements_saved = Signal(object)  # MeasurementHistory, già riletto dopo il salvataggio
    measurements_rejected = Signal(object)  # EditIssue
    sport_weights_loaded = Signal(object)  # Mapping[Sport, SportWeight]
    sport_weights_saved = Signal()
    failed = Signal(object)  # ErrorMessage

    def __init__(self, controller: WorkspaceController, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._controller = controller

    # --- Misure -----------------------------------------------------------------------

    def load_measurements(self) -> bool:
        """Legge dati fissi e storico. Falso se ora non si può modificare il profilo."""
        if not self._controller.can_edit_profile:
            return False
        return self._controller.run_on_profile(_read_history, self._after_load_measurements)

    def save_measurements(self, request: MeasurementsRequest) -> bool:
        """Salva le misure datate e/o completa i dati fissi, poi rilegge lo storico."""
        if not self._controller.can_edit_profile:
            return False

        def work(access: ProfileAccess) -> MeasurementHistory:
            session = access.session
            UpdateMeasurements(
                session.activities,
                session.physiology,
                session.weights,
                access.today,
                session.fixed_data,
            )(request.update, request.fixed)
            return _read_history(access)

        return self._controller.run_on_profile(work, self._after_save_measurements)

    def _after_load_measurements(self, outcome: JobOutcome) -> None:
        if isinstance(outcome, JobSucceeded) and isinstance(outcome.value, MeasurementHistory):
            self.measurements_loaded.emit(outcome.value)
        elif isinstance(outcome, JobFailed):
            self.failed.emit(self._controller.describe_failure(outcome.error))

    def _after_save_measurements(self, outcome: JobOutcome) -> None:
        if isinstance(outcome, JobSucceeded) and isinstance(outcome.value, MeasurementHistory):
            self.measurements_saved.emit(outcome.value)
            self._controller.request_overview_refresh()
        elif isinstance(outcome, JobFailed):
            error = outcome.error
            if isinstance(error, InvalidMeasurementsError):
                self.measurements_rejected.emit(issue_for_rejection(error.problem))
            else:
                self.failed.emit(self._controller.describe_failure(error))

    # --- Pesi degli sport -------------------------------------------------------------

    def load_sport_weights(self) -> bool:
        """Legge i pesi degli sport. Falso se ora non si può modificare il profilo."""
        if not self._controller.can_edit_profile:
            return False

        def work(access: ProfileAccess) -> Mapping[Sport, SportWeight]:
            return dict(GetSportWeights(access.session.sport_weights)())

        return self._controller.run_on_profile(work, self._after_load_weights)

    def save_sport_weights(self, weights: Mapping[Sport, SportWeight]) -> bool:
        """Salva i pesi degli sport e ricalcola la panoramica."""
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
