# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""The map background the user chose, remembered in `QSettings`.

One object holds the choice; the Map menu and the control on the map both change it and both
listen to it, so they cannot disagree. Only a background of the closed set `BACKGROUNDS` can be
chosen: what `QSettings` holds is looked up in that set (an unknown or damaged value is the
default, OpenStreetMap).
"""

from __future__ import annotations

from typing import Final

from PySide6.QtCore import QObject, QSettings, Signal

from ponos_coach.maps.domain import (
    DEFAULT_BACKGROUND,
    MapBackground,
    TileAddressError,
    background_by_key,
)

KEY_BACKGROUND: Final = "map/background"


def saved_background(settings: QSettings) -> MapBackground:
    """The background the user chose (OpenStreetMap if none was saved or the value is unknown)."""
    raw = settings.value(KEY_BACKGROUND, "")
    try:
        return background_by_key(raw) if isinstance(raw, str) else DEFAULT_BACKGROUND
    except TileAddressError:
        return DEFAULT_BACKGROUND


class BackgroundChoice(QObject):
    """The current map background. `changed` carries the new `MapBackground`."""

    changed = Signal(object)

    def __init__(self, settings: QSettings, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._settings = settings
        self._current = saved_background(settings)

    @property
    def current(self) -> MapBackground:
        """The background in use."""
        return self._current

    def choose(self, background: MapBackground) -> None:
        """Use and remember this background; nothing happens if it is already the one in use."""
        if background == self._current:
            return
        self._current = background
        self._settings.setValue(KEY_BACKGROUND, background.key)
        self.changed.emit(background)
