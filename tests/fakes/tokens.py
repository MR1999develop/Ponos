# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""In-memory token store (`TokenStore` port), with injectable failures."""

from __future__ import annotations

from ponos_coach.activities.domain import SessionTokens
from ponos_coach.profiles.domain import ProfileId
from ponos_coach.profiles.ports import TokenStoreCorruptError, TokenStoreUnavailableError


class InMemoryTokenStore:
    """Tokens in a dictionary by profile id.

    `unavailable = True` simulates a keyring that does not respond; `corrupt(id)` makes the content
    of a profile unreadable. `saved` shows what is there (tests only).
    """

    def __init__(self) -> None:
        self.saved: dict[ProfileId, SessionTokens] = {}
        self.unavailable = False
        self._corrupt: set[ProfileId] = set()

    def corrupt(self, profile_id: ProfileId) -> None:
        """The profile content can no longer be read (until it is replaced or deleted)."""
        self._corrupt.add(profile_id)

    def save(self, profile_id: ProfileId, tokens: SessionTokens) -> None:
        """See `TokenStore.save`."""
        self._check_available()
        self.saved[profile_id] = tokens
        self._corrupt.discard(profile_id)

    def load(self, profile_id: ProfileId) -> SessionTokens | None:
        """See `TokenStore.load`."""
        self._check_available()
        if profile_id in self._corrupt:
            raise TokenStoreCorruptError("contenuto illeggibile")
        return self.saved.get(profile_id)

    def delete(self, profile_id: ProfileId) -> None:
        """See `TokenStore.delete`."""
        self._check_available()
        self.saved.pop(profile_id, None)
        self._corrupt.discard(profile_id)

    def _check_available(self) -> None:
        if self.unavailable:
            raise TokenStoreUnavailableError("keyring not available")
