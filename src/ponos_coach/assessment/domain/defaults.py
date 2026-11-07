# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Every number of the 30-minute threshold test, in one place.

These are project defaults (see the project skill `ponos-allenamento`,
`references/test-soglia.md`); the pace-zone percentages are derived from Friel's zones. Changing a
value changes the outcome of every test: nothing else in the package holds a number of this kind.
"""

from __future__ import annotations

from typing import Final

from ponos_coach.activities.domain import Sport
from ponos_coach.load.domain import MAX_GAP_S

# --- The test -------------------------------------------------------------------------------
# The sports that can be the test: a run, on road or on trail.
THRESHOLD_TEST_SPORTS: Final = frozenset({Sport.RUNNING, Sport.TRAIL})
TEST_DURATION_S: Final = 1800  # the segment: 30 minutes of elapsed time
THRESHOLD_WINDOW_S: Final = 1200  # the last 20 minutes of the segment give the threshold
MAX_PAUSE_S: Final = 60  # pauses allowed in the segment, in total
# A gap between two HR samples above this is a pause (the same limit the load uses).
PAUSE_GAP_S: Final = MAX_GAP_S
MIN_VALID_HR_PERCENT: Final = 90  # valid HR needed, as a share of the time of the last 20 minutes
# Changes when the way the result is computed changes; saved with every test.
METHOD_VERSION: Final = 1

# The last distance sample is valid until the next one, as an HR sample is: a distance series may
# stop short of the end of the segment by one interval between samples, at most this long.
DISTANCE_END_TOLERANCE_S: Final = 1.0
# A step of the cumulative distance faster than this is a GPS glitch, not a runner: 12 m/s is
# beyond the 100 m world record (about 10.4 m/s). Such a step never attracts the choice of the
# segment, and the pace of a stretch that contains one is not given.
MAX_PLAUSIBLE_SPEED_M_S: Final = 12.0

# --- Pace --------------------------------------------------------------------------------------
METERS_PER_KM: Final = 1000
# A pace outside this range is not a running pace (a GPS glitch or a walk): no pace is given.
MIN_PACE_S_PER_KM: Final = 90
MAX_PACE_S_PER_KM: Final = 3600
# Pace zones as a share of the threshold SPEED: Z1 below 78%, Z2 78-88%, Z3 88-94%, Z4 94-100%,
# Z5 100% and over. In pace terms, about: slower than 128% of the threshold pace, 114-128%,
# 106-114%, 100-106%, faster than the threshold pace.
PACE_ZONE_STARTS_PERCENT: Final = (78, 88, 94, 100)

# --- Retest --------------------------------------------------------------------------------------
RETEST_MIN_DAYS: Final = 42  # six weeks after the last applied test
RETEST_MAX_DAYS: Final = 56  # eight weeks after the last applied test
