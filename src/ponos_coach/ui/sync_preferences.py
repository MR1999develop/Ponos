# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Preferenze della sincronizzazione, ricordate per profilo.

Oggi una sola: se all'apertura del profilo parte da sola la sincronizzazione (predefinito:
sì). La porta `SyncPreferences` la usa il controller; l'implementazione su `QSettings` la
compone `__main__`, quella in memoria serve ai test e ai casi senza impostazioni.
"""

from __future__ import annotations

from typing import Final, Protocol

from PySide6.QtCore import QSettings

from ponos_coach.profiles.domain import ProfileId

AUTO_SYNC_DEFAULT: Final = True
_KEY_PREFIX: Final = "garmin/autoSyncOnOpen/"
_TRUE_TEXTS: Final = frozenset({"true", "1"})
_FALSE_TEXTS: Final = frozenset({"false", "0"})


class SyncPreferences(Protocol):
    """Le scelte dell'utente sulla sincronizzazione, una per profilo."""

    def auto_sync(self, profile_id: ProfileId) -> bool:
        """Vero se aprendo il profilo la sincronizzazione parte da sola."""
        ...

    def set_auto_sync(self, profile_id: ProfileId, *, enabled: bool) -> None:
        """Ricorda la scelta per il profilo."""
        ...


class MemorySyncPreferences:
    """Preferenze che non sopravvivono al processo."""

    def __init__(self) -> None:
        self._auto: dict[ProfileId, bool] = {}

    def auto_sync(self, profile_id: ProfileId) -> bool:
        """Vedi `SyncPreferences.auto_sync`."""
        return self._auto.get(profile_id, AUTO_SYNC_DEFAULT)

    def set_auto_sync(self, profile_id: ProfileId, *, enabled: bool) -> None:
        """Vedi `SyncPreferences.set_auto_sync`."""
        self._auto[profile_id] = enabled


def parse_stored_flag(raw: object, *, default: bool) -> bool:
    """Il valore letto da `QSettings` (bool, "true"/"false" o altro): ciò che non si capisce
    vale il predefinito, mai un errore."""
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        text = raw.strip().lower()
        if text in _TRUE_TEXTS:
            return True
        if text in _FALSE_TEXTS:
            return False
    return default


class QSettingsSyncPreferences:
    """Preferenze nelle impostazioni dell'app, con una chiave per profilo."""

    def __init__(self, settings: QSettings) -> None:
        self._settings = settings

    def auto_sync(self, profile_id: ProfileId) -> bool:
        """Vedi `SyncPreferences.auto_sync`; un valore illeggibile vale il predefinito."""
        return parse_stored_flag(
            self._settings.value(_KEY_PREFIX + str(profile_id)), default=AUTO_SYNC_DEFAULT
        )

    def set_auto_sync(self, profile_id: ProfileId, *, enabled: bool) -> None:
        """Vedi `SyncPreferences.set_auto_sync`."""
        self._settings.setValue(_KEY_PREFIX + str(profile_id), enabled)
