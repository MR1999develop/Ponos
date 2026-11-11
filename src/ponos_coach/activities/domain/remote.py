# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Activity from the remote Garmin list, list pages and matching with a file import.

Everything is already validated: the adapter turns the JSON response into these types at the
boundary, or
discards the entry. No free text from Garmin (names, descriptions) enters here.
"""

from __future__ import annotations

import math
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Final

from ponos_coach.activities.domain.duplicates import is_possible_duplicate
from ponos_coach.activities.domain.garmin_id import GarminActivityId
from ponos_coach.activities.domain.identity import identity_key
from ponos_coach.activities.domain.record import ActivityRecord
from ponos_coach.activities.domain.sport import Sport
from ponos_coach.activities.domain.timing import check_utc, is_plausible_span

MAX_PAGE_SIZE: Final = 1000  # entries per page: beyond this, the response is suspicious
DEFAULT_PAGE_SIZE: Final = 100  # entries per page requested from Garmin (project default)
_SPORT_KEY: Final = re.compile(r"[a-z][a-z0-9_]{0,63}")

# Garmin activity type keys (`typeKey`) -> app sport. The rest is `OTHER`.
_SPORT_BY_KEY: Final[dict[str, Sport]] = {
    # Known Garmin subkeys: project default, to be confirmed with the real structure
    # of the responses (spike); unknown keys end up in OTHER.
    "running": Sport.RUNNING,
    "street_running": Sport.RUNNING,
    "treadmill_running": Sport.RUNNING,
    "indoor_running": Sport.RUNNING,
    "track_running": Sport.RUNNING,
    "virtual_run": Sport.RUNNING,
    "ultra_run": Sport.RUNNING,
    "obstacle_run": Sport.RUNNING,
    "trail_running": Sport.TRAIL,
    "cycling": Sport.CYCLING,
    "road_biking": Sport.CYCLING,
    "mountain_biking": Sport.CYCLING,
    "gravel_cycling": Sport.CYCLING,
    "cyclocross": Sport.CYCLING,
    "track_cycling": Sport.CYCLING,
    "recumbent_cycling": Sport.CYCLING,
    "downhill_biking": Sport.CYCLING,
    "bmx": Sport.CYCLING,
    "indoor_cycling": Sport.CYCLING,
    "virtual_ride": Sport.CYCLING,
    "e_bike_fitness": Sport.CYCLING,
    "e_bike_mountain": Sport.CYCLING,
    "strength_training": Sport.STRENGTH,
    "hiking": Sport.HIKING,
    "walking": Sport.WALKING,
    "casual_walking": Sport.WALKING,
    "speed_walking": Sport.WALKING,
    "indoor_walking": Sport.WALKING,
}


def sport_from_garmin_key(sport_key: str) -> Sport:
    """App sport for the Garmin key; `Sport.OTHER` if it does not know it."""
    return _SPORT_BY_KEY.get(sport_key, Sport.OTHER)


@dataclass(frozen=True, slots=True)
class RemoteActivity:
    """Summary of an activity in the remote list: what is enough to decide whether to download it.

    `start` is UTC to the millisecond and `start` + `duration_s` lies in the window of allowed
    times; `duration_s` is finite and positive; `sport_key` is the Garmin key in lowercase
    (`[a-z][a-z0-9_]{0,63}`); `has_heart_rate` is None if the list does not say.
    """

    garmin_id: GarminActivityId
    start: datetime
    duration_s: float
    sport_key: str
    has_heart_rate: bool | None = None

    def __post_init__(self) -> None:
        check_utc(self.start)
        if self.start.microsecond % 1000:
            raise ValueError("the start must be at millisecond precision")
        if not math.isfinite(self.duration_s) or self.duration_s <= 0:
            raise ValueError("the duration must be finite and positive")
        if not is_plausible_span(self.start, self.duration_s):
            raise ValueError("start or end outside the window of allowed times")
        if not _SPORT_KEY.fullmatch(self.sport_key):
            raise ValueError("invalid sport key")

    @property
    def sport(self) -> Sport:
        """The app sport corresponding to the Garmin key."""
        return sport_from_garmin_key(self.sport_key)


@dataclass(frozen=True, slots=True)
class ActivityPage:
    """A page of the remote list, most recent first.

    `offset` is the requested position; `raw_count` how many entries Garmin returned, including
    those the adapter discarded as malformed (`items` has fewer or as many):
    the next position is `offset + raw_count`. `declared_total` is the total declared by
    Garmin, if it says so. The ids in `items` are distinct.
    """

    offset: int
    items: tuple[RemoteActivity, ...]
    raw_count: int
    declared_total: int | None = None

    def __post_init__(self) -> None:
        if self.offset < 0 or not len(self.items) <= self.raw_count <= MAX_PAGE_SIZE:
            raise ValueError("inconsistent page: position or counts out of range")
        if self.declared_total is not None and self.declared_total < 0:
            raise ValueError("the declared total cannot be negative")
        if len({item.garmin_id for item in self.items}) != len(self.items):
            raise ValueError("repeated ids in the page")

    @property
    def skipped_count(self) -> int:
        """Entries returned by Garmin but discarded as malformed."""
        return self.raw_count - len(self.items)


def find_matching_import(
    remote: RemoteActivity, candidates: Sequence[ActivityRecord]
) -> ActivityRecord | None:
    """The activity already imported from file that matches the remote one, or None.

    It matches if it has no Garmin id yet, has the same sport and `is_possible_duplicate`
    (starts within 60 s, durations within 5%). With several candidates the closest wins
    in start, then the smallest identity key: the choice is deterministic.
    `candidates` are usually the results of `ActivityIndex.find_possible_duplicates`.
    """
    matching = [
        record
        for record in candidates
        if record.garmin_activity_id is None
        and record.sport is remote.sport
        and is_possible_duplicate(remote.start, remote.duration_s, record.start, record.elapsed_s)
    ]
    if not matching:
        return None
    return min(matching, key=lambda r: (abs(r.start - remote.start), identity_key(r.identity)))


def original_matches_listing(remote: RemoteActivity, start: datetime, elapsed_s: float) -> bool:
    """True if a downloaded file (start `start`, elapsed time `elapsed_s`) is the activity
    in the list: same rule as duplicates (starts within 60 s, durations within 5%).

    A file that does not match belongs to another activity: it is not recorded for that id.
    """
    return is_possible_duplicate(remote.start, remote.duration_s, start, elapsed_s)
