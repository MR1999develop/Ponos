# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Apertura e chiusura dei dialoghi dei dati del profilo: modifica, pesi degli sport, dettaglio.

Ogni dialogo si apre quando il thread dei dati ha letto ciò che mostra (storico delle misure,
pesi, dettaglio dell'attività): la UI non legge mai il profilo. Il salvataggio chiude il dialogo
solo a lavoro finito; se il caso d'uso rifiuta le misure, il problema compare sul campo giusto e
il dialogo resta aperto con i dati scritti.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import date
from zoneinfo import ZoneInfo

from PySide6.QtCore import QCoreApplication, QObject, Qt
from PySide6.QtWidgets import QDialog, QWidget

from ponos_coach.activities.domain import ActivityIdentity, Sport
from ponos_coach.load.domain import SportWeight
from ponos_coach.profiles.measurements import MeasurementHistory
from ponos_coach.ui.activity_detail import DetailReady, DetailUnavailable
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
from ponos_coach.ui.sport_weights_dialog import SportWeightsDialog
from ponos_coach.ui.sport_weights_form import SportWeightsForm
from ponos_coach.ui.tokens import Theme, tokens_for


class ProfileDialogs(QObject):
    """Possiede i dialoghi dei dati del profilo e li collega ai lavori del thread dei dati."""

    def __init__(  # noqa: PLR0913 - ogni argomento è una dipendenza della finestra madre
        self,
        window: QWidget,
        editor: ProfileEditor,
        loader: ActivityDetailLoader,
        engine: ChartEngine,
        *,
        theme: Callable[[], Theme],
        today: Callable[[], date],
        zone: Callable[[], ZoneInfo],
        profile_name: Callable[[], str],
        show_error: Callable[[ErrorMessage], None],
        notify: Callable[[str], None],
    ) -> None:
        super().__init__(window)
        self._window = window
        self._editor = editor
        self._loader = loader
        self._engine = engine
        self._theme = theme
        self._today = today
        self._zone = zone
        self._profile_name = profile_name
        self._show_error = show_error
        self._notify = notify
        self._edit_dialog: ProfileEditDialog | None = None
        self._weights_dialog: SportWeightsDialog | None = None
        self._detail_dialog: ActivityDetailDialog | None = None
        self._waiting_for_edit = False
        self._waiting_for_weights = False
        editor.measurements_loaded.connect(self._on_measurements_loaded)
        editor.measurements_saved.connect(self._on_measurements_saved)
        editor.measurements_rejected.connect(self._on_measurements_rejected)
        editor.sport_weights_loaded.connect(self._on_weights_loaded)
        editor.sport_weights_saved.connect(self._on_weights_saved)
        editor.failed.connect(self._on_failed)
        loader.loaded.connect(self._on_detail_loaded)
        loader.failed.connect(self._on_detail_failed)

    # --- Accessori per i test ---------------------------------------------------------

    @property
    def edit_dialog(self) -> ProfileEditDialog | None:
        """Il dialogo "Modifica profilo" aperto, se c'è."""
        return self._edit_dialog

    @property
    def weights_dialog(self) -> SportWeightsDialog | None:
        """Il dialogo "Pesi degli sport" aperto, se c'è."""
        return self._weights_dialog

    @property
    def detail_dialog(self) -> ActivityDetailDialog | None:
        """La finestra "Dettaglio attività" aperta, se c'è."""
        return self._detail_dialog

    def close_all(self) -> None:
        """Chiude i dialoghi aperti (alla chiusura della finestra): le pagine web si rilasciano."""
        for dialog in (self._detail_dialog, self._edit_dialog, self._weights_dialog):
            if dialog is not None:
                dialog.close()

    # --- Modifica profilo -------------------------------------------------------------

    def edit_profile(self) -> None:
        """Legge lo storico e apre "Modifica profilo" (nessun effetto se è già aperto)."""
        if self._edit_dialog is not None or self._waiting_for_edit:
            return
        self._waiting_for_edit = self._editor.load_measurements()

    def _on_measurements_loaded(self, history: object) -> None:
        self._waiting_for_edit = False
        if not isinstance(history, MeasurementHistory) or self._edit_dialog is not None:
            return
        form = MeasurementsForm(history, self._today())
        dialog = ProfileEditDialog(form, history, self._profile_name(), self._theme(), self._window)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.save_requested.connect(self._on_save_measurements)
        dialog.finished.connect(self._on_edit_closed)
        self._edit_dialog = dialog
        dialog.open()

    def _on_save_measurements(self, request: object) -> None:
        if isinstance(request, MeasurementsRequest) and not self._editor.save_measurements(request):
            self._not_now()

    def _on_measurements_saved(self, edit: object) -> None:
        dialog = self._edit_dialog
        if dialog is not None and isinstance(edit, SavedEdit):
            dialog.set_history(edit.history)
            dialog.accept()
            periods = [
                saved_period_text(series, period)
                for series, period in (
                    (MeasurementSeries.WEIGHT, edit.saved.weight),
                    (MeasurementSeries.HEART_RATE, edit.saved.heart_rate),
                )
                if period is not None
            ]
            self._notify(
                " ".join(
                    [
                        QCoreApplication.translate("ProfileDialogs", "Profile updated."),
                        *periods,
                        QCoreApplication.translate(
                            "ProfileDialogs", "The load is being recalculated."
                        ),
                    ]
                )
            )

    def _on_measurements_rejected(self, issue: object) -> None:
        if self._edit_dialog is not None and isinstance(issue, EditIssue):
            self._edit_dialog.show_rejection(issue)

    def _on_edit_closed(self) -> None:
        self._edit_dialog = None

    # --- Pesi degli sport -------------------------------------------------------------

    def edit_sport_weights(self) -> None:
        """Legge i pesi e apre "Pesi degli sport" (nessun effetto se è già aperto)."""
        if self._weights_dialog is not None or self._waiting_for_weights:
            return
        self._waiting_for_weights = self._editor.load_sport_weights()

    def _on_weights_loaded(self, weights: object) -> None:
        self._waiting_for_weights = False
        if not isinstance(weights, Mapping) or self._weights_dialog is not None:
            return
        current: dict[Sport, SportWeight] = {
            sport: value
            for sport, value in weights.items()
            if isinstance(sport, Sport) and isinstance(value, SportWeight)
        }
        dialog = SportWeightsDialog(SportWeightsForm(current), self._window)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.save_requested.connect(self._on_save_weights)
        dialog.finished.connect(self._on_weights_closed)
        self._weights_dialog = dialog
        dialog.open()

    def _on_save_weights(self, weights: object) -> None:
        if isinstance(weights, Mapping) and not self._editor.save_sport_weights(dict(weights)):
            self._not_now()

    def _on_weights_saved(self) -> None:
        if self._weights_dialog is not None:
            self._weights_dialog.accept()
            self._notify(
                QCoreApplication.translate(
                    "ProfileDialogs", "Sport weights saved. The load is being recalculated."
                )
            )

    def _on_weights_closed(self) -> None:
        self._weights_dialog = None

    # --- Dettaglio attività -----------------------------------------------------------

    def show_activity_detail(self, identity: ActivityIdentity) -> None:
        """Apre (o riusa) la finestra del dettaglio e avvia il caricamento."""
        dialog = self._detail_dialog
        if dialog is None:
            dialog = ActivityDetailDialog(self._engine, self._zone(), self._window)
            dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
            dialog.finished.connect(self._on_detail_closed)
            self._detail_dialog = dialog
        else:
            dialog.show_loading()
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()
        labels = HeartRateLabels(
            x_axis=QCoreApplication.translate("ProfileDialogs", "Time"),
            y_axis=QCoreApplication.translate("ProfileDialogs", "Heart rate"),
            heart_rate=QCoreApplication.translate("ProfileDialogs", "Heart rate"),
            discarded=QCoreApplication.translate("ProfileDialogs", "Samples outside your limits"),
            chrome=chart_chrome(),
        )
        if not self._loader.load(identity, labels, tokens_for(self._theme())):
            dialog.close()

    def _on_detail_loaded(self, outcome: object) -> None:
        dialog = self._detail_dialog
        if dialog is not None and isinstance(outcome, DetailReady | DetailUnavailable):
            dialog.show_outcome(outcome)

    def _on_detail_failed(self, message: object) -> None:
        if self._detail_dialog is not None and isinstance(message, ErrorMessage):
            self._detail_dialog.show_error(message)

    def _on_detail_closed(self) -> None:
        self._detail_dialog = None
        self._loader.cancel()

    # --- Errori -----------------------------------------------------------------------

    def _not_now(self) -> None:
        """Il salvataggio non è partito (il profilo è occupato): i dati scritti restano."""
        self._unlock_all()
        self._notify(
            QCoreApplication.translate(
                "ProfileDialogs", "The profile is busy. Save again when the operation has finished."
            )
        )

    def _unlock_all(self) -> None:
        for dialog in (self._edit_dialog, self._weights_dialog):
            if dialog is not None:
                self._unlock(dialog)

    def _on_failed(self, message: object) -> None:
        """Un lavoro è fallito: i dialoghi tornano modificabili e l'errore si mostra."""
        self._waiting_for_edit = False
        self._waiting_for_weights = False
        self._unlock_all()
        if isinstance(message, ErrorMessage):
            self._show_error(message)

    @staticmethod
    def _unlock(dialog: QDialog) -> None:
        if isinstance(dialog, ProfileEditDialog | SportWeightsDialog):
            dialog.set_busy(False)
