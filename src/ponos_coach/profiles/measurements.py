# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Casi d'uso delle misure del profilo: peso, FC minima, FC massima e soglia con data di validità.

- `UpdateMeasurements`: aggiunge misure datate, in una transazione.
- `ReadMeasurementHistory`: dati fissi e storico per "Modifica profilo".
- `CompleteFixedProfileData`: imposta altezza e data di nascita di un profilo che non le ha.

Sono dati di salute: mai nei log né nei messaggi d'errore.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from enum import Enum

from ponos_coach.activities.ports import ActivityIndex
from ponos_coach.load.domain import (
    HeartRateLimits,
    Physiology,
    PhysiologyEntry,
    ThresholdHeartRate,
    physiology_for_day,
)
from ponos_coach.load.ports import PhysiologyStore
from ponos_coach.profiles.domain import (
    MIN_BIRTH_DATE,
    FixedProfileData,
    Weight,
    WeightEntry,
)
from ponos_coach.profiles.ports import FixedDataStore, WeightStore


class MeasurementProblem(Enum):
    """Perché un aggiornamento delle misure è rifiutato (decide il messaggio dell'interfaccia)."""

    NOTHING_TO_UPDATE = "nothing_to_update"  # nessuna misura indicata
    DATE_IN_FUTURE = "date_in_future"  # il giorno di validità è dopo oggi
    DATE_TOO_OLD = "date_too_old"  # il giorno di validità è anteriore al 1900-01-01
    INVALID_HEART_RATE = "invalid_heart_rate"  # FC fuori intervallo o incoerente


class InvalidMeasurementsError(ValueError):
    """Aggiornamento delle misure rifiutato. `problem` dice perché; mai i valori nel messaggio."""

    def __init__(self, problem: MeasurementProblem) -> None:
        super().__init__(problem.value)
        self.problem = problem


@dataclass(frozen=True, slots=True)
class MeasurementUpdate:
    """Nuove misure valide da un giorno: almeno una tra peso, FC minima, FC massima e soglia.

    Una misura `None` non cambia: vale quella in vigore quel giorno. Il sesso non si cambia.

    Raises:
        InvalidMeasurementsError: nessuna misura indicata (`NOTHING_TO_UPDATE`).
    """

    valid_from: date
    weight: Weight | None = None
    min_bpm: int | None = None
    max_bpm: int | None = None
    threshold_bpm: int | None = None

    def __post_init__(self) -> None:
        if self.weight is None and not self.changes_heart_rate:
            raise InvalidMeasurementsError(MeasurementProblem.NOTHING_TO_UPDATE)

    @property
    def changes_heart_rate(self) -> bool:
        """Vero se l'aggiornamento tocca FC minima, massima o soglia."""
        return not (self.min_bpm is None and self.max_bpm is None and self.threshold_bpm is None)


def _physiology_after(base: Physiology, update: MeasurementUpdate) -> Physiology:
    """I parametri `base` con le misure indicate; il risultato deve restare coerente."""
    try:
        return Physiology(
            HeartRateLimits(
                base.limits.min_bpm if update.min_bpm is None else update.min_bpm,
                base.limits.max_bpm if update.max_bpm is None else update.max_bpm,
            ),
            ThresholdHeartRate(
                base.threshold.bpm if update.threshold_bpm is None else update.threshold_bpm
            ),
            base.sex,
        )
    except ValueError as exc:
        raise InvalidMeasurementsError(MeasurementProblem.INVALID_HEART_RATE) from exc


class UpdateMeasurements:
    """Aggiunge misure datate: peso, FC minima, FC massima, soglia.

    Le misure non indicate restano quelle valide quel giorno; per lo stesso giorno la voce si
    sostituisce. Il vincolo `FC minima < soglia <= FC massima` si verifica sul risultato.
    Tutto in una transazione: o si salvano tutte le voci o nessuna. I carichi già calcolati con
    FC o soglia diverse si ricalcolano da soli alla panoramica successiva (cambia l'impronta);
    il peso corporeo non entra nel carico.

    `today` (la data di oggi nel fuso del profilo) è iniettata dal Composition Root.
    """

    def __init__(
        self,
        activities: ActivityIndex,
        physiology: PhysiologyStore,
        weights: WeightStore,
        today: Callable[[], date],
    ) -> None:
        self._activities = activities
        self._physiology = physiology
        self._weights = weights
        self._today = today

    def __call__(self, update: MeasurementUpdate) -> None:
        """Salva le misure dal giorno `update.valid_from`.

        Raises:
            InvalidMeasurementsError: giorno nel futuro o anteriore al 1900-01-01, oppure FC
                incoerenti; nulla è stato scritto.
            MissingPhysiologyError: il profilo non ha parametri fisiologici su cui basarsi.
            LoadStoreError, ProfileDataError: scrittura non riuscita; nulla cambia.
        """
        if update.valid_from > self._today():
            raise InvalidMeasurementsError(MeasurementProblem.DATE_IN_FUTURE)
        if update.valid_from < MIN_BIRTH_DATE:
            raise InvalidMeasurementsError(MeasurementProblem.DATE_TOO_OLD)
        with self._activities.transaction():
            if update.changes_heart_rate:
                base = physiology_for_day(self._physiology.list_entries(), update.valid_from)
                self._physiology.put(
                    PhysiologyEntry(update.valid_from, _physiology_after(base, update))
                )
            if update.weight is not None:
                self._weights.put(WeightEntry(update.valid_from, update.weight))


@dataclass(frozen=True, slots=True)
class MeasurementHistory:
    """Storico per "Modifica profilo": dati fissi (None se da completare), serie del peso e dei
    parametri fisiologici, per data di validità crescente."""

    fixed: FixedProfileData | None
    weights: tuple[WeightEntry, ...]
    physiology: tuple[PhysiologyEntry, ...]


class ReadMeasurementHistory:
    """Legge i dati fissi e lo storico delle misure del profilo aperto."""

    def __init__(
        self, fixed: FixedDataStore, weights: WeightStore, physiology: PhysiologyStore
    ) -> None:
        self._fixed = fixed
        self._weights = weights
        self._physiology = physiology

    def __call__(self) -> MeasurementHistory:
        """Lo storico completo.

        Raises:
            LoadStoreError, ProfileDataError: dati illeggibili.
        """
        return MeasurementHistory(
            self._fixed.get(),
            tuple(self._weights.list_entries()),
            tuple(self._physiology.list_entries()),
        )


class CompleteFixedProfileData:
    """Imposta altezza e data di nascita di un profilo che non le ha (creato prima che
    esistessero). Una volta impostate non si cambiano più."""

    def __init__(self, fixed: FixedDataStore) -> None:
        self._fixed = fixed

    def __call__(self, data: FixedProfileData) -> None:
        """Salva i dati fissi.

        Raises:
            FixedDataAlreadySetError: il profilo li ha già; nulla cambia.
            ProfileDataError: scrittura non riuscita.
        """
        self._fixed.set_once(data)
