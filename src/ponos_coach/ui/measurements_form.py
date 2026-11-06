# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Modulo "Modifica profilo": stato e regole, testabili senza finestra.

Le misure (peso, FC minima, FC massima, soglia) valgono da un giorno scelto dall'utente (oggi per
default, mai nel futuro). Un campo che l'utente non ha toccato mostra, e vale, il valore in vigore
quel giorno; cambiando il giorno i campi non toccati seguono. Un valore scritto uguale a quello in
vigore non è una modifica: il campo torna "non toccato" e segue il giorno. Si salvano solo i campi
cambiati; `changes` dice cosa cambia rispetto al giorno scelto e `pending_periods` per quanto
tempo varrebbero i valori nuovi (una misura vale fino alla voce successiva già esistente).
Per un profilo creato prima che esistessero, altezza e data di nascita si completano una volta.

Il modulo dà problemi come categorie (`EditIssue`): i messaggi li sceglie la vista. Peso, altezza
e data di nascita sono dati di salute: mai in un messaggio né in un log.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
from datetime import date, timedelta
from enum import Enum

from ponos_coach.load.domain import (
    HeartRateLimits,
    MissingPhysiologyError,
    Physiology,
    Sex,
    ThresholdHeartRate,
    physiology_for_day,
)
from ponos_coach.profiles.domain import MIN_MEASUREMENT_DATE, FixedProfileData, Weight
from ponos_coach.profiles.measurements import (
    MeasurementHistory,
    MeasurementProblem,
    MeasurementUpdate,
    ValidityPeriod,
)
from ponos_coach.ui.body_input import BodyProblem, read_birth_date, read_height, read_weight
from ponos_coach.ui.heart_rate_rules import HeartRateProblem, heart_rate_problems
from ponos_coach.ui.new_profile_form import DEFAULT_MAX_BPM, DEFAULT_MIN_BPM, estimated_threshold


class EditField(Enum):
    """Dove la vista mostra un problema."""

    GENERAL = "general"
    VALID_FROM = "valid_from"
    WEIGHT = "weight"
    MIN_BPM = "min_bpm"
    MAX_BPM = "max_bpm"
    THRESHOLD = "threshold"
    HEIGHT = "height"
    BIRTH_DATE = "birth_date"


type IssueKind = BodyProblem | HeartRateProblem | MeasurementProblem


@dataclass(frozen=True, slots=True)
class EditIssue:
    """Un problema e il campo a cui appartiene."""

    field: EditField
    kind: IssueKind


class MeasurementSeries(Enum):
    """Le due serie datate del profilo: ognuna ha le sue voci e i suoi periodi di validità."""

    WEIGHT = "weight"
    HEART_RATE = "heart_rate"


@dataclass(frozen=True, slots=True)
class FieldChange:
    """Un valore che il salvataggio cambierebbe, e quello che vale quel giorno prima."""

    field: EditField
    before: Weight | int | None  # None: nessun peso registrato fino a quel giorno


@dataclass(frozen=True, slots=True)
class PendingPeriod:
    """Per quanto tempo varrebbero i valori nuovi di una serie se si salvasse ora."""

    series: MeasurementSeries
    period: ValidityPeriod

    @property
    def is_superseded(self) -> bool:
        """Vero se una voce successiva già esistente prende il posto dei valori nuovi."""
        return self.period.valid_to is not None


@dataclass(frozen=True, slots=True)
class MeasurementsRequest:
    """Ciò che il salvataggio deve scrivere: misure datate e/o dati fissi da completare."""

    update: MeasurementUpdate | None
    fixed: FixedProfileData | None


_HEART_RATE_FIELD = {
    HeartRateProblem.MIN_NOT_BELOW_MAX: EditField.MIN_BPM,
    HeartRateProblem.THRESHOLD_NOT_ABOVE_MIN: EditField.THRESHOLD,
    HeartRateProblem.THRESHOLD_ABOVE_MAX: EditField.THRESHOLD,
}
_REJECTION_FIELD = {
    MeasurementProblem.NOTHING_TO_UPDATE: EditField.GENERAL,
    MeasurementProblem.DATE_IN_FUTURE: EditField.VALID_FROM,
    MeasurementProblem.DATE_TOO_OLD: EditField.VALID_FROM,
    MeasurementProblem.INVALID_HEART_RATE: EditField.MIN_BPM,
}


def issue_for_rejection(problem: MeasurementProblem) -> EditIssue:
    """Il problema mostrato per un aggiornamento rifiutato dal caso d'uso."""
    return EditIssue(_REJECTION_FIELD[problem], problem)


def weight_in_force(history: MeasurementHistory, day: date) -> Weight | None:
    """Il peso in vigore in un giorno (l'ultima voce non successiva); `None` se non c'è ancora.

    Solo per mostrare il valore di partenza: la voce che si salva è decisa dal caso d'uso.
    """
    entries = history.weights
    index = bisect_right([entry.valid_from for entry in entries], day) - 1
    return entries[index].weight if index >= 0 else None


