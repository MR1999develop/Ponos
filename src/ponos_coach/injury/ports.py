# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Ports of the injury feature: the injuries and illnesses of the profile.

The implementation shares the profile's connection (`ActivityIndex.transaction`), so a change that
touches several records is all or nothing. Health data: never in logs or error messages.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from ponos_coach.injury.domain import InjuryId, Problem


class InjuryStoreError(Exception):
    """Root of the errors of the injury store (unreadable data, failed write)."""


class InjuryNotFoundError(InjuryStoreError):
    """There is no injury or illness with that id; nothing changes."""


class InjuryAlreadyExistsError(InjuryStoreError):
    """An injury or illness with that id is already saved; nothing changes."""


class InjuryStore(Protocol):
    """The injuries and illnesses of the profile, ids chosen by the caller."""

    def add(self, problem: Problem) -> None:
        """Saves a new injury or illness with all its check-ins.

        Raises:
            InjuryAlreadyExistsError: the id is taken.
            InjuryStoreError: write failed.
        """
        ...

    def replace(self, problem: Problem) -> None:
        """Replaces the record with the same id, check-ins included, all or nothing.

        Raises:
            InjuryNotFoundError: there is none with that id.
            InjuryStoreError: write failed.
        """
        ...

    def remove(self, injury_id: InjuryId) -> None:
        """Deletes the record and its check-ins.

        Raises:
            InjuryNotFoundError: there is none; nothing changes.
            InjuryStoreError: write failed.
        """
        ...

    def get(self, injury_id: InjuryId) -> Problem | None:
        """The record with that id, or None.

        Raises:
            InjuryStoreError: unreadable or invalid data.
        """
        ...

    def list_problems(self) -> Sequence[Problem]:
        """All the records by ascending start day, then id.

        Raises:
            InjuryStoreError: unreadable or invalid data.
        """
        ...
