# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Profile of an activity for the map: altitude over distance and HR zone of each stretch.

Read-only use case: reads the original from the archive once, decodes the activity and its track
from the same bytes, and gives the columns the elevation chart and the zone-coloured track need.
The zone of a point is the one the load computation gives to the HR sample in force at that
instant: same samples (inside the HR limits of the day, interval to the next at most `MAX_GAP_S`),
same zone thresholds, parameters valid on the activity's local day. Positions are personal data and
the HR is health data: no log.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from enum import Enum
from zoneinfo import ZoneInfo

import numpy as np
from numpy.typing import NDArray

from ponos_coach.activities.decoding import ActivityFileError
from ponos_coach.activities.domain import (
    EARTH_RADIUS_M,
    MAX_ACTIVITY_FILE_BYTES,
    ActivityIdentity,
    ActivityTrack,
    HeartRateSeries,
    Sport,
)
from ponos_coach.activities.map_queries import TrackDecoder
from ponos_coach.activities.ports import ActivityArchive, ActivityIndex, ActivityNotFoundError
from ponos_coach.load.domain import (
    MAX_GAP_S,
    HeartRateLimits,
    MissingPhysiologyError,
    out_of_limits,
    physiology_for_day,
    threshold_for_sport,
    zone_indices,
    zone_starts_bpm,
)
from ponos_coach.load.domain.activity_load import sample_validity_s
from ponos_coach.load.ports import PhysiologyStore
from ponos_coach.load.use_cases import (
    Decoder,
    ExcludedActivity,
    ExclusionReason,
    local_day_of,
    read_original_bytes,
)

MAX_PROFILE_POINTS = 2000  # points of the profile: enough for any chart width
NO_ZONE = -1  # the sample in force is not one the load counts (or there is none)


class ZoneAbsence(Enum):
    """Why the stretches have no zone. The value is stable."""

    NO_HEART_RATE = "no_heart_rate"  # the file has no HR
    NO_PARAMETERS = "no_parameters"  # no physiological parameters to place the zones


@dataclass(frozen=True, slots=True)
class NoTrack:
    """The activity has no usable position (indoor, strength, or a file without GPS)."""

    identity: ActivityIdentity


@dataclass(frozen=True, slots=True, eq=False)
class ActivityProfileData:
    """Columns of the profile, one entry per point (at most `MAX_PROFILE_POINTS`), read-only.

    `segment` numbers the continuous stretches: where it changes there was a gap and no line
    joins the two points. `altitude_m` is `None` when the file has no altitude at all (the
    chart is absent, with that reason), `NaN` at the points that lack it. `zone` is 0 for Z1 ...
    4 for Z5 or `NO_ZONE`; the stretch that starts at point i takes `zone[i]`. `zone` is `None`
    when there is no zone at all, and `zone_absence` says why. `zone_starts_bpm` are the HRs
    where Z2 ... Z5 start that day (for the legend). `seconds` counts from the first record of
    the file, the time axis of the HR and speed charts, and increases along the points.
    """

    identity: ActivityIdentity
    sport: Sport
    local_day: date
    seconds: NDArray[np.float64]
    segment: NDArray[np.intp]
    distance_m: NDArray[np.float64]
    lat_deg: NDArray[np.float64]
    lon_deg: NDArray[np.float64]
    altitude_m: NDArray[np.float64] | None
    zone: NDArray[np.int8] | None
    zone_absence: ZoneAbsence | None
    threshold_bpm: int | None
    zone_starts_bpm: tuple[float, float, float, float] | None

    def __post_init__(self) -> None:
        count = self.segment.size
        columns: list[NDArray[np.generic]] = [
            self.seconds,
            self.segment,
            self.distance_m,
            self.lat_deg,
            self.lon_deg,
        ]
        columns += [c for c in (self.altitude_m, self.zone) if c is not None]
        if any(c.shape != (count,) for c in columns):
            raise ValueError("the profile columns have different lengths")
        if (self.zone is None) != (self.zone_absence is not None):
            raise ValueError("zones and the reason for their absence do not agree")
        for column in columns:
            column.setflags(write=False)

    def __len__(self) -> int:
        return int(self.segment.size)

    def index_at(self, seconds: float) -> int | None:
        """The point nearest in time to `seconds` (from the first record); None without points
        or for a time that is not a finite number."""
        count = len(self)
        if count == 0 or not math.isfinite(seconds):
            return None
        after = int(np.searchsorted(self.seconds, seconds))
        if after == 0:
            return 0
        if after == count:
            return count - 1
        before = after - 1
        nearer_before = seconds - self.seconds[before] <= self.seconds[after] - seconds
        return before if nearer_before else after


def _flatten(
    track: ActivityTrack,
) -> tuple[
    NDArray[np.float64],
    NDArray[np.float64],
    NDArray[np.float64],
    NDArray[np.float64],
    NDArray[np.intp],
]:
    """Columns seconds, latitude, longitude, altitude (NaN where unknown), segment number."""
    parts = track.segments
    seconds = np.concatenate([s.seconds for s in parts])
    lat = np.concatenate([s.lat_deg for s in parts])
    lon = np.concatenate([s.lon_deg for s in parts])
    altitude = np.concatenate(
        [np.full(len(s), np.nan) if s.altitude_m is None else s.altitude_m for s in parts]
    )
    segment = np.concatenate([np.full(len(s), n, dtype=np.intp) for n, s in enumerate(parts)])
    return seconds, lat, lon, altitude, segment


