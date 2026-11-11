# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Loading an activity's detail on the data thread.

The job reads the original file from the archive, decodes it, computes zones and load with the
parameters valid on that day and builds the HR figure and the speed or pace figure: all
off the UI thread.
A request superseded by another delivers nothing.
"""

from __future__ import annotations

from functools import partial

from PySide6.QtCore import QObject, Signal

from ponos_coach.activities.domain import ActivityIdentity
from ponos_coach.load.activity_detail import GetActivityDetail
from ponos_coach.load.use_cases import ExcludedActivity
from ponos_coach.profiles.day_nutrition import GetActivityEnergy
from ponos_coach.ui.activity_detail import DetailOutcome, DetailReady, DetailUnavailable
from ponos_coach.ui.data_thread import JobFailed, JobOutcome, JobSucceeded
from ponos_coach.ui.hr_figure import HeartRateLabels, prepare_heart_rate_chart
from ponos_coach.ui.nutrition_sources import nutrition_sources
from ponos_coach.ui.speed_figure import SpeedLabels, prepare_speed_chart
from ponos_coach.ui.tokens import ThemeTokens
from ponos_coach.ui.workspace import ProfileAccess, WorkspaceController


def _detail_of(
    identity: ActivityIdentity,
    labels: HeartRateLabels,
    speed_labels: SpeedLabels,
    tokens: ThemeTokens,
    access: ProfileAccess,
) -> DetailOutcome:
    """The data thread job: use case, HR figure and speed or pace figure."""
    session = access.session
    result = GetActivityDetail(
        session.activities,
        access.archive,
        session.physiology,
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
    speed_chart = None
    if result.speed is not None:
        speed_chart = prepare_speed_chart(result.speed, result.sport, speed_labels, tokens)
    return DetailReady(
        result, chart, GetActivityEnergy(nutrition_sources(access))(result), speed_chart
    )


class ActivityDetailLoader(QObject):
    """Load one activity's detail at a time; the latest request wins."""

    loaded = Signal(object)  # DetailOutcome
    failed = Signal(object)  # ErrorMessage

    def __init__(self, controller: WorkspaceController, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._controller = controller
        self._request = 0

    def load(
        self,
        identity: ActivityIdentity,
        labels: HeartRateLabels,
        speed_labels: SpeedLabels,
        tokens: ThemeTokens,
    ) -> bool:
        """Start loading. False if no profile is open or the app is closing."""
        self._request += 1
        request = self._request

        work = partial(_detail_of, identity, labels, speed_labels, tokens)

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
        """The dialog was closed: the incoming outcome no longer matters."""
        self._request += 1
