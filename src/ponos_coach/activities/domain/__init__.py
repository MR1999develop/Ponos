# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Pure domain of the activities feature (no I/O, Qt, network or logging)."""

from ponos_coach.activities.domain.activity import Activity, FileId

__all__ = ["Activity", "FileId"]
