# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Dettaglio di un'attività: tracciato di FC, zone valide quel giorno, riepilogo e carico.

Caso d'uso di sola lettura: legge l'originale dall'archivio, lo decodifica e calcola il carico
con le stesse regole e gli stessi parametri della panoramica (parametri validi nel giorno
locale dell'attività, pesi salvati), senza passare dalla cache e senza scriverla.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

import numpy as np
from numpy.typing import NDArray

from ponos_coach.activities.domain import (
    MAX_ACTIVITY_FILE_BYTES,
    ActivityIdentity,
    ActivitySummary,
    HeartRateSeries,
    Sport,
)
from ponos_coach.activities.ports import ActivityArchive, ActivityIndex, ActivityNotFoundError
from ponos_coach.load.domain import (
    ActivityLoad,
    HeartRateLimits,
    out_of_limits,
    physiology_for_day,
    threshold_for_sport,
    zone_starts_bpm,
)
from ponos_coach.load.ports import PhysiologyStore, SportWeightStore
from ponos_coach.load.use_cases import (
    Decoder,
    ExcludedActivity,
    ExclusionReason,
    load_of_activity,
    local_day_of,
    read_original_activity,
)


@dataclass(frozen=True, slots=True, eq=False)
class HeartRateTrace:
    """Tracciato di FC: secondi dall'inizio, bpm e quali campioni il carico scarta.

    `discarded[i]` è vero se `bpm[i]` è fuori da `[FC minima, FC massima]` di quel giorno. Array
    di sola lettura, della stessa lunghezza.
    """

    seconds: NDArray[np.float64]
    bpm: NDArray[np.float64]
    discarded: NDArray[np.bool_]

    def __post_init__(self) -> None:
        if not self.seconds.shape == self.bpm.shape == self.discarded.shape:
            raise ValueError("le colonne del tracciato hanno lunghezze diverse")
        for array in (self.seconds, self.bpm, self.discarded):
            array.setflags(write=False)

    @classmethod
    def of(cls, series: HeartRateSeries, limits: HeartRateLimits) -> HeartRateTrace:
        """Tracciato di una serie con i limiti validi quel giorno."""
        return cls(series.seconds, series.bpm, out_of_limits(series, limits))

    @property
    def discarded_count(self) -> int:
        """Quanti campioni sono marcati come scartati."""
        return int(np.count_nonzero(self.discarded))


@dataclass(frozen=True, slots=True)
class ActivityDetail:
    """Tutto ciò che serve a mostrare un'attività.

    `trace` è None se l'attività non ha FC. `limits` e `threshold_bpm` sono quelli validi quel
    giorno per lo sport; `zone_starts_bpm` sono le FC da cui cominciano Z2, Z3, Z4 e Z5 (Z1 è
    sotto la prima). `unordered_hr_samples` conta i campioni di FC scartati dal lettore del file
    perché con orario ripetuto o all'indietro; i campioni fuori limiti sono in `trace` e in
    `load.discarded_samples`.
    """

    identity: ActivityIdentity
    sport: Sport
    start: datetime
    local_day: date
    elapsed_s: float
    timer_s: float
    summary: ActivitySummary
    load: ActivityLoad
    trace: HeartRateTrace | None
    limits: HeartRateLimits
    threshold_bpm: int
    zone_starts_bpm: tuple[float, float, float, float]
    unordered_hr_samples: int


class GetActivityDetail:
    """Dettaglio di un'attività del profilo aperto.

    Dipendenze: indice, archivio, parametri fisiologici, pesi degli sport, `decode` (dal
    Composition Root) e il fuso del profilo.
    """

    def __init__(  # noqa: PLR0913 - ogni porta è una dipendenza volatile distinta
        self,
        index: ActivityIndex,
        archive: ActivityArchive,
        physiology: PhysiologyStore,
        sport_weights: SportWeightStore,
        decode: Decoder,
        *,
        timezone: ZoneInfo,
        max_file_bytes: int = MAX_ACTIVITY_FILE_BYTES,
    ) -> None:
        if max_file_bytes <= 0:
            raise ValueError("il tetto di byte deve essere positivo")
        self._index = index
        self._archive = archive
        self._physiology = physiology
        self._sport_weights = sport_weights
        self._decode = decode
        self._timezone = timezone
        self._max_file_bytes = max_file_bytes

    def __call__(self, identity: ActivityIdentity) -> ActivityDetail | ExcludedActivity:
        """Dettaglio dell'attività, o il motivo per cui il suo file non si può usare.

        Raises:
            ActivityNotFoundError: l'identità non è nell'indice.
            MissingPhysiologyError: nessun parametro fisiologico impostato.
        """
        record = self._index.get(identity)
        if record is None:
            raise ActivityNotFoundError("attività non presente nell'indice")
        activity = read_original_activity(self._archive, record, self._decode, self._max_file_bytes)
        if isinstance(activity, ExclusionReason):
            return ExcludedActivity(identity, activity)
        local_day = local_day_of(record.start, self._timezone)
        physiology = physiology_for_day(self._physiology.list_entries(), local_day)
        load = load_of_activity(activity, physiology, self._sport_weights.load())
        threshold = threshold_for_sport(physiology, activity.sport)
        series = activity.heart_rate
        return ActivityDetail(
            identity=identity,
            sport=activity.sport,
            start=activity.start,
            local_day=local_day,
            elapsed_s=activity.elapsed_s,
            timer_s=activity.timer_s,
            summary=activity.summary,
            load=load,
            trace=None if series is None else HeartRateTrace.of(series, physiology.limits),
            limits=physiology.limits,
            threshold_bpm=threshold,
            zone_starts_bpm=zone_starts_bpm(threshold),
            unordered_hr_samples=activity.discarded_hr_samples,
        )
