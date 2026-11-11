# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""`ThresholdPace` (domain primitive, text reading) and the pace in force on a day."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from hypothesis import given
from hypothesis import strategies as st

from ponos_coach.assessment.domain import ThresholdTest
from ponos_coach.assessment.domain.defaults import MAX_PACE_S_PER_KM, MIN_PACE_S_PER_KM
from ponos_coach.profiles.domain import (
    PaceSource,
    ThresholdPace,
    ThresholdPaceEntry,
    entries_of_tests,
    threshold_pace_in_force,
)
from tests.builders import fit_identity

DAY = date(2026, 3, 10)


def entry(offset_days: int, seconds: int) -> ThresholdPaceEntry:
    return ThresholdPaceEntry(DAY + timedelta(days=offset_days), ThresholdPace(seconds))


# --- The domain primitive ------------------------------------------------------------------------
@pytest.mark.parametrize(
    "seconds", [MIN_PACE_S_PER_KM, MIN_PACE_S_PER_KM + 1, 300, MAX_PACE_S_PER_KM]
)
def test_the_limits_and_the_values_between_are_accepted(seconds: int) -> None:
    assert ThresholdPace(seconds).s_per_km == seconds


@pytest.mark.parametrize("seconds", [MIN_PACE_S_PER_KM - 1, 0, -1, MAX_PACE_S_PER_KM + 1])
def test_the_values_just_outside_the_limits_are_refused(seconds: int) -> None:
    with pytest.raises(ValueError, match="between 1:30 and 60:00"):
        ThresholdPace(seconds)


@pytest.mark.parametrize(
    ("text", "seconds"),
    [
        ("5:30", 330),
        ("5.30", 330),
        ("5,30", 330),
        ("5'30", 330),
        ("  5:30  ", 330),
        ("6", 360),  # whole minutes
        ("60:00", 3600),
    ],
)
def test_tolerant_text_is_read_as_minutes_and_seconds(text: str, seconds: int) -> None:
    assert ThresholdPace.parse(text) == ThresholdPace(seconds)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "abc",
        "5:60",  # seconds above 59
        "1:29",  # below the range
        "60:01",  # above the range
        "-5:30",
        "5:30:10",
        "".join(chr(c) for c in (0xFF15, 0x3A, 0xFF13, 0xFF10)),  # full-width digits
        "".join(chr(c) for c in (0x665, 0x3A, 0x663, 0x660)),  # Arabic-Indic digits
        "5:30\n6:00",
        "1" * 40,  # too long: refused before matching
    ],
)
def test_unreadable_or_out_of_range_text_is_refused_and_not_repeated(text: str) -> None:
    with pytest.raises(ValueError) as error:
        ThresholdPace.parse(text)
    assert text.strip() == "" or text.strip() not in str(error.value)


@given(st.integers(MIN_PACE_S_PER_KM, MAX_PACE_S_PER_KM))
def test_reading_the_formatted_pace_gives_the_same_pace(seconds: int) -> None:
    minutes, rest = divmod(seconds, 60)

    assert ThresholdPace.parse(f"{minutes}:{rest:02d}") == ThresholdPace(seconds)


# --- The pace in force ---------------------------------------------------------------------------
def test_without_any_entry_there_is_no_pace() -> None:
    assert threshold_pace_in_force([], [], DAY) is None


def test_a_manual_entry_alone_is_in_force_from_its_day_and_never_before() -> None:
    manual = [entry(-5, 330)]

    assert threshold_pace_in_force(manual, [], DAY - timedelta(days=6)) is None
    found = threshold_pace_in_force(manual, [], DAY)
    assert found is not None
    assert (found.pace, found.source, found.since) == (
        ThresholdPace(330),
        PaceSource.MANUAL,
        DAY - timedelta(days=5),
    )


