# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Use cases of the form overview: activity load, PMC, sport weights.

- `ComputeProfileOverview`: overview of a profile (index, archive, load cache):
  local day, load computation, PMC series.
- `GetSportWeights` and `SetSportWeights`: read and set the profile's sport weights.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from types import MappingProxyType
from zoneinfo import ZoneInfo

from ponos_coach.activities.decoding import ActivityFileError
from ponos_coach.activities.domain import (
    MAX_ACTIVITY_FILE_BYTES,
    Activity,
    ActivityIdentity,
    ActivityRecord,
    ActivitySummary,
    FileFormat,
    Sport,
)
from ponos_coach.activities.ports import (
    ActivityArchive,
    ActivityIndex,
    ArchiveError,
    ArchiveFileMissingError,
    ArchiveIntegrityError,
    ArchiveTooLargeError,
)
from ponos_coach.load.domain import (
    ActivityLoad,
    DayLoad,
    LoadFingerprint,
    Physiology,
    PmcSeries,
    SportWeight,
    WeeklyLoad,
    WeeklyZones,
    compute_activity_load,
    compute_pmc,
    customised_weights,
    effective_weight,
    effective_weights,
    load_fingerprint,
    physiology_for_day,
    weekly_load_by_sport,
    weekly_zone_seconds,
)
from ponos_coach.load.ports import LoadCache, PhysiologyStore, SportWeightStore


def local_day_of(start: datetime, timezone: ZoneInfo) -> date:
    """Local day (in the profile's time zone) of the start of an activity."""
    return start.astimezone(timezone).date()


def load_of_activity(
    activity: Activity, physiology: Physiology, saved_weights: Mapping[Sport, SportWeight]
) -> ActivityLoad:
    """Load of the activity over the moving time: the single computation point, used by the
    overview and by the detail. The sport weight is the saved one or, without it, the default."""
    return compute_activity_load(
        activity.sport,
        activity.timer_s,
        activity.heart_rate,
        physiology,
        effective_weight(activity.sport, saved_weights),
    )


def _pmc_of(day_loads: Iterable[tuple[date, float]], today: date) -> PmcSeries:
    """PMC from the first day with activity to `today`; empty if there are none (or they are in
    the future)."""
    daily: dict[date, float] = defaultdict(float)
    for day, load in day_loads:
        daily[day] += load
    if not daily:
        return PmcSeries.empty(today)
    first_day = min(daily)
    if first_day > today:
        return PmcSeries.empty(today)
    return compute_pmc(daily, first_day, today)


# --- Overview of a profile ---------------------------------------------------------------


class ExclusionReason(Enum):
    """Why an activity of the profile does not enter the overview."""

    FILE_MISSING = "file_missing"  # the file is no longer in the archive
    FILE_ALTERED = "file_altered"  # different size or SHA-256, or different identity
    FILE_TOO_LARGE = "file_too_large"  # beyond the byte cap
    UNREADABLE = "unreadable"  # other archive error
    DECODE_FAILED = "decode_failed"  # the content is not a valid activity


class OverviewCancelledError(Exception):
    """The computation was cancelled by the user; no partial result."""


type Decoder = Callable[[bytes, FileFormat], Activity]
"""Decodes the bytes of an archived file; raises `ActivityFileError` if it is not valid."""


@dataclass(frozen=True, slots=True)
class ProfileActivity:
    """An activity of the profile with its load and summary (distance, ascent).

    `start` is in UTC, `local_day` the local date in the profile's time zone; `load.source` says
    whether the
    load comes from HR or from an estimate.
    """

    identity: ActivityIdentity
    sport: Sport
    start: datetime
    local_day: date
    elapsed_s: float
    timer_s: float
    summary: ActivitySummary
    load: ActivityLoad


@dataclass(frozen=True, slots=True)
class ExcludedActivity:
    """An activity that could not be computed and the reason."""

    identity: ActivityIdentity
    reason: ExclusionReason


