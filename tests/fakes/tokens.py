# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Archivio dei token in memoria (porta `TokenStore`), con guasti iniettabili."""

from __future__ import annotations

from ponos_coach.activities.domain import SessionTokens
from ponos_coach.profiles.domain import ProfileId
from ponos_coach.profiles.ports import TokenStoreCorruptError, TokenStoreUnavailableError


class InMemoryTokenStore:
    """Token in un dizionario per id di profilo.

    `unavailable = True` simula un portachiavi che non risponde; `corrupt(id)` rende illeggibile
    il contenuto di un profilo. `saved` mostra cosa c'è (solo per i test).
    """

    def __init__(self) -> None:
        self.saved: dict[ProfileId, SessionTokens] = {}
        self.unavailable = False
        self._corrupt: set[ProfileId] = set()

    def corrupt(self, profile_id: ProfileId) -> None:
        """Il contenuto del profilo non si legge più (finché non lo si sostituisce o cancella)."""
        self._corrupt.add(profile_id)

    def save(self, profile_id: ProfileId, tokens: SessionTokens) -> None:
        """Vedi `TokenStore.save`."""
        self._check_available()
        self.saved[profile_id] = tokens
        self._corrupt.discard(profile_id)

    def load(self, profile_id: ProfileId) -> SessionTokens | None:
        """Vedi `TokenStore.load`."""
        self._check_available()
        if profile_id in self._corrupt:
            raise TokenStoreCorruptError("contenuto illeggibile")
        return self.saved.get(profile_id)

    def delete(self, profile_id: ProfileId) -> None:
        """Vedi `TokenStore.delete`."""
        self._check_available()
        self.saved.pop(profile_id, None)
        self._corrupt.discard(profile_id)

    def _check_available(self) -> None:
        if self.unavailable:
            raise TokenStoreUnavailableError("portachiavi non disponibile")
