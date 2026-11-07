# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Id of an activity on Garmin: a positive integer that fits in a SQLite INTEGER."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

MAX_GARMIN_ACTIVITY_ID: Final = 2**63 - 1  # the maximum of a SQLite INTEGER
_MAX_DIGITS: Final = 19
_DIGITS: Final = re.compile(r"[1-9][0-9]*")


@dataclass(frozen=True, slots=True, order=True)
class GarminActivityId:
    """Id of a Garmin activity, valid by construction: `1 <= value <= 2**63 - 1`."""

    value: int

    def __post_init__(self) -> None:
        if type(self.value) is not int or not 1 <= self.value <= MAX_GARMIN_ACTIVITY_ID:
            raise ValueError("invalid Garmin id")

    def __str__(self) -> str:
        return str(self.value)

    @classmethod
    def parse(cls, raw: object) -> GarminActivityId:
        """Id from a value in a JSON response: an integer or a string of ASCII digits.

        Everything else is rejected (booleans, decimals, signs, spaces, leading zeros, paths
        like `../1`): an external id never enters a file name or a query.

        Raises:
            ValueError: value not allowed.
        """
        if isinstance(raw, str):
            if len(raw) > _MAX_DIGITS or not _DIGITS.fullmatch(raw):
                raise ValueError("invalid Garmin id")
            return cls(int(raw))
        if type(raw) is int:
            return cls(raw)
        raise ValueError("invalid Garmin id")
