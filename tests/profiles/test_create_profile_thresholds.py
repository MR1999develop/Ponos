# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""CreateProfile: cycling and other-activities thresholds and the threshold pace enter the
first entries, in the same transaction as the rest."""

from __future__ import annotations

from pathlib import Path

import pytest

from ponos_coach.load.domain import (
    HeartRateLimits,
    Physiology,
    PhysiologyEntry,
    Sex,
    ThresholdHeartRate,
)
from ponos_coach.profiles.domain import (
    ProfileId,
    ProfileName,
    ThresholdPace,
    ThresholdPaceEntry,
    Weight,
)
from ponos_coach.profiles.ports import OpenProfile, ProfileStorageError
from ponos_coach.profiles.use_cases import NewProfileRequest
from tests.builders import absolute_path
from tests.fakes.profile_storage import InMemoryProfileStorage
from tests.profiles.test_use_cases import NAME, TODAY, World, world  # noqa: F401


def full_physiology() -> Physiology:
    return Physiology(
        HeartRateLimits(50, 190),
        ThresholdHeartRate(170),
        Sex.FEMALE,
        other_threshold=ThresholdHeartRate(160),
        run_lt1=ThresholdHeartRate(140),
        other_lt1=ThresholdHeartRate(130),
    )


def test_the_thresholds_and_the_pace_enter_the_first_entries_with_the_same_date(
    world: World,  # noqa: F811
) -> None:
    folder = absolute_path("profili", "atleta")
    request = NewProfileRequest(
        NAME,
        folder,
        "Europe/Rome",
        full_physiology(),
        TODAY,
        weight=Weight.parse("65,5"),
        threshold_pace=ThresholdPace(330),
    )

    with world.create()(request) as session:
        assert list(session.physiology.list_entries()) == [
            PhysiologyEntry(TODAY, full_physiology())
        ]
        assert list(session.threshold_paces.list_entries()) == [
            ThresholdPaceEntry(TODAY, ThresholdPace(330))
        ]


def test_without_a_pace_the_series_stays_empty(world: World) -> None:  # noqa: F811
    request = NewProfileRequest(
        NAME, absolute_path("profili", "atleta"), "Europe/Rome", full_physiology(), TODAY
    )

    with world.create()(request) as session:
        assert list(session.threshold_paces.list_entries()) == []
        [entry] = session.physiology.list_entries()
        assert entry.physiology.other_threshold == ThresholdHeartRate(160)
        assert entry.physiology.run_lt1 == ThresholdHeartRate(140)
        assert entry.physiology.other_lt1 == ThresholdHeartRate(130)


class _PaceWriteFails(InMemoryProfileStorage):
    """Database whose pace series cannot be written."""

    def create(
        self, folder: Path, profile_id: ProfileId, name: ProfileName, timezone_name: str
    ) -> OpenProfile:
        opened = super().create(folder, profile_id, name, timezone_name)

        def failing_put(entry: ThresholdPaceEntry) -> None:
            del entry
            raise ProfileStorageError("disk full")

        object.__setattr__(opened.threshold_paces, "put", failing_put)
        return opened


def test_a_failure_writing_the_pace_leaves_no_profile_no_folder_and_no_lock(
    world: World,  # noqa: F811
) -> None:
    folder = absolute_path("profili", "atleta")
    request = NewProfileRequest(
        NAME,
        folder,
        "Europe/Rome",
        full_physiology(),
        TODAY,
        threshold_pace=ThresholdPace(330),
    )

    with pytest.raises(ProfileStorageError):
        world.create(world.ports(_PaceWriteFails(world.disk)))(request)

    assert world.registry.list_entries() == []
    assert folder not in world.disk.folders
    assert folder not in world.disk.databases
    assert folder not in world.disk.locks
