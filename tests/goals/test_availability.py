# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""The weekly availability: valid by construction, and the days that get a session."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from ponos_coach.goals.domain import WeeklyAvailability

_DAYS = frozenset(range(7))


@pytest.mark.parametrize(
    ("days", "sessions", "long_day", "cap"),
    [
        (frozenset({7}), 1, 0, 60),
        (frozenset({-1, 2}), 1, 2, 60),
        (_DAYS, 3, 7, 60),
        (_DAYS, 3, -1, 60),
        (_DAYS, 3, 0, 6001),
        (_DAYS, 3, 0, -1),
        (_DAYS, 0, 0, 60),  # no session with days available
        (_DAYS, 8, 0, 60),
        (frozenset({1, 3}), 3, 1, 60),  # more sessions than days
        (frozenset({1, 3}), 1, 2, 60),  # the long day is not an available day
        (frozenset(), 0, 0, 60),  # time but no day
        (frozenset(), 1, 0, 0),  # a session but no day
    ],
)
def test_availability_rejects_what_does_not_hold_together(
    days: frozenset[int], sessions: int, long_day: int, cap: int
) -> None:
    with pytest.raises(ValueError, match=r"day|session|minutes"):
        WeeklyAvailability(days, sessions, long_day, cap)


def test_availability_accepts_the_limits() -> None:
    assert WeeklyAvailability(_DAYS, 7, 6, 6000).sessions_per_week == 7
    assert WeeklyAvailability(frozenset({0}), 1, 0, 0).long_day == 0


def test_availability_is_empty_without_days_or_hours() -> None:
    assert WeeklyAvailability.empty().is_empty
    assert WeeklyAvailability(_DAYS, 3, 6, 0).is_empty
    assert not WeeklyAvailability(frozenset({6}), 1, 6, 60).is_empty


@pytest.mark.parametrize(
    ("days", "sessions", "long_day", "expected"),
    [
        (_DAYS, 7, 6, _DAYS),
        (_DAYS, 1, 3, {3}),  # the long day alone
        (_DAYS, 2, 6, {2, 6}),  # farthest from Sunday, lowest day on the tie (2 and 3)
        (_DAYS, 3, 6, {2, 4, 6}),
        (_DAYS, 4, 6, {0, 2, 4, 6}),
        (_DAYS, 3, 5, {1, 3, 5}),
        ({0, 1, 2, 3}, 2, 3, {0, 3}),  # the circle: Sunday is next to Monday, still the farthest
        ({1, 3, 5}, 3, 5, {1, 3, 5}),
        ({4, 5, 6}, 2, 4, {4, 6}),
    ],
)
def test_the_sessions_are_spread_as_far_apart_as_the_days_allow(
    days: set[int], sessions: int, long_day: int, expected: set[int]
) -> None:
    availability = WeeklyAvailability(frozenset(days), sessions, long_day, 600)
    assert availability.session_days == expected


def test_the_empty_availability_has_no_session_days() -> None:
    assert WeeklyAvailability.empty().session_days == frozenset()


@st.composite
def _availabilities(draw: st.DrawFn) -> WeeklyAvailability:
    days = draw(st.sets(st.integers(0, 6), min_size=1))
    return WeeklyAvailability(
        frozenset(days),
        draw(st.integers(1, len(days))),
        draw(st.sampled_from(sorted(days))),
        draw(st.integers(0, 6000)),
    )


@given(_availabilities())
def test_session_days_are_available_days_with_the_long_day_and_the_chosen_count(
    availability: WeeklyAvailability,
) -> None:
    chosen = availability.session_days
    assert chosen <= availability.days
    assert availability.long_day in chosen
    assert len(chosen) == availability.sessions_per_week


def _naive_days(availability: WeeklyAvailability) -> set[int]:
    """The rule written out day by day: repeatedly the available day whose nearest chosen day is
    the farthest on the circle of seven days, the lowest weekday among equals."""
    chosen = [availability.long_day]
    while len(chosen) < availability.sessions_per_week:
        best_day, best_gap = -1, -1
        for day in range(7):
            if day not in availability.days or day in chosen:
                continue
            gap = 7
            for other in chosen:
                forward = (day - other) % 7
                gap = min(gap, forward, 7 - forward)
            if gap > best_gap:
                best_day, best_gap = day, gap
        chosen.append(best_day)
    return set(chosen)


@given(_availabilities())
def test_session_days_follow_the_rule_written_out_day_by_day(
    availability: WeeklyAvailability,
) -> None:
    assert availability.session_days == _naive_days(availability)


@given(_availabilities())
def test_no_two_sessions_are_next_to_each_other_if_the_days_are_never_neighbours(
    availability: WeeklyAvailability,
) -> None:
    days = availability.days
    if any((d + 1) % 7 in days for d in days) and len(days) > 1:
        return  # neighbours among the available days: they may be unavoidable
    chosen = availability.session_days
    assert not any((d + 1) % 7 in chosen for d in chosen) or len(chosen) == 7
