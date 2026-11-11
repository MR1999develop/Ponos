# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Ports of the assessment feature: the history of the threshold tests.

The implementation shares the profile's connection (`ActivityIndex.transaction`), so applying a
test and saving the threshold it produced are all or nothing. Health data: never in logs or
error messages.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from ponos_coach.activities.domain import ActivityIdentity
from ponos_coach.assessment.domain import ThresholdTest


class AssessmentStoreError(Exception):
    """Root of the errors of the threshold test history (unreadable data, failed write)."""


class ThresholdTestAlreadyStoredError(AssessmentStoreError):
    """This activity is already in the history as a test; nothing changes."""


class ThresholdTestNotFoundError(AssessmentStoreError):
    """This activity is not in the history as a test."""


class ThresholdTestStore(Protocol):
    """History of the applied threshold tests, one per activity."""

    def add(self, test: ThresholdTest) -> None:
        """Saves a test.

        Raises:
            ThresholdTestAlreadyStoredError: the activity is already a test; nothing changes.
            AssessmentStoreError: write failed.
        """
        ...

    def remove(self, identity: ActivityIdentity) -> None:
        """Deletes the test made on this activity.

        Raises:
            ThresholdTestNotFoundError: there is none; nothing changes.
            AssessmentStoreError: write failed.
        """
        ...

    def get(self, identity: ActivityIdentity) -> ThresholdTest | None:
        """The test made on this activity, or None.

        Raises:
            AssessmentStoreError: unreadable or invalid data.
        """
        ...

    def list_tests(self) -> Sequence[ThresholdTest]:
        """All the tests by ascending day, then segment start, then activity key.

        Raises:
            AssessmentStoreError: unreadable or invalid data.
        """
        ...