def test_a_test_alone_is_in_force_from_the_test_day() -> None:
    found = threshold_pace_in_force([], [entry(-3, 300)], DAY)

    assert found is not None
    assert (found.pace, found.source) == (ThresholdPace(300), PaceSource.TEST)


def test_the_most_recent_of_the_two_sources_wins() -> None:
    older_test = threshold_pace_in_force([entry(-2, 330)], [entry(-9, 300)], DAY)
    older_manual = threshold_pace_in_force([entry(-9, 330)], [entry(-2, 300)], DAY)

    assert older_test is not None
    assert (older_test.pace, older_test.source) == (ThresholdPace(330), PaceSource.MANUAL)
    assert older_manual is not None
    assert (older_manual.pace, older_manual.source) == (ThresholdPace(300), PaceSource.TEST)


def test_on_the_same_day_the_manual_pace_wins() -> None:
    found = threshold_pace_in_force([entry(-4, 330)], [entry(-4, 300)], DAY)

    assert found is not None
    assert (found.pace, found.source) == (ThresholdPace(330), PaceSource.MANUAL)


def test_entries_after_the_day_do_not_count() -> None:
    found = threshold_pace_in_force([entry(1, 330)], [entry(2, 300), entry(-1, 340)], DAY)

    assert found is not None
    assert found.pace == ThresholdPace(340)


def test_among_the_entries_of_a_series_the_latest_date_counts_and_on_a_tie_the_last_one() -> None:
    manual = [entry(-8, 350), entry(-3, 320), entry(-3, 325)]

    found = threshold_pace_in_force(manual, [], DAY)

    assert found is not None
    assert found.pace == ThresholdPace(325)


def test_it_is_deterministic() -> None:
    manual, tests = [entry(-8, 350), entry(-3, 320)], [entry(-3, 300)]

    assert threshold_pace_in_force(manual, tests, DAY) == threshold_pace_in_force(
        manual, tests, DAY
    )


@given(
    manual=st.lists(
        st.tuples(st.integers(-30, 30), st.integers(MIN_PACE_S_PER_KM, MAX_PACE_S_PER_KM))
    ),
    tests=st.lists(
        st.tuples(st.integers(-30, 30), st.integers(MIN_PACE_S_PER_KM, MAX_PACE_S_PER_KM))
    ),
    today=st.integers(-30, 30),
)
def test_the_choice_equals_a_naive_scan_of_the_candidates(
    manual: list[tuple[int, int]], tests: list[tuple[int, int]], today: int
) -> None:
    """Naive model: all candidates not after the day, ordered by (date, manual first on a tie,
    position); the best is the last."""
    candidates = [(d, 1, i, PaceSource.MANUAL, s) for i, (d, s) in enumerate(manual) if d <= today]
    candidates += [(d, 0, i, PaceSource.TEST, s) for i, (d, s) in enumerate(tests) if d <= today]

    found = threshold_pace_in_force(
        [entry(d, s) for d, s in manual], [entry(d, s) for d, s in tests], DAY + timedelta(today)
    )

    if not candidates:
        assert found is None
        return
    day, _, _, source, seconds = max(candidates)
    assert found is not None
    assert (found.since, found.source, found.pace.s_per_km) == (
        DAY + timedelta(days=day),
        source,
        seconds,
    )


# --- The paces of the threshold tests ------------------------------------------------------------
def test_only_the_tests_that_measured_a_pace_become_entries_in_their_order() -> None:
    start = datetime(2026, 1, 1, 7, 0, tzinfo=UTC)
    tests = [
        ThresholdTest(fit_identity(1), DAY, start, 165, 310),
        ThresholdTest(fit_identity(2), DAY + timedelta(days=1), start, 168, None),
        ThresholdTest(fit_identity(3), DAY + timedelta(days=2), start, 169, 300),
    ]

    assert entries_of_tests(tests) == (
        ThresholdPaceEntry(DAY, ThresholdPace(310)),
        ThresholdPaceEntry(DAY + timedelta(days=2), ThresholdPace(300)),
    )
