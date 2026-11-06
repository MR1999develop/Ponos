# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Politica di pausa e ritentativi verso Garmin, e stima del tempo restante.

Tutti i valori stanno in `SyncPolicy` (default del progetto, PLAN.md rischi e 4.4 punto 4); le
funzioni sono pure: il jitter viene da una sorgente casuale iniettata, mai dal modulo `random`.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final, Protocol

import numpy as np

from ponos_coach.activities.domain.limits import MAX_ACTIVITY_FILE_BYTES
from ponos_coach.activities.domain.remote import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE

MAX_DOWNLOAD_BYTES: Final = MAX_ACTIVITY_FILE_BYTES  # tetto dello ZIP scaricato (default)
ETA_WINDOW: Final = 20  # durate recenti nella media mobile della stima


class RandomSource(Protocol):
    """Sorgente casuale iniettata (`random.Random` la soddisfa)."""

    def uniform(self, low: float, high: float) -> float:
        """Un numero in `[low, high]`."""
        ...


def _positive_finite(*values: float) -> bool:
    return all(math.isfinite(v) and v > 0 for v in values)


@dataclass(frozen=True, slots=True)
class SyncPolicy:
    """Pause, backoff e tetti dei tentativi (default del progetto).

    - pausa tra due richieste: `min_pause_s` più un jitter in `[0, max_jitter_s]`;
    - dopo un errore transitorio o un 429: `backoff_base_s * 2^(n-1)` (n = errori di fila) più
      il jitter, al massimo `backoff_cap_s`;
    - al massimo `max_attempts_per_request` tentativi per la stessa richiesta;
    - una voce del giornale diventa "fallita definitiva" dopo `max_syncs_per_activity`
      sincronizzazioni fallite;
    - l'elenco si chiede a pagine di `page_size` voci.
    """

    min_pause_s: float = 1.0
    max_jitter_s: float = 0.75
    backoff_base_s: float = 5.0
    backoff_cap_s: float = 120.0
    max_attempts_per_request: int = 5
    max_syncs_per_activity: int = 3
    page_size: int = DEFAULT_PAGE_SIZE

    def __post_init__(self) -> None:
        if not _positive_finite(self.min_pause_s, self.backoff_base_s, self.backoff_cap_s):
            raise ValueError("pause e backoff devono essere finiti e positivi")
        if not math.isfinite(self.max_jitter_s) or self.max_jitter_s < 0:
            raise ValueError("il jitter deve essere finito e non negativo")
        if self.backoff_cap_s < self.backoff_base_s:
            raise ValueError("il tetto del backoff non può stare sotto la base")
        if self.max_attempts_per_request < 1 or self.max_syncs_per_activity < 1:
            raise ValueError("serve almeno un tentativo")
        if not 1 <= self.page_size <= MAX_PAGE_SIZE:
            raise ValueError("dimensione di pagina fuori intervallo")


DEFAULT_POLICY: Final = SyncPolicy()


def pause_between_requests_s(policy: SyncPolicy, rng: RandomSource) -> float:
    """Secondi da attendere prima della richiesta successiva: pausa minima più jitter."""
    return policy.min_pause_s + _jitter(policy, rng)


def retry_wait_s(
    policy: SyncPolicy,
    failures: int,
    rng: RandomSource,
    suggested_s: float | None = None,
) -> float | None:
    """Attesa prima di ritentare una richiesta fallita `failures` volte di fila, o None.

    None dice di smettere: i tentativi hanno raggiunto `max_attempts_per_request`, oppure
    Garmin chiede un'attesa (`suggested_s`, per esempio `Retry-After`) oltre il tetto del
    backoff, e aspettarla a lungo sarebbe peggio di fermarsi con un errore chiaro. Altrimenti
    l'attesa è `backoff_base_s * 2^(failures-1)`, almeno `suggested_s`, più il jitter, mai oltre
    `backoff_cap_s`.

    Raises:
        ValueError: `failures` minore di 1 o `suggested_s` negativo o non finito.
    """
    if failures < 1:
        raise ValueError("serve almeno un fallimento")
    if suggested_s is not None and (not math.isfinite(suggested_s) or suggested_s < 0):
        raise ValueError("attesa suggerita non valida")
    if failures >= policy.max_attempts_per_request:
        return None
    if suggested_s is not None and suggested_s > policy.backoff_cap_s:
        return None
    exponent = min(failures - 1, 62)  # oltre il tetto non cambia nulla, ma 2**n resta piccolo
    base = min(policy.backoff_base_s * 2.0**exponent, policy.backoff_cap_s)
    wait = max(base, suggested_s or 0.0) + _jitter(policy, rng)
    return min(wait, policy.backoff_cap_s)


def _jitter(policy: SyncPolicy, rng: RandomSource) -> float:
    if policy.max_jitter_s == 0:
        return 0.0
    return min(max(rng.uniform(0.0, policy.max_jitter_s), 0.0), policy.max_jitter_s)


def estimate_remaining_s(
    recent_durations_s: Sequence[float], remaining_items: int, window: int = ETA_WINDOW
) -> float | None:
    """Secondi stimati per le voci rimaste: media mobile delle ultime `window` durate.

    Ogni durata è il tempo di una voce intera (pause comprese). None se non c'è ancora nessuna
    durata e restano voci; 0 se non resta nulla.

    Raises:
        ValueError: `remaining_items` negativo, `window` minore di 1 o durate non finite o
            negative.
    """
    if remaining_items < 0 or window < 1:
        raise ValueError("voci rimaste o finestra non valide")
    if remaining_items == 0:
        return 0.0
    if not recent_durations_s:
        return None
    recent = np.asarray(recent_durations_s[-window:], dtype=np.float64)
    if not bool(np.all(np.isfinite(recent))) or bool(np.any(recent < 0)):
        raise ValueError("durate non valide")
    return float(np.mean(recent)) * remaining_items
