# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Attività dell'elenco remoto di Garmin, pagine dell'elenco e abbinamento a un import da file.

Tutto è già validato: l'adattatore trasforma la risposta JSON in questi tipi al confine, o
scarta la voce. Nessun testo libero di Garmin (nomi, descrizioni) entra qui.
"""

from __future__ import annotations

import math
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Final

from ponos_coach.activities.domain.duplicates import is_possible_duplicate
from ponos_coach.activities.domain.garmin_id import GarminActivityId
from ponos_coach.activities.domain.identity import identity_key
from ponos_coach.activities.domain.record import ActivityRecord
from ponos_coach.activities.domain.sport import Sport
from ponos_coach.activities.domain.timing import check_utc, is_plausible_span

MAX_PAGE_SIZE: Final = 1000  # voci per pagina: oltre, la risposta è sospetta
DEFAULT_PAGE_SIZE: Final = 100  # voci per pagina chieste a Garmin (default del progetto)
_SPORT_KEY: Final = re.compile(r"[a-z][a-z0-9_]{0,63}")

# Chiavi di tipo dell'attività di Garmin (`typeKey`) -> sport dell'app. Il resto è `OTHER`.
_SPORT_BY_KEY: Final[dict[str, Sport]] = {
    # Sottochiavi note di Garmin: default del progetto, da confermare con la struttura
    # reale delle risposte (spike); le chiavi ignote finiscono in OTHER.
    "running": Sport.RUNNING,
    "street_running": Sport.RUNNING,
    "treadmill_running": Sport.RUNNING,
    "indoor_running": Sport.RUNNING,
    "track_running": Sport.RUNNING,
    "virtual_run": Sport.RUNNING,
    "ultra_run": Sport.RUNNING,
    "obstacle_run": Sport.RUNNING,
    "trail_running": Sport.TRAIL,
    "cycling": Sport.CYCLING,
    "road_biking": Sport.CYCLING,
    "mountain_biking": Sport.CYCLING,
    "gravel_cycling": Sport.CYCLING,
    "cyclocross": Sport.CYCLING,
    "track_cycling": Sport.CYCLING,
    "recumbent_cycling": Sport.CYCLING,
    "downhill_biking": Sport.CYCLING,
    "bmx": Sport.CYCLING,
    "indoor_cycling": Sport.CYCLING,
    "virtual_ride": Sport.CYCLING,
    "e_bike_fitness": Sport.CYCLING,
    "e_bike_mountain": Sport.CYCLING,
    "strength_training": Sport.STRENGTH,
    "hiking": Sport.HIKING,
    "walking": Sport.WALKING,
    "casual_walking": Sport.WALKING,
    "speed_walking": Sport.WALKING,
    "indoor_walking": Sport.WALKING,
}


def sport_from_garmin_key(sport_key: str) -> Sport:
    """Sport dell'app per la chiave di Garmin; `Sport.OTHER` se non la conosce."""
    return _SPORT_BY_KEY.get(sport_key, Sport.OTHER)


@dataclass(frozen=True, slots=True)
class RemoteActivity:
    """Riepilogo di un'attività dell'elenco remoto: ciò che basta per decidere se scaricarla.

    `start` è UTC al millisecondo e `start` + `duration_s` sta nella finestra degli orari
    ammessi; `duration_s` è finita e positiva; `sport_key` è la chiave di Garmin in minuscolo
    (`[a-z][a-z0-9_]{0,63}`); `has_heart_rate` è None se l'elenco non lo dice.
    """

    garmin_id: GarminActivityId
    start: datetime
    duration_s: float
    sport_key: str
    has_heart_rate: bool | None = None

    def __post_init__(self) -> None:
        check_utc(self.start)
        if self.start.microsecond % 1000:
            raise ValueError("l'inizio deve essere al millisecondo")
        if not math.isfinite(self.duration_s) or self.duration_s <= 0:
            raise ValueError("la durata deve essere finita e positiva")
        if not is_plausible_span(self.start, self.duration_s):
            raise ValueError("inizio o fine fuori dalla finestra degli orari ammessi")
        if not _SPORT_KEY.fullmatch(self.sport_key):
            raise ValueError("chiave dello sport non valida")

    @property
    def sport(self) -> Sport:
        """Lo sport dell'app corrispondente alla chiave di Garmin."""
        return sport_from_garmin_key(self.sport_key)


@dataclass(frozen=True, slots=True)
class ActivityPage:
    """Una pagina dell'elenco remoto, dalla più recente.

    `offset` è la posizione richiesta; `raw_count` quante voci Garmin ha restituito, comprese
    quelle che l'adattatore ha scartato perché malformate (`items` ne ha di meno o altrettante):
    la posizione successiva è `offset + raw_count`. `declared_total` è il totale dichiarato da
    Garmin, se lo dice. Gli id di `items` sono distinti.
    """

    offset: int
    items: tuple[RemoteActivity, ...]
    raw_count: int
    declared_total: int | None = None

    def __post_init__(self) -> None:
        if self.offset < 0 or not len(self.items) <= self.raw_count <= MAX_PAGE_SIZE:
            raise ValueError("pagina incoerente: posizione o conteggi fuori intervallo")
        if self.declared_total is not None and self.declared_total < 0:
            raise ValueError("il totale dichiarato non può essere negativo")
        if len({item.garmin_id for item in self.items}) != len(self.items):
            raise ValueError("id ripetuti nella pagina")

    @property
    def skipped_count(self) -> int:
        """Voci restituite da Garmin ma scartate come malformate."""
        return self.raw_count - len(self.items)


def find_matching_import(
    remote: RemoteActivity, candidates: Sequence[ActivityRecord]
) -> ActivityRecord | None:
    """L'attività già importata da file che coincide con quella remota, o None.

    Coincide se non ha ancora un id Garmin, ha lo stesso sport e `is_possible_duplicate`
    (inizi a non più di 60 s, durate entro il 5%). Con più candidati vince il più vicino
    nell'inizio, poi la chiave d'identità più piccola: la scelta è deterministica.
    `candidates` sono di solito i risultati di `ActivityIndex.find_possible_duplicates`.
    """
    matching = [
        record
        for record in candidates
        if record.garmin_activity_id is None
        and record.sport is remote.sport
        and is_possible_duplicate(remote.start, remote.duration_s, record.start, record.elapsed_s)
    ]
    if not matching:
        return None
    return min(matching, key=lambda r: (abs(r.start - remote.start), identity_key(r.identity)))
