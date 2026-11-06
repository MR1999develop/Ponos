# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""`physiology_for_day`: parametri validi in un giorno."""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from hypothesis import given
from hypothesis import strategies as st

from ponos_coach.load.domain import MissingPhysiologyError, PhysiologyEntry, physiology_for_day
from tests.builders import physiology, physiology_entry

FIRST = date(2026, 1, 1)
SECOND = date(2026, 2, 1)
ENTRIES = [physiology_entry(FIRST, 170), physiology_entry(SECOND, 160)]


def test_no_entries_is_an_explicit_error() -> None:
    with pytest.raises(MissingPhysiologyError):
        physiology_for_day([], FIRST)


@pytest.mark.parametrize(
    ("day", "threshold"),
    [
        (FIRST - timedelta(days=1), 170),  # prima del primo: vale il primo
        (FIRST, 170),  # on point: la data di validità è inclusa
        (SECOND - timedelta(days=1), 170),  # off point
        (SECOND, 160),
        (SECOND + timedelta(days=400), 160),
    ],
)
def test_most_recent_entry_not_after_the_day(day: date, threshold: int) -> None:
    assert physiology_for_day(ENTRIES, day) == physiology(threshold)


@given(
    days=st.sets(st.dates(min_value=date(2000, 1, 1), max_value=date(2040, 1, 1)), min_size=1),
    day=st.dates(min_value=date(1999, 1, 1), max_value=date(2041, 1, 1)),
)
def test_matches_a_naive_search(days: set[date], day: date) -> None:
    ordered = sorted(days)
    entries = [physiology_entry(d, 100 + i) for i, d in enumerate(ordered)]
    valid = [e for e in entries if e.valid_from <= day]
    naive: PhysiologyEntry = valid[-1] if valid else entries[0]

    assert physiology_for_day(entries, day) == naive.physiology
