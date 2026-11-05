# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Pure domain of the activities feature (no I/O, Qt, network or logging)."""

from ponos_coach.activities.domain.activity import Activity
from ponos_coach.activities.domain.duplicates import (
    DUPLICATE_DURATION_TOLERANCE_PERCENT,
    DUPLICATE_START_TOLERANCE,
    is_possible_duplicate,
)
from ponos_coach.activities.domain.geo import (
    MAX_POLYLINE_CHARS,
    GeoPoint,
    decode_polyline,
    encode_polyline,
    simplify_indices,
    simplify_track,
)
from ponos_coach.activities.domain.heart_rate_series import HeartRateSeries
from ponos_coach.activities.domain.identity import (
    ActivityIdentity,
    FileFormat,
    FileId,
    FitIdentity,
    GpxIdentity,
    format_of,
    identity_from_key,
    identity_key,
)
from ponos_coach.activities.domain.limits import (
    MAX_ACTIVITY_FILE_BYTES,
    MAX_TRACK_POINTS,
    TRACK_TOLERANCE_M,
)
from ponos_coach.activities.domain.record import ActivityOrigin, ActivityRecord, ArchivedFile
from ponos_coach.activities.domain.sport import Sport
from ponos_coach.activities.domain.summary import ActivitySummary

__all__ = [
    "DUPLICATE_DURATION_TOLERANCE_PERCENT",
    "DUPLICATE_START_TOLERANCE",
    "MAX_ACTIVITY_FILE_BYTES",
    "MAX_POLYLINE_CHARS",
    "MAX_TRACK_POINTS",
    "TRACK_TOLERANCE_M",
    "Activity",
    "ActivityIdentity",
    "ActivityOrigin",
    "ActivityRecord",
    "ActivitySummary",
    "ArchivedFile",
    "FileFormat",
    "FileId",
    "FitIdentity",
    "GeoPoint",
    "GpxIdentity",
    "HeartRateSeries",
    "Sport",
    "decode_polyline",
    "encode_polyline",
    "format_of",
    "identity_from_key",
    "identity_key",
    "is_possible_duplicate",
    "simplify_indices",
    "simplify_track",
]