@dataclass(frozen=True, slots=True)
class ProfileOverview:
    """Outcome: PMC from the first day with activity to `today` (empty without activities),
    activities with
    load by ascending start, excluded ones in the same order."""

    pmc: PmcSeries
    activities: tuple[ProfileActivity, ...]
    excluded: tuple[ExcludedActivity, ...]

    def day_loads(self) -> tuple[DayLoad, ...]:
        """The activities in the shape of the aggregations (local day, sport, load, zones)."""
        return tuple(
            DayLoad(a.local_day, a.sport, a.load.load, a.load.zone_seconds) for a in self.activities
        )

    def weekly_load(self, through: date) -> WeeklyLoad:
        """Load per ISO week and sport up to the week of `through` (usually today)."""
        return weekly_load_by_sport(self.day_loads(), through)

    def weekly_zones(self, through: date) -> WeeklyZones:
        """Seconds per HR zone and ISO week up to the week of `through`."""
        return weekly_zone_seconds(self.day_loads(), through)


def read_original_bytes(
    archive: ActivityArchive, record: ActivityRecord, max_file_bytes: int
) -> bytes | ExclusionReason:
    """Bytes of the original from the archive (hash-verified, with the byte cap), or the reason."""
    try:
        return archive.read(record.archived, max_file_bytes)
    except ArchiveFileMissingError:
        return ExclusionReason.FILE_MISSING
    except ArchiveIntegrityError:
        return ExclusionReason.FILE_ALTERED
    except ArchiveTooLargeError:
        return ExclusionReason.FILE_TOO_LARGE
    except ArchiveError:
        return ExclusionReason.UNREADABLE


def read_original_activity(
    archive: ActivityArchive, record: ActivityRecord, decode: Decoder, max_file_bytes: int
) -> Activity | ExclusionReason:
    """Reads the original from the archive (with the byte cap) and decodes it.

    A failure does not raise: it gives the reason (missing file, altered, too large, unreadable,
    not decodable, or with an identity different from the record's).
    """
    data = read_original_bytes(archive, record, max_file_bytes)
    if isinstance(data, ExclusionReason):
        return data
    try:
        activity = decode(data, record.file_format)
    except ActivityFileError:
        return ExclusionReason.DECODE_FAILED
    if activity.identity != record.identity:
        return ExclusionReason.FILE_ALTERED
    return activity


def _never_cancelled() -> bool:
    return False


def _ignore_progress(_done: int, _total: int) -> None:
    return None


type _Computed = tuple[ActivityIdentity, LoadFingerprint, ActivityLoad]


