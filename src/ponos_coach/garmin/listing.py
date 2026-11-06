# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Dall'elenco JSON di Garmin ai tipi di dominio: validazione al confine (T-SEC-03).

La risposta è non fidata. Qui diventa una sequenza di `RemoteActivity` valide più il conteggio
delle voci ricevute; ogni voce malformata (non un oggetto, id non numerico o con percorsi, orario
senza fuso o fuori finestra, durata assente o non finita, tipo di sport mancante, id ripetuto) si
scarta e si conta, senza ripararla. Nessun testo libero (nome, descrizione) viene letto. Si
accede solo alle chiavi note con controlli di tipo: nessuna ricorsione sulla struttura ricevuta.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Final

from ponos_coach.activities.domain import (
    MAX_PAGE_SIZE,
    GarminActivityId,
    RemoteActivity,
    truncate_to_millisecond,
)

MAX_TOTAL_COUNT: Final = 10**9  # totale dichiarato oltre il quale la risposta è sospetta
_MAX_TIME_CHARS: Final = 40
_MAX_SPORT_KEY_CHARS: Final = 64
_GMT_TIME: Final = re.compile(
    r"([0-9]{4})-([0-9]{2})-([0-9]{2})[ T]([0-9]{2}):([0-9]{2}):([0-9]{2})(?:\.([0-9]{1,6}))?Z?"
)


class UnexpectedListingError(Exception):
    """La risposta non è un elenco di attività (forma sbagliata o troppe voci)."""


def parse_page(raw: object) -> tuple[tuple[RemoteActivity, ...], int]:
    """Le attività valide di una pagina di elenco e quante voci Garmin ne ha restituite.

    `raw` è la lista di voci, oppure un oggetto con la lista in `activityList`.

    Raises:
        UnexpectedListingError: non c'è una lista, o ha più di `MAX_PAGE_SIZE` voci.
    """
    entries = raw.get("activityList") if isinstance(raw, Mapping) else raw
    if not isinstance(entries, list) or len(entries) > MAX_PAGE_SIZE:
        raise UnexpectedListingError
    items: list[RemoteActivity] = []
    seen: set[GarminActivityId] = set()
    for entry in entries:
        activity = parse_entry(entry)
        if activity is not None and activity.garmin_id not in seen:
            seen.add(activity.garmin_id)
            items.append(activity)
    return tuple(items), len(entries)


def parse_total(raw: object) -> int | None:
    """Il totale dichiarato (intero non negativo e plausibile) da un valore, o None."""
    if type(raw) is int and 0 <= raw <= MAX_TOTAL_COUNT:
        return raw
    return None


def parse_entry(entry: object) -> RemoteActivity | None:
    """L'attività di una voce dell'elenco, o None se la voce è malformata."""
    if not isinstance(entry, Mapping):
        return None
    try:
        garmin_id = GarminActivityId.parse(entry.get("activityId"))
        start = _parse_start(entry.get("startTimeGMT"))
        duration_s = _duration_s(entry)
        sport_key = _sport_key(entry.get("activityType"))
        if start is None or duration_s is None or sport_key is None:
            return None
        return RemoteActivity(garmin_id, start, duration_s, sport_key, _has_heart_rate(entry))
    except (ValueError, OverflowError):
        return None


def _number(value: object) -> float | None:
    """Un numero finito (non un booleano), altrimenti None."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _duration_s(entry: Mapping[str, object]) -> float | None:
    """Durata trascorsa in secondi: `elapsedDuration` se c'è, altrimenti `duration`.

    La durata dell'app è il tempo trascorso (come nel FIT), che include le pause.
    """
    raw = entry.get("elapsedDuration")
    value = _number(raw if raw is not None else entry.get("duration"))
    return value if value is not None and value > 0 else None


def _sport_key(activity_type: object) -> str | None:
    if not isinstance(activity_type, Mapping):
        return None
    key = activity_type.get("typeKey")
    if not isinstance(key, str) or not 0 < len(key) <= _MAX_SPORT_KEY_CHARS:
        return None
    return key


def _has_heart_rate(entry: Mapping[str, object]) -> bool | None:
    """Vero se la FC media è un numero positivo; falso se è nulla (`null` o 0: Garmin non l'ha
    registrata); None se l'elenco non ha la chiave o ha un valore che non è un numero."""
    if "averageHR" not in entry:
        return None
    raw = entry["averageHR"]
    if raw is None:
        return False
    value = _number(raw)
    return None if value is None else value > 0


def _parse_start(raw: object) -> datetime | None:
    """Inizio in UTC al millisecondo da `startTimeGMT` (`2026-03-02 07:00:00`), o None."""
    if not isinstance(raw, str) or len(raw) > _MAX_TIME_CHARS:
        return None
    match = _GMT_TIME.fullmatch(raw)
    if match is None:
        return None
    year, month, day, hour, minute, second = (int(group) for group in match.groups()[:6])
    fraction = match.group(7) or ""
    microsecond = int(fraction.ljust(6, "0")) if fraction else 0
    try:
        moment = datetime(year, month, day, hour, minute, second, microsecond, tzinfo=UTC)
    except ValueError:
        return None
    return truncate_to_millisecond(moment)
