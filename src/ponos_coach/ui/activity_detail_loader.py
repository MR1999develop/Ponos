# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Caricamento del dettaglio di un'attività nel thread dei dati.

Il lavoro legge il file originale dall'archivio, lo decodifica, calcola zone e carico con i
parametri validi quel giorno e costruisce la figura della FC: tutto fuori dal thread della UI.
Una richiesta superata da un'altra non consegna nulla.
"""

from __future__ import annotations

from functools import partial

from PySide6.QtCore import QObject, Signal

from ponos_coach.activities.domain import ActivityIdentity
from ponos_coach.load.activity_detail import GetActivityDetail
from ponos_coach.load.use_cases import ExcludedActivity
from ponos_coach.ui.activity_detail import DetailOutcome, DetailReady, DetailUnavailable
from ponos_coach.ui.data_thread import JobFailed, JobOutcome, JobSucceeded
from ponos_coach.ui.hr_figure import HeartRateLabels, prepare_heart_rate_chart
from ponos_coach.ui.tokens import ThemeTokens
from ponos_coach.ui.workspace import ProfileAccess, WorkspaceController


def _detail_of(
    identity: ActivityIdentity,
    labels: HeartRateLabels,
    tokens: ThemeTokens,
    access: ProfileAccess,
) -> DetailOutcome:
    """Il lavoro del thread dei dati: caso d'uso e figura della FC."""
    session = access.session
    result = GetActivityDetail(
        session.activities,
        access.archive,
        session.physiology,
        session.sport_weights,
        access.decode,
        timezone=access.zone,
    )(identity)
    if isinstance(result, ExcludedActivity):
        return DetailUnavailable(result.reason)
    chart = None
    if result.trace is not None:
        chart = prepare_heart_rate_chart(
            result.trace, result.limits, result.zone_starts_bpm, labels, tokens
        )
    return DetailReady(result, chart)


class ActivityDetailLoader(QObject):
    """Carica il dettaglio di un'attività alla volta; l'ultima richiesta vince."""

    loaded = Signal(object)  # DetailOutcome
    failed = Signal(object)  # ErrorMessage

    def __init__(self, controller: WorkspaceController, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._controller = controller
        self._request = 0

    def load(
        self, identity: ActivityIdentity, labels: HeartRateLabels, tokens: ThemeTokens
    ) -> bool:
        """Avvia il caricamento. Falso se non c'è un profilo aperto o l'app si chiude."""
        self._request += 1
        request = self._request

        work = partial(_detail_of, identity, labels, tokens)

        def done(outcome: JobOutcome) -> None:
            if request != self._request:
                return
            if isinstance(outcome, JobSucceeded) and isinstance(
                outcome.value, DetailReady | DetailUnavailable
            ):
                self.loaded.emit(outcome.value)
            elif isinstance(outcome, JobFailed):
                self.failed.emit(self._controller.describe_failure(outcome.error))

        return self._controller.run_on_profile(work, done)

    def cancel(self) -> None:
        """Il dialogo si è chiuso: l'esito in arrivo non interessa più."""
        self._request += 1
