# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Presentation model of Settings > Map: the tile key, the cache size and "Clear map cache".

No widgets: the dialog shows what this model answers. The key is checked with the provider, which
takes seconds, so the check runs in a worker and the result arrives as a signal; nothing else here
waits. The key itself is never shown, stored or logged by this module (it only passes through, from
the field to `TileKeySettings`); the cache size is remembered in `QSettings` (text, in GiB).
"""

from __future__ import annotations

import logging
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from enum import Enum
from typing import Final

from PySide6.QtCore import QCoreApplication, QObject, QSettings, Qt, Signal, Slot

from ponos_coach.maps.domain import DEFAULT_CACHE_BYTES, GIB, CacheLimit, parse_cache_limit_gib
from ponos_coach.maps.ports import (
    TileCache,
    TileCacheError,
    TileKeyStoreError,
    TileKeyStoreUnavailableError,
)
from ponos_coach.maps.use_cases import KeyCheck, TileKeySettings, TileProxy

_LOG = logging.getLogger(__name__)
KEY_CACHE_GIB: Final = "map/cacheGiB"


class KeyOutcome(Enum):
    """What happened to a key the user saved. The value is stable."""

    ACCEPTED = "accepted"
    REJECTED = "rejected"
    INVALID_FORMAT = "invalid_format"
    SAVED_UNVERIFIED = "saved_unverified"
    STORE_UNAVAILABLE = "store_unavailable"
    REMOVED = "removed"


_OF_CHECK: Final = {
    KeyCheck.ACCEPTED: KeyOutcome.ACCEPTED,
    KeyCheck.REJECTED: KeyOutcome.REJECTED,
    KeyCheck.INVALID_FORMAT: KeyOutcome.INVALID_FORMAT,
    KeyCheck.SAVED_UNVERIFIED: KeyOutcome.SAVED_UNVERIFIED,
}


@dataclass(frozen=True, slots=True)
class CacheSizeResult:
    """Outcome of a size typed by the user: `limit` if it was valid, `None` if not."""

    limit: CacheLimit | None


class MapSettingsModel(QObject):
    """Settings of the map as the dialog sees them."""

    key_finished = Signal(object)  # KeyOutcome
    _key_checked = Signal(object)  # KeyOutcome, from the worker

    def __init__(
        self,
        key_settings: TileKeySettings,
        proxy: TileProxy,
        cache: TileCache,
        settings: QSettings,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._key_settings = key_settings
        self._proxy = proxy
        self._cache = cache
        self._settings = settings
        self._pool: ThreadPoolExecutor | None = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="ponos-tilekey"
        )
        self._checking = False
        # Always queued: a finished future calls back in the caller's thread, and the answer
        # must not arrive before `save_key` has returned.
        self._key_checked.connect(self._on_key_checked, Qt.ConnectionType.QueuedConnection)

    # --- Key ------------------------------------------------------------------------------

    @property
    def is_checking(self) -> bool:
        """Whether a key is being checked with the provider."""
        return self._checking

    def has_key(self) -> bool:
        """Whether a key is saved (false also if the key store cannot be read)."""
        try:
            return self._key_settings.settings(self.cache_limit()).has_key
        except TileKeyStoreError:
            return False

    def save_key(self, text: str) -> bool:
        """Check the key with the provider and keep it if it is not refused; the outcome arrives
        as `key_finished`. False if a check is already running or the model was closed."""
        pool = self._pool
        if pool is None or self._checking:
            return False
        self._checking = True
        pool.submit(self._check, text).add_done_callback(self._after_check)
        return True

    def remove_key(self) -> KeyOutcome:
        """Forget the key."""
        try:
            self._key_settings.remove_key()
        except TileKeyStoreUnavailableError:
            return KeyOutcome.STORE_UNAVAILABLE
        return KeyOutcome.REMOVED

    def close(self) -> None:
        """Stop the worker (a check in progress ends by itself within its timeouts)."""
        pool, self._pool = self._pool, None
        if pool is not None:
            pool.shutdown(wait=False, cancel_futures=True)

    def _check(self, text: str) -> KeyOutcome:
        try:
            return _OF_CHECK[self._key_settings.set_key(text)]
        except TileKeyStoreUnavailableError:
            return KeyOutcome.STORE_UNAVAILABLE

    def _after_check(self, future: Future[KeyOutcome]) -> None:
        """Worker thread: hand the outcome to the UI thread (never the key, never the error)."""
        try:
            outcome = future.result()
        except Exception as error:  # worker boundary: the dialog gets a plain failure
            _LOG.error("Checking the tile key failed: %s", type(error).__name__)
            outcome = KeyOutcome.STORE_UNAVAILABLE
        try:
            self._key_checked.emit(outcome)
        except RuntimeError:  # the model was destroyed while the check ran
            return

    @Slot(object)
    def _on_key_checked(self, outcome: object) -> None:
        self._checking = False
        if isinstance(outcome, KeyOutcome):
            self.key_finished.emit(outcome)

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


def outcome_text(outcome: KeyOutcome) -> str:
    """What the user is told after saving or removing a key, with what to do next."""
    texts = {
        KeyOutcome.ACCEPTED: QCoreApplication.translate(
            "MapSettings", "Key saved. Tracestrack accepted it."
        ),
        KeyOutcome.REJECTED: QCoreApplication.translate(
            "MapSettings",
            "Tracestrack refused this key, so it was not saved. Copy it again from your "
            "Tracestrack account and paste it here.",
        ),
        KeyOutcome.INVALID_FORMAT: QCoreApplication.translate(
            "MapSettings",
            "This is not a Tracestrack key. A key has 8 to 128 letters, digits, hyphens or "
            "underscores. Copy it again from your Tracestrack account.",
        ),
        KeyOutcome.SAVED_UNVERIFIED: QCoreApplication.translate(
            "MapSettings",
            "Key saved, but Tracestrack could not be asked (no network, or the credit is used "
            "up). The map will use it when it can.",
        ),
        KeyOutcome.STORE_UNAVAILABLE: QCoreApplication.translate(
            "MapSettings",
            "The system key store is not available, so the key cannot be saved. On Linux, "
            "install and unlock a Secret Service provider (for example GNOME Keyring), then try "
            "again.",
        ),
        KeyOutcome.REMOVED: QCoreApplication.translate(
            "MapSettings", "Key removed. The map shows a plain background."
        ),
    }
    return texts[outcome]
