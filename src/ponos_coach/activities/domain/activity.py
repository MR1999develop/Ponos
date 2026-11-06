# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Attività letta da un file FIT o GPX."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from ponos_coach.activities.domain.heart_rate_series import HeartRateSeries
from ponos_coach.activities.domain.identity import ActivityIdentity, FileFormat, format_of
from ponos_coach.activities.domain.sport import Sport
from ponos_coach.activities.domain.summary import ActivitySummary
from ponos_coach.activities.domain.timing import (
    check_durations,
    check_utc,
    is_plausible_span,
)


@dataclass(frozen=True, slots=True)
class Activity:
    """Un'attività: identità, formato, sport, inizio UTC, durate, serie di FC e riepilogo.

    `elapsed_s` è il tempo trascorso (pause comprese) e `timer_s` il tempo in movimento
    (senza pause); entrambi finiti e positivi, con `timer_s <= elapsed_s` (regola del
    progetto: chi legge il file porta al trascorso uno scarto fino a 1 s e rifiuta il resto).
    `file_format` deve essere quello dell'identità. La serie di FC ha i secondi dal primo
    record; `discarded_hr_samples` conta i campioni di FC scartati perché con timestamp
    ripetuto o non crescente. Inizio e fine stanno in [1990-01-01, 2100-01-01) UTC.
    """

    identity: ActivityIdentity
    file_format: FileFormat
    sport: Sport
    start: datetime
    elapsed_s: float
    timer_s: float
    heart_rate: HeartRateSeries | None
    summary: ActivitySummary
    discarded_hr_samples: int = 0

    def __post_init__(self) -> None:
        if format_of(self.identity) is not self.file_format:
            raise ValueError("il formato non corrisponde all'identità")
        check_utc(self.start)
        check_durations(self.elapsed_s, self.timer_s)
        if not is_plausible_span(self.start, self.elapsed_s):
            raise ValueError("inizio o fine fuori dalla finestra degli orari ammessi")
        if self.discarded_hr_samples < 0:
            raise ValueError("i campioni scartati non possono essere negativi")
