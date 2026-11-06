# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Casi d'uso delle misure del profilo: peso, FC minima, FC massima e soglia con data di validità.

- `UpdateMeasurements`: il salvataggio di "Modifica profilo", in una sola transazione: dati fissi
  da completare (altezza e data di nascita di un profilo che non le ha), peso e misure di FC
  datati. Restituisce il periodo di validità effettivo di ciò che ha salvato.
- `ReadMeasurementHistory`: dati fissi e storico per "Modifica profilo".

Sono dati di salute: mai nei log né nei messaggi d'errore.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date, timedelta
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
    MIN_MEASUREMENT_DATE,
    FixedProfileData,
    Weight,
    WeightEntry,
)
from ponos_coach.profiles.ports import FixedDataStore, WeightStore


class MeasurementProblem(Enum):
    """Perché un aggiornamento delle misure è rifiutato (decide il messaggio dell'interfaccia)."""

    NOTHING_TO_UPDATE = "nothing_to_update"  # nessuna misura né dato fisso indicati
    DATE_IN_FUTURE = "date_in_future"  # il giorno di validità è dopo oggi
    DATE_TOO_OLD = "date_too_old"  # il giorno di validità è anteriore a MIN_MEASUREMENT_DATE
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


@dataclass(frozen=True, slots=True)
class ValidityPeriod:
    """Da quando a quando vale una misura salvata.

    `valid_from` è il primo giorno; `valid_to` l'ultimo giorno incluso, cioè il giorno prima della
    voce successiva già esistente della stessa serie, o None se nessuna voce la segue (vale
    fino alla prossima misura).
    """

    valid_from: date
    valid_to: date | None


@dataclass(frozen=True, slots=True)
class SavedMeasurements:
    """Che cosa ha salvato `UpdateMeasurements`.

    Il periodo di ogni serie toccata (None se non toccata: il peso e le misure di FC sono due
    serie distinte) e se ha completato i dati fissi.
    """

    weight: ValidityPeriod | None
    heart_rate: ValidityPeriod | None
    fixed_completed: bool


def _period_in(valid_from: date, entry_days: Iterable[date]) -> ValidityPeriod:
    """Il periodo di una voce di data `valid_from` tra le voci della serie (lei compresa)."""
    following = min((day for day in entry_days if day > valid_from), default=None)
    return ValidityPeriod(valid_from, None if following is None else following - timedelta(days=1))


class UpdateMeasurements:
    """Salva i dati di "Modifica profilo" in una sola transazione: o tutto o niente.

    Può completare i dati fissi (altezza e data di nascita di un profilo creato prima che
    esistessero; una volta impostati non si cambiano più) e aggiungere misure datate: peso, FC
    minima, FC massima, soglia. Se una parte fallisce (per esempio i dati fissi sono già
    impostati) non resta scritto niente.

    Regola di validità: una misura vale dal suo giorno fino alla voce successiva già esistente
    della stessa serie. Una misura con data passata quindi non cambia ciò che vale dopo una voce
    più recente, e per lo stesso giorno la voce si sostituisce. Le misure non indicate restano
    quelle in vigore quel giorno. Il vincolo `FC minima < soglia <= FC massima` si verifica sul
    risultato. I carichi già calcolati con FC o soglia diverse si ricalcolano da soli alla
    panoramica successiva (cambia l'impronta); il peso corporeo non entra nel carico.

    `today` (la data di oggi nel fuso del profilo) è iniettata dal Composition Root.
    """

    def __init__(
        self,
        activities: ActivityIndex,
        physiology: PhysiologyStore,
        weights: WeightStore,
        today: Callable[[], date],
        fixed_data: FixedDataStore,
    ) -> None:
        self._activities = activities
        self._physiology = physiology
        self._weights = weights
        self._today = today
        self._fixed = fixed_data

    def __call__(
        self, update: MeasurementUpdate | None = None, fixed: FixedProfileData | None = None
    ) -> SavedMeasurements:
        """Salva i dati fissi da completare (`fixed`) e/o le misure di `update`, e dice per
        quali periodi valgono.

        Raises:
            InvalidMeasurementsError: nulla da salvare, giorno nel futuro o anteriore a
                `MIN_MEASUREMENT_DATE`, oppure FC incoerenti; nulla è stato scritto.
            FixedDataAlreadySetError: il profilo ha già altezza e data di nascita; nulla cambia.
            MissingPhysiologyError: il profilo non ha parametri fisiologici su cui basarsi.
            LoadStoreError, ProfileDataError: scrittura non riuscita; nulla cambia.
        """
        if update is None and fixed is None:
            raise InvalidMeasurementsError(MeasurementProblem.NOTHING_TO_UPDATE)
        if update is not None:
            if update.valid_from > self._today():
                raise InvalidMeasurementsError(MeasurementProblem.DATE_IN_FUTURE)
            if update.valid_from < MIN_MEASUREMENT_DATE:
                raise InvalidMeasurementsError(MeasurementProblem.DATE_TOO_OLD)
        with self._activities.transaction():
            if fixed is not None:
                self._fixed.set_once(fixed)
            weight_period, heart_rate_period = self._save(update)
        return SavedMeasurements(weight_period, heart_rate_period, fixed is not None)

    def _save(
        self, update: MeasurementUpdate | None
    ) -> tuple[ValidityPeriod | None, ValidityPeriod | None]:
        """Scrive le misure e dà i periodi (peso, FC), dentro la transazione già aperta."""
        if update is None:
            return (None, None)
        heart_rate_period = None
        if update.changes_heart_rate:
            base = physiology_for_day(self._physiology.list_entries(), update.valid_from)
            self._physiology.put(
                PhysiologyEntry(update.valid_from, _physiology_after(base, update))
            )
            heart_rate_period = _period_in(
                update.valid_from, (e.valid_from for e in self._physiology.list_entries())
            )
        weight_period = None
        if update.weight is not None:
            self._weights.put(WeightEntry(update.valid_from, update.weight))
            weight_period = _period_in(
                update.valid_from, (e.valid_from for e in self._weights.list_entries())
            )
        return (weight_period, heart_rate_period)


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
