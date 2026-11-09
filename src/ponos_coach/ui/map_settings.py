# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Presentation model of Settings > Map: the cache size and "Clear map cache".

No widgets: the dialog shows what this model answers. The cache size is remembered in `QSettings`
(text, in GiB).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from PySide6.QtCore import QObject, QSettings

from ponos_coach.maps.domain import DEFAULT_CACHE_BYTES, GIB, CacheLimit, parse_cache_limit_gib
from ponos_coach.maps.ports import TileCache, TileCacheError
from ponos_coach.maps.use_cases import TileProxy

KEY_CACHE_GIB: Final = "map/cacheGiB"


@dataclass(frozen=True, slots=True)
class CacheSizeResult:
    """Outcome of a size typed by the user: `limit` if it was valid, `None` if not."""

    limit: CacheLimit | None


class MapSettingsModel(QObject):
    """Settings of the map as the dialog sees them."""

    def __init__(
        self,
        proxy: TileProxy,
        cache: TileCache,
        settings: QSettings,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._proxy = proxy
        self._cache = cache
        self._settings = settings

    # --- Cache ---------------------------------------------------------------------------

    def cache_limit(self) -> CacheLimit:
        """The cap chosen by the user (the default if none was saved or the saved one is wrong)."""
        return saved_cache_limit(self._settings)

    def set_cache_limit(self, text: str) -> CacheSizeResult:
        """Apply and remember the size typed in GiB (comma or point); invalid text changes
        nothing."""
        try:
            limit = parse_cache_limit_gib(text)
        except ValueError:
            return CacheSizeResult(None)
        try:
            self._cache.set_limit(limit.bytes)
        except TileCacheError:
            return CacheSizeResult(None)
        self._settings.setValue(KEY_CACHE_GIB, f"{limit.bytes / GIB:g}")
        return CacheSizeResult(limit)

    def cache_used_bytes(self) -> int:
        """Bytes the stored tiles count against the cap."""
        return self._cache.used_bytes()

    def clear_cache(self) -> int | None:
        """Empty the cache; the bytes freed, or `None` if it could not be done."""
        before = self._cache.used_bytes()
        try:
            self._proxy.clear_cache()
        except TileCacheError:
            return None
        return before


_DEFAULT: Final = CacheLimit(DEFAULT_CACHE_BYTES)


def saved_cache_limit(settings: QSettings) -> CacheLimit:
    """The cap the user chose (4 GB if none was saved or the saved text is not a valid size)."""
    raw = settings.value(KEY_CACHE_GIB, "")
    try:
        return parse_cache_limit_gib(raw) if isinstance(raw, str) and raw else _DEFAULT
    except ValueError:
        return _DEFAULT