def _haversine_cumulative_m(
    lat_deg: NDArray[np.float64], lon_deg: NDArray[np.float64]
) -> NDArray[np.float64]:
    """Distance along the points, in metres, from the first (great-circle steps)."""
    lat, lon = np.radians(lat_deg), np.radians(lon_deg)
    d_lat, d_lon = np.diff(lat), np.diff(lon)
    a = np.sin(d_lat / 2) ** 2 + np.cos(lat[:-1]) * np.cos(lat[1:]) * np.sin(d_lon / 2) ** 2
    steps = 2 * EARTH_RADIUS_M * np.arcsin(np.sqrt(np.clip(a, 0.0, 1.0)))
    return np.concatenate(([0.0], np.cumsum(steps)))


def _distance_m(
    seconds: NDArray[np.float64],
    lat_deg: NDArray[np.float64],
    lon_deg: NDArray[np.float64],
    series: tuple[NDArray[np.float64], NDArray[np.float64]] | None,
) -> NDArray[np.float64]:
    """The device's cumulative distance at the track instants, or the one along the track."""
    if series is not None:
        return np.interp(seconds, series[0], series[1])
    return _haversine_cumulative_m(lat_deg, lon_deg)


def kept_indices(count: int) -> NDArray[np.intp]:
    """Evenly spaced points, first and last included, at most `MAX_PROFILE_POINTS`."""
    if count <= MAX_PROFILE_POINTS:
        return np.arange(count, dtype=np.intp)
    return np.unique(np.linspace(0, count - 1, MAX_PROFILE_POINTS).round().astype(np.intp))


def zones_at(
    seconds: NDArray[np.float64],
    series: HeartRateSeries,
    limits: HeartRateLimits,
    threshold_bpm: int,
) -> NDArray[np.int8]:
    """Zone (0 ... 4) of the HR sample in force at each instant, `NO_ZONE` where the load does
    not count it.

    A sample is in force from its own time until the next sample; it is counted like in
    `compute_activity_load` (HR inside the limits, interval at most `MAX_GAP_S`). Vectorised.
    """
    validity = sample_validity_s(series.seconds)
    counted = ~out_of_limits(series, limits) & (validity <= MAX_GAP_S)
    at = np.searchsorted(series.seconds, seconds, side="right") - 1
    safe = np.clip(at, 0, series.seconds.size - 1)
    in_force = (at >= 0) & counted[safe] & (seconds < series.seconds[safe] + validity[safe])
    zones = zone_indices(series.bpm[safe], threshold_bpm)
    return np.where(in_force, zones, NO_ZONE).astype(np.int8)


class GetActivityProfile:
    """Profile of an activity of the open profile (use case "activity profile").

    Dependencies: index, archive, physiological parameters, the two decoders (from the
    Composition Root) and the profile's time zone.
    """

    def __init__(  # noqa: PLR0913 - every port is a distinct volatile dependency
        self,
        index: ActivityIndex,
        archive: ActivityArchive,
        physiology: PhysiologyStore,
        decode: Decoder,
        decode_track: TrackDecoder,
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
        self._decode_track = decode_track
        self._timezone = timezone
        self._max_file_bytes = max_file_bytes

    def __call__(
        self, identity: ActivityIdentity
    ) -> ActivityProfileData | NoTrack | ExcludedActivity:
        """The profile, `NoTrack` if the file has no positions, or why the file cannot be used.

        Raises:
            ActivityNotFoundError: the identity is not in the index.
        """
        record = self._index.get(identity)
        if record is None:
            raise ActivityNotFoundError("activity not in the index")
        data = read_original_bytes(self._archive, record, self._max_file_bytes)
        if isinstance(data, ExclusionReason):
            return ExcludedActivity(identity, data)
        try:
            activity = self._decode(data, record.file_format)
            track = self._decode_track(data, record.file_format)
        except ActivityFileError:
            return ExcludedActivity(identity, ExclusionReason.DECODE_FAILED)
        if activity.identity != record.identity:
            return ExcludedActivity(identity, ExclusionReason.FILE_ALTERED)
        if track is None:
            return NoTrack(identity)
        seconds, lat, lon, altitude, segment = _flatten(track)
        series = activity.distance
        distance = _distance_m(
            seconds, lat, lon, None if series is None else (series.seconds, series.meters)
        )
        keep = kept_indices(seconds.size)
        local_day = local_day_of(record.start, self._timezone)
        zone, absence, threshold = self._zones(
            activity.heart_rate, activity.sport, local_day, seconds[keep]
        )
        return ActivityProfileData(
            identity=identity,
            sport=activity.sport,
            local_day=local_day,
            seconds=seconds[keep],
            segment=segment[keep],
            distance_m=distance[keep],
            lat_deg=lat[keep],
            lon_deg=lon[keep],
            altitude_m=None if np.isnan(altitude).all() else altitude[keep],
            zone=zone,
            zone_absence=absence,
            threshold_bpm=threshold,
            zone_starts_bpm=None if threshold is None else zone_starts_bpm(threshold),
        )

    def _zones(
        self, series: HeartRateSeries | None, sport: Sport, day: date, seconds: NDArray[np.float64]
    ) -> tuple[NDArray[np.int8] | None, ZoneAbsence | None, int | None]:
        """Zone column, why it is absent, and the threshold used (all `None` without zones)."""
        if series is None:
            return None, ZoneAbsence.NO_HEART_RATE, None
        try:
            physiology = physiology_for_day(self._physiology.list_entries(), day)
        except MissingPhysiologyError:
            return None, ZoneAbsence.NO_PARAMETERS, None
        threshold = threshold_for_sport(physiology, sport)
        return zones_at(seconds, series, physiology.limits, threshold), None, threshold


__all__ = [
    "MAX_PROFILE_POINTS",
    "NO_ZONE",
    "ActivityProfileData",
    "GetActivityProfile",
    "NoTrack",
    "ZoneAbsence",
    "kept_indices",
    "zones_at",
]
