# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Id di un'attività su Garmin: un intero positivo che entra in un INTEGER di SQLite."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

MAX_GARMIN_ACTIVITY_ID: Final = 2**63 - 1  # il massimo di un INTEGER di SQLite
_MAX_DIGITS: Final = 19
_DIGITS: Final = re.compile(r"[1-9][0-9]*")


@dataclass(frozen=True, slots=True, order=True)
class GarminActivityId:
    """Id di un'attività Garmin, valido per costruzione: `1 <= value <= 2**63 - 1`."""

    value: int

    def __post_init__(self) -> None:
        if type(self.value) is not int or not 1 <= self.value <= MAX_GARMIN_ACTIVITY_ID:
            raise ValueError("id Garmin non valido")

    def __str__(self) -> str:
        return str(self.value)

    @classmethod
    def parse(cls, raw: object) -> GarminActivityId:
        """Id da un valore di una risposta JSON: un intero oppure una stringa di cifre ASCII.

        Si rifiuta tutto il resto (booleani, decimali, segni, spazi, zeri iniziali, percorsi
        come `../1`): un id esterno non entra mai in un nome di file né in una query.

        Raises:
            ValueError: valore non ammesso.
        """
        if isinstance(raw, str):
            if len(raw) > _MAX_DIGITS or not _DIGITS.fullmatch(raw):
                raise ValueError("id Garmin non valido")
            return cls(int(raw))
        if type(raw) is int:
            return cls(raw)
        raise ValueError("id Garmin non valido")
