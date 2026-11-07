# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Porte della funzionalità carico: parametri fisiologici e cache dei carichi.

I parametri sono dati dell'utente (fonte di verità); i carichi sono derivati e rigenerabili.
Le implementazioni condividono la connessione del profilo (`ActivityIndex.transaction`).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Protocol

from ponos_coach.activities.domain import ActivityIdentity, Sport
from ponos_coach.load.domain import ActivityLoad, LoadFingerprint, PhysiologyEntry, SportWeight


class LoadStoreError(Exception):
    """Radice degli errori di PhysiologyStore e LoadCache."""


class UnknownActivityError(LoadStoreError):
    """L'attività non è nell'indice: un carico si può salvare solo per un'attività nota."""


class PhysiologyStore(Protocol):
    """Parametri fisiologici con data di validità."""

    def put(self, entry: PhysiologyEntry) -> None:
        """Salva i parametri validi da `entry.valid_from`.

        Una voce con la stessa data di una esistente la sostituisce (correzione di un valore).
        """
        ...

    def list_entries(self) -> Sequence[PhysiologyEntry]:
        """Tutte le voci per data di validità crescente (una sola per data)."""
        ...


class SportWeightStore(Protocol):
    """Pesi degli sport scelti dall'utente (fonte di verità; i carichi derivati si ricalcolano).

    Contiene solo i pesi diversi dal default: uno sport assente vale `default_weight`.
    """

    def load(self) -> Mapping[Sport, SportWeight]:
        """I pesi salvati (vuoto se l'utente non ha cambiato nulla)."""
        ...

    def replace(self, weights: Mapping[Sport, SportWeight]) -> None:
        """Sostituisce tutti i pesi salvati con questi, tutto o niente."""
        ...


class LoadCache(Protocol):
    """Carichi per attività, validi solo per l'impronta dei parametri che li hanno prodotti."""

    def get(self, identity: ActivityIdentity, fingerprint: LoadFingerprint) -> ActivityLoad | None:
        """Il carico salvato, solo se prodotto con questa impronta; altrimenti None."""
        ...

    def put(
        self, identity: ActivityIdentity, fingerprint: LoadFingerprint, load: ActivityLoad
    ) -> None:
        """Salva (o sostituisce) il carico dell'attività con la sua impronta.

        Raises:
            UnknownActivityError: l'attività non è nell'indice.
        """
        ...

    def invalidate(self, identity: ActivityIdentity) -> None:
        """Dimentica il carico di un'attività (nessun effetto se non c'è)."""
        ...

    def clear(self) -> None:
        """Dimentica tutti i carichi (cambio di parametri: si ricalcolano dai file)."""
        ...
