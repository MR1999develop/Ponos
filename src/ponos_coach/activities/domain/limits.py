# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Project default caps for activity files and tracks (one single definition)."""

from datetime import UTC, datetime

MAX_ACTIVITY_FILE_BYTES = 50 * 1024 * 1024  # FIT or GPX: beyond this, the file is not read
MAX_TRACK_POINTS = 2000  # points of the simplified track saved in the index
TRACK_TOLERANCE_M = 10.0  # maximum deviation of the simplified track from the original

# Window of times allowed for an activity, [start, end): outside it, the file is corrupt. A
# zeroed clock (year 1) or a crazy one would inflate the load series to centuries of days.
MIN_ACTIVITY_INSTANT = datetime(1990, 1, 1, tzinfo=UTC)
MAX_ACTIVITY_INSTANT = datetime(2100, 1, 1, tzinfo=UTC)
