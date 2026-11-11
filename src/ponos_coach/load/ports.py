# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Ports of the load feature: physiological parameters and load cache.

The parameters are user data (source of truth); the loads are derived and regenerable.
The implementations share the profile's connection (`ActivityIndex.transaction`).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from typing import Protocol

from ponos_coach.activities.domain import ActivityIdentity
from ponos_coach.load.domain import ActivityLoad, LoadFingerprint, PhysiologyEntry


class LoadStoreError(Exception):
    """Root of the errors of PhysiologyStore and LoadCache."""


class UnknownActivityError(LoadStoreError):
    """The activity is not in the index: a load can only be saved for a known activity."""


class PhysiologyEntryNotFoundError(LoadStoreError):
    """There is no physiological parameter entry starting on that day."""


class PhysiologyStore(Protocol):
    """Physiological parameters with validity date."""

    def put(self, entry: PhysiologyEntry) -> None:
        """Saves the parameters valid from `entry.valid_from`.

        An entry with the same date as an existing one replaces it (correction of a value).
        """
        ...

    def remove(self, valid_from: date) -> None:
        """Deletes the entry whose validity starts on `valid_from`.

        Raises:
            PhysiologyEntryNotFoundError: there is none; nothing changes.
            LoadStoreError: write failed.
        """
        ...

    def list_entries(self) -> Sequence[PhysiologyEntry]:
        """All the entries by ascending validity date (only one per date)."""
        ...


class LoadCache(Protocol):
    """Loads per activity, valid only for the fingerprint of the parameters that produced them."""

    def get(self, identity: ActivityIdentity, fingerprint: LoadFingerprint) -> ActivityLoad | None:
        """The saved load, only if produced with this fingerprint; otherwise None."""
        ...

    def put(
        self, identity: ActivityIdentity, fingerprint: LoadFingerprint, load: ActivityLoad
    ) -> None:
        """Saves (or replaces) the load of the activity with its fingerprint.

        Raises:
            UnknownActivityError: the activity is not in the index.
        """
        ...

    def invalidate(self, identity: ActivityIdentity) -> None:
        """Forgets the load of an activity (no effect if there is none)."""
        ...

    def clear(self) -> None:
        """Forgets all the loads (parameters change: they are recomputed from the files)."""
        ...