def _default_physiology() -> Physiology:
    return Physiology(
        HeartRateLimits(DEFAULT_MIN_BPM, DEFAULT_MAX_BPM),
        ThresholdHeartRate(estimated_threshold(DEFAULT_MAX_BPM)),
        Sex.MALE,
    )


class MeasurementsForm:
    """Valori del modulo "Modifica profilo" e regole. Non conosce widget né testi."""

    def __init__(self, history: MeasurementHistory, today: date) -> None:
        self._history = history
        self._today = today
        self._valid_from = today
        self._weight_text: str | None = None  # None: non toccato
        self._min_bpm: int | None = None
        self._max_bpm: int | None = None
        self._threshold_bpm: int | None = None
        self.height_text = ""  # solo per completare i dati fissi
        self.birth_date: date | None = None

    # --- Dati fissi -------------------------------------------------------------------

    @property
    def fixed(self) -> FixedProfileData | None:
        """Altezza e data di nascita del profilo; `None` se vanno ancora completate."""
        return self._history.fixed

    @property
    def needs_fixed_data(self) -> bool:
        """Vero per un profilo creato senza altezza e data di nascita."""
        return self._history.fixed is None

    @property
    def sex(self) -> Sex:
        """Il sesso del profilo (non si cambia): quello dell'ultima impostazione."""
        return self._latest_physiology().sex

    # --- Giorno di validità -----------------------------------------------------------

    @property
    def today(self) -> date:
        """Il limite superiore del giorno di validità."""
        return self._today

    @property
    def valid_from(self) -> date:
        """Il giorno da cui valgono le misure (oggi per default)."""
        return self._valid_from

    def set_valid_from(self, day: date) -> None:
        """Sceglie il giorno; un giorno fuori intervallo si porta al limite più vicino."""
        self._valid_from = min(max(day, MIN_MEASUREMENT_DATE), self._today)

    # --- Valori mostrati ---------------------------------------------------------------

    @property
    def baseline_weight(self) -> Weight | None:
        """Il peso in vigore nel giorno scelto, se c'è."""
        return weight_in_force(self._history, self._valid_from)

    @property
    def edited_weight_text(self) -> str | None:
        """Il peso scritto dall'utente, `None` se non ha toccato il campo."""
        return self._weight_text

    @property
    def min_bpm(self) -> int:
        """FC minima mostrata: quella scritta, o quella in vigore nel giorno scelto."""
        return self._min_bpm if self._min_bpm is not None else self._baseline().limits.min_bpm

    @property
    def max_bpm(self) -> int:
        """FC massima mostrata."""
        return self._max_bpm if self._max_bpm is not None else self._baseline().limits.max_bpm

    @property
    def threshold_bpm(self) -> int:
        """Soglia mostrata."""
        if self._threshold_bpm is not None:
            return self._threshold_bpm
        return self._baseline().threshold.bpm

    def _baseline(self) -> Physiology:
        try:
            return physiology_for_day(self._history.physiology, self._valid_from)
        except MissingPhysiologyError:
            return _default_physiology()

    def _latest_physiology(self) -> Physiology:
        try:
            return physiology_for_day(self._history.physiology, self._today)
        except MissingPhysiologyError:
            return _default_physiology()

    # --- Modifiche ---------------------------------------------------------------------

    def edit_weight(self, text: str) -> None:
        """L'utente scrive il peso. Vuoto o uguale a quello in vigore: il campo torna non toccato
        e segue il giorno scelto."""
        reading = read_weight(text)
        untouched = reading is BodyProblem.WEIGHT_EMPTY or (
            not isinstance(reading, BodyProblem) and reading == self.baseline_weight
        )
        self._weight_text = None if untouched else text

    def edit_min_bpm(self, value: int) -> None:
        """L'utente cambia la FC minima (uguale a quella in vigore: torna non toccata)."""
        self._min_bpm = _changed(value, self._baseline().limits.min_bpm)

    def edit_max_bpm(self, value: int) -> None:
        """L'utente cambia la FC massima (uguale a quella in vigore: torna non toccata)."""
        self._max_bpm = _changed(value, self._baseline().limits.max_bpm)

    def edit_threshold_bpm(self, value: int) -> None:
        """L'utente cambia la soglia (uguale a quella in vigore: torna non toccata)."""
        self._threshold_bpm = _changed(value, self._baseline().threshold.bpm)

    def _heart_rate_changes(self) -> tuple[int | None, int | None, int | None]:
        """Le tre FC cambiate rispetto al giorno scelto (`None` dove non cambiano)."""
        base = self._baseline()
        return (
            _changed(self._min_bpm, base.limits.min_bpm),
            _changed(self._max_bpm, base.limits.max_bpm),
            _changed(self._threshold_bpm, base.threshold.bpm),
        )

    def _fixed_started(self) -> bool:
        return bool(self.height_text.strip()) or self.birth_date is not None

    @property
    def has_changes(self) -> bool:
        """C'è qualcosa da salvare (anche un valore da correggere): abilita "Salva"."""
        heart_rate = any(value is not None for value in self._heart_rate_changes())
        return (
            self._weight_differs()
            or heart_rate
            or (self.needs_fixed_data and self._fixed_started())
        )

    def _weight_differs(self) -> bool:
        """Un peso scritto che non è quello in vigore (anche se da correggere)."""
        if self._weight_text is None:
            return False
        reading = read_weight(self._weight_text)
        return isinstance(reading, BodyProblem) or reading != self.baseline_weight

    def changes(self) -> tuple[FieldChange, ...]:
        """I valori validi che il salvataggio cambierebbe, con quello in vigore il giorno scelto."""
        base = self._baseline()
        found: list[FieldChange] = []
        if self._changed_weight() is not None:
            found.append(FieldChange(EditField.WEIGHT, self.baseline_weight))
        for field, edited, before in (
            (EditField.MIN_BPM, self._min_bpm, base.limits.min_bpm),
            (EditField.MAX_BPM, self._max_bpm, base.limits.max_bpm),
            (EditField.THRESHOLD, self._threshold_bpm, base.threshold.bpm),
        ):
            if edited is not None and edited != before:
                found.append(FieldChange(field, before))
        return tuple(found)

    def pending_periods(self) -> tuple[PendingPeriod, ...]:
        """Per quanto varrebbero i valori nuovi di ogni serie toccata: dal giorno scelto fino a
        quello prima della voce successiva già esistente (aperto se non ce n'è)."""
        found: list[PendingPeriod] = []
        if self._changed_weight() is not None:
            days = [entry.valid_from for entry in self._history.weights]
            found.append(PendingPeriod(MeasurementSeries.WEIGHT, self._period_in(days)))
        if any(value is not None for value in self._heart_rate_changes()):
            days = [entry.valid_from for entry in self._history.physiology]
            found.append(PendingPeriod(MeasurementSeries.HEART_RATE, self._period_in(days)))
        return tuple(found)

    def _period_in(self, entry_days: list[date]) -> ValidityPeriod:
        following = min((day for day in entry_days if day > self._valid_from), default=None)
        return ValidityPeriod(
            self._valid_from, None if following is None else following - timedelta(days=1)
        )

    # --- Validazione -------------------------------------------------------------------

    def issues(self) -> tuple[EditIssue, ...]:
        """Problemi dei valori attuali, nell'ordine dei campi (vuoto se si può salvare)."""
        found: list[EditIssue] = []
        if self._weight_text is not None:
            reading = read_weight(self._weight_text)
            if isinstance(reading, BodyProblem):
                found.append(EditIssue(EditField.WEIGHT, reading))
        if any(value is not None for value in self._heart_rate_changes()):
            found.extend(
                EditIssue(_HEART_RATE_FIELD[kind], kind)
                for kind in heart_rate_problems(self.min_bpm, self.max_bpm, self.threshold_bpm)
            )
        if self.needs_fixed_data and self._fixed_started():
            found.extend(self._fixed_issues())
        return tuple(found)

    def _fixed_issues(self) -> list[EditIssue]:
        height = read_height(self.height_text)
        birth = read_birth_date(self.birth_date, self._today)
        found: list[EditIssue] = []
        if isinstance(height, BodyProblem):
            found.append(EditIssue(EditField.HEIGHT, height))
        if isinstance(birth, BodyProblem):
            found.append(EditIssue(EditField.BIRTH_DATE, birth))
        return found

    def build(self) -> MeasurementsRequest | tuple[EditIssue, ...]:
        """La richiesta di salvataggio coi soli campi cambiati, o i problemi che la impediscono."""
        found = self.issues()
        if found:
            return found
        weight = self._changed_weight()
        min_bpm, max_bpm, threshold_bpm = self._heart_rate_changes()
        update = None
        if weight is not None or any(v is not None for v in (min_bpm, max_bpm, threshold_bpm)):
            update = MeasurementUpdate(self._valid_from, weight, min_bpm, max_bpm, threshold_bpm)
        fixed = self._built_fixed()
        if update is None and fixed is None:
            return (EditIssue(EditField.GENERAL, MeasurementProblem.NOTHING_TO_UPDATE),)
        return MeasurementsRequest(update, fixed)

    def _changed_weight(self) -> Weight | None:
        """Il peso scritto, se valido e diverso da quello in vigore nel giorno scelto."""
        if self._weight_text is None:
            return None
        reading = read_weight(self._weight_text)
        if isinstance(reading, BodyProblem) or reading == self.baseline_weight:
            return None
        return reading

    def _built_fixed(self) -> FixedProfileData | None:
        if not (self.needs_fixed_data and self._fixed_started()):
            return None
        height = read_height(self.height_text)
        birth = read_birth_date(self.birth_date, self._today)
        if isinstance(height, BodyProblem) or isinstance(birth, BodyProblem):
            return None
        return FixedProfileData(height, birth)


def _changed(edited: int | None, base: int) -> int | None:
    """Il valore scritto se differisce da quello in vigore, altrimenti `None`."""
    return None if edited is None or edited == base else edited