class ComputeProfileOverview:
    """Computes the overview of the open profile.

    For each activity in the index: local day, parameters valid that day, fingerprint; if the
    cache has the load with that fingerprint nothing is read, otherwise the original is read
    from the archive (with the byte cap), decoded, and the load is computed over the moving
    time. A failure on an activity puts it among the excluded ones without stopping the others.
    Computed loads are written to the cache in a single transaction at the end (even after a
    cancellation). Deterministic: same data and parameters, same result.

    Sport weights are those saved in the profile (`sport_weights`), read at every computation;
    a sport without a saved weight uses the default. An activity's fingerprint includes only the
    weight of its sport: changing the bike's does not recompute the runs.

    Volatile dependencies injected: ports, `decode` (supplied by the Composition Root), `today`
    (today, in the profile's time zone), `is_cancelled` (cooperative cancellation, checked before
    every activity) and `report(done, total)` for progress.
    """

    def __init__(  # noqa: PLR0913 - every port is a distinct volatile dependency
        self,
        index: ActivityIndex,
        archive: ActivityArchive,
        physiology: PhysiologyStore,
        cache: LoadCache,
        decode: Decoder,
        *,
        sport_weights: SportWeightStore,
        timezone: ZoneInfo,
        today: Callable[[], date],
        max_file_bytes: int = MAX_ACTIVITY_FILE_BYTES,
        is_cancelled: Callable[[], bool] = _never_cancelled,
        report: Callable[[int, int], None] = _ignore_progress,
    ) -> None:
        if max_file_bytes <= 0:
            raise ValueError("the byte cap must be positive")
        self._index = index
        self._archive = archive
        self._physiology = physiology
        self._cache = cache
        self._decode = decode
        self._timezone = timezone
        self._today = today
        self._sport_weights = sport_weights
        self._max_file_bytes = max_file_bytes
        self._is_cancelled = is_cancelled
        self._report = report

    def execute(self) -> ProfileOverview:
        """Computes the overview.

        Raises:
            OverviewCancelledError: `is_cancelled` became true.
            MissingPhysiologyError: there are activities but no physiological parameter.
        """
        records = self._index.list_by_start()
        today = self._today()
        entries = self._physiology.list_entries() if records else ()
        saved_weights = self._sport_weights.load() if records else {}
        activities: list[ProfileActivity] = []
        excluded: list[ExcludedActivity] = []
        computed: list[_Computed] = []
        self._report(0, len(records))
        try:
            for done, record in enumerate(records, start=1):
                if self._is_cancelled():
                    raise OverviewCancelledError
                local_day = local_day_of(record.start, self._timezone)
                physiology = physiology_for_day(entries, local_day)
                saved = saved_weights.get(record.sport)
                fingerprint = load_fingerprint(
                    physiology, {} if saved is None else {record.sport: saved}
                )
                load = self._cache.get(record.identity, fingerprint)
                if load is None:
                    outcome = self._compute(record, physiology, saved_weights)
                    if isinstance(outcome, ExclusionReason):
                        excluded.append(ExcludedActivity(record.identity, outcome))
                    else:
                        computed.append((record.identity, fingerprint, outcome))
                        activities.append(_profile_activity(record, local_day, outcome))
                else:
                    activities.append(_profile_activity(record, local_day, load))
                self._report(done, len(records))
        finally:
            self._store(computed)
        pmc = _pmc_of(((a.local_day, a.load.load) for a in activities), today)
        return ProfileOverview(pmc, tuple(activities), tuple(excluded))

    def _compute(
        self,
        record: ActivityRecord,
        physiology: Physiology,
        saved_weights: Mapping[Sport, SportWeight],
    ) -> ActivityLoad | ExclusionReason:
        """Load from the original in the archive, or the reason why it cannot be computed."""
        activity = read_original_activity(self._archive, record, self._decode, self._max_file_bytes)
        if isinstance(activity, ExclusionReason):
            return activity
        return load_of_activity(activity, physiology, saved_weights)

    def _store(self, computed: list[_Computed]) -> None:
        if not computed:
            return
        with self._index.transaction():
            for identity, fingerprint, load in computed:
                self._cache.put(identity, fingerprint, load)


def _profile_activity(
    record: ActivityRecord, local_day: date, load: ActivityLoad
) -> ProfileActivity:
    return ProfileActivity(
        record.identity, record.sport, record.start, local_day,
        record.elapsed_s, record.timer_s, record.summary, load,
    )  # fmt: skip


# --- Sport weights -----------------------------------------------------------------------


class GetSportWeights:
    """Reads the profile's sport weights: those chosen by the user, the default for the rest."""

    def __init__(self, store: SportWeightStore) -> None:
        self._store = store

    def __call__(self) -> Mapping[Sport, SportWeight]:
        """The weight of each sport, all present.

        Raises:
            LoadStoreError: saved weights unreadable.
        """
        return MappingProxyType(effective_weights(self._store.load()))


class SetSportWeights:
    """Sets the profile's sport weights.

    Only the weights different from the default are saved (canonical form: no useless recomputation
    of the
    loads). Loads already computed with other weights are recomputed on their own at the next
    overview, because the fingerprint changes.
    """

    def __init__(self, activities: ActivityIndex, store: SportWeightStore) -> None:
        self._activities = activities
        self._store = store

    def __call__(self, weights: Mapping[Sport, SportWeight]) -> None:
        """Replaces the saved weights with `weights` (sports not indicated go back to the default).

        Raises:
            LoadStoreError: write failed; the previous weights stay.
        """
        with self._activities.transaction():
            self._store.replace(customised_weights(weights))
