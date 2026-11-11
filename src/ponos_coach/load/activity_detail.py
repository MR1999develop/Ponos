# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Detail of an activity: HR trace, zones valid that day, summary and load.

Read-only use case: reads the original from the archive, decodes it and computes the load
with the same rules and parameters as the overview (parameters valid on the activity's
local day), without going through the cache and without writing to it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

import numpy as np
from numpy.typing import NDArray

from ponos_coach.activities.decoding import ActivityFileError
from ponos_coach.activities.domain import (
    MAX_ACTIVITY_FILE_BYTES,
    ActivityIdentity,
    ActivitySummary,
    HeartRateSeries,
    SpeedSeries,
    Sport,
    smoothed_speed,
)
from ponos_coach.activities.ports import ActivityArchive, ActivityIndex, ActivityNotFoundError
from ponos_coach.load.domain import (
    ActivityLoad,
    DomainSeconds,
    HeartRateLimits,
    ZoneLoad,
    domain_seconds,
    load_per_zone,
    lt1_for_sport,
    out_of_limits,
    physiology_for_day,
    threshold_for_sport,
    zone_starts_bpm,
)
from ponos_coach.load.ports import PhysiologyStore
from ponos_coach.load.use_cases import (
    Decoder,
    ExcludedActivity,
    ExclusionReason,
    load_of_activity,
    local_day_of,
    read_original_bytes,
)


@dataclass(frozen=True, slots=True, eq=False)
class HeartRateTrace:
    """HR trace: seconds from the start, bpm and which samples the load discards.

    `discarded[i]` is true if `bpm[i]` is outside `[min HR, max HR]` of that day. Read-only
    arrays, of the same length.
    """

    seconds: NDArray[np.float64]
    bpm: NDArray[np.float64]
    discarded: NDArray[np.bool_]

    def __post_init__(self) -> None:
        if not self.seconds.shape == self.bpm.shape == self.discarded.shape:
            raise ValueError("the trace columns have different lengths")
        for array in (self.seconds, self.bpm, self.discarded):
            array.setflags(write=False)

    @classmethod
    def of(cls, series: HeartRateSeries, limits: HeartRateLimits) -> HeartRateTrace:
        """Trace of a series with the limits valid that day."""
        return cls(series.seconds, series.bpm, out_of_limits(series, limits))

    @property
    def discarded_count(self) -> int:
        """How many samples are marked as discarded."""
        return int(np.count_nonzero(self.discarded))


@dataclass(frozen=True, slots=True)
class ActivityDetail:
    """Everything needed to show an activity.

    `trace` is None if the activity has no HR. `limits` and `threshold_bpm` are those valid that
    day for the sport; `zone_starts_bpm` are the HRs where Z2, Z3, Z4 and Z5 start (Z1 is
    below the first). `unordered_hr_samples` counts the HR samples discarded by the file reader
    because of repeated or backwards times; samples out of limits are in `trace` and in
    `load.discarded_samples`. `zone_load` is the load earned in each zone (it adds up to
    `load.trimp_eq`); None when the load is an estimate.
    The GPS track is not read here: the detail window does not draw it.
    """

    identity: ActivityIdentity
    sport: Sport
    start: datetime
    local_day: date
    elapsed_s: float
    timer_s: float
    summary: ActivitySummary
    load: ActivityLoad
    trace: HeartRateTrace | None
    limits: HeartRateLimits
    threshold_bpm: int
    lt1_bpm: int
    zone_starts_bpm: tuple[float, float, float, float]
    unordered_hr_samples: int
    zone_load: ZoneLoad | None = None
    domain_seconds: DomainSeconds | None = None
    speed: SpeedSeries | None = None


class GetActivityDetail:
    """Detail of an activity of the open profile.

    Dependencies: index, archive, physiological parameters, `decode` (from the Composition
    Root) and the profile's time zone. The file is decoded once.
    """

    def __init__(
        self,
        index: ActivityIndex,
        archive: ActivityArchive,
        physiology: PhysiologyStore,
        decode: Decoder,
        *,
        timezone: ZoneInfo,
        max_file_bytes: int = MAX_ACTIVITY_FILE_BYTES,
    ) -> None:
        if max_file_bytes <= 0:
            raise ValueError("the byte cap must be positive")
        self._index = index
        self._archive = archive
        self._physiology = physiology
        self._decode = decode
        self._timezone = timezone
        self._max_file_bytes = max_file_bytes

    def __call__(self, identity: ActivityIdentity) -> ActivityDetail | ExcludedActivity:
        """Detail of the activity, or the reason why its file cannot be used.

        Raises:
            ActivityNotFoundError: the identity is not in the index.
            MissingPhysiologyError: no physiological parameter set.
        """
        record = self._index.get(identity)
        if record is None:
            raise ActivityNotFoundError("activity not in the index")
        data = read_original_bytes(self._archive, record, self._max_file_bytes)
        if isinstance(data, ExclusionReason):
            return ExcludedActivity(identity, data)
        try:
            activity = self._decode(data, record.file_format)
        except ActivityFileError:
            return ExcludedActivity(identity, ExclusionReason.DECODE_FAILED)
        if activity.identity != record.identity:
            return ExcludedActivity(identity, ExclusionReason.FILE_ALTERED)
        local_day = local_day_of(record.start, self._timezone)
        physiology = physiology_for_day(self._physiology.list_entries(), local_day)
        load = load_of_activity(activity, physiology)
        threshold = threshold_for_sport(physiology, activity.sport)
        series = activity.heart_rate
        return ActivityDetail(
            identity=identity,
            sport=activity.sport,
            start=activity.start,
            local_day=local_day,
            elapsed_s=activity.elapsed_s,
            timer_s=activity.timer_s,
            summary=activity.summary,
            load=load,
            trace=None if series is None else HeartRateTrace.of(series, physiology.limits),
            limits=physiology.limits,
            threshold_bpm=threshold,
            lt1_bpm=lt1_for_sport(physiology, activity.sport),
            zone_starts_bpm=zone_starts_bpm(threshold),
            unordered_hr_samples=activity.discarded_hr_samples,
            zone_load=load_per_zone(activity.sport, series, physiology, load),
            domain_seconds=domain_seconds(activity.sport, series, physiology),
            speed=None if activity.distance is None else smoothed_speed(activity.distance),
        )
