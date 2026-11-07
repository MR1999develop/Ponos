# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Synchronisation preferences, remembered per profile.

Today just one: whether synchronisation starts by itself when the profile opens (default:
yes). The controller uses the `SyncPreferences` port; `__main__` composes the
`QSettings` implementation, the in-memory one serves tests and cases without settings.
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
    """The user's choices about synchronisation, one per profile."""

    def auto_sync(self, profile_id: ProfileId) -> bool:
        """True if synchronisation starts by itself when the profile opens."""
        ...

    def set_auto_sync(self, profile_id: ProfileId, *, enabled: bool) -> None:
        """Remember the choice for the profile."""
        ...


class MemorySyncPreferences:
    """Preferences that do not survive the process."""

    def __init__(self) -> None:
        self._auto: dict[ProfileId, bool] = {}

    def auto_sync(self, profile_id: ProfileId) -> bool:
        """See `SyncPreferences.auto_sync`."""
        return self._auto.get(profile_id, AUTO_SYNC_DEFAULT)

    def set_auto_sync(self, profile_id: ProfileId, *, enabled: bool) -> None:
        """See `SyncPreferences.set_auto_sync`."""
        self._auto[profile_id] = enabled


def parse_stored_flag(raw: object, *, default: bool) -> bool:
    """The value read from `QSettings` (bool, "true"/"false" or other): what is not understood
    gets the default, never an error."""
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
    """Preferences in the app's settings, with one key per profile."""

    def __init__(self, settings: QSettings) -> None:
        self._settings = settings

    def auto_sync(self, profile_id: ProfileId) -> bool:
        """See `SyncPreferences.auto_sync`; an unreadable value gets the default."""
        return parse_stored_flag(
            self._settings.value(_KEY_PREFIX + str(profile_id)), default=AUTO_SYNC_DEFAULT
        )

    def set_auto_sync(self, profile_id: ProfileId, *, enabled: bool) -> None:
        """See `SyncPreferences.set_auto_sync`."""
        self._settings.setValue(_KEY_PREFIX + str(profile_id), enabled)
