# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""The training settings form's rules, without a window."""

from __future__ import annotations

import pytest

from ponos_coach.activities.domain import Sport
from ponos_coach.goals.domain import SessionChoices, SessionType, WeeklyAvailability
from ponos_coach.ui.training_draft import (
    TrainingDraft,
    TrainingHint,
    draft_of,
    hints_of,
    is_suggestion,
)

SAVED = WeeklyAvailability(frozenset({0, 2, 4, 5, 6}), 3, 6, 600)
CHOICES = SessionChoices.of({Sport.RUNNING: {SessionType.EASY, SessionType.LONG}})


def test_a_profile_with_nothing_saved_starts_from_suggestions() -> None:
    draft = draft_of(None, SessionChoices.of({}))

    assert is_suggestion(None, SessionChoices.of({}))
    assert draft.availability().is_empty is False
    assert SessionType.EASY in draft.choices.for_sport(Sport.RUNNING)


def test_what_is_saved_fills_the_form_as_it_is() -> None:
    draft = draft_of(SAVED, CHOICES)

    assert not is_suggestion(SAVED, CHOICES)
    assert draft.availability() == SAVED
    assert draft.choices == CHOICES


def test_the_sessions_the_long_day_and_the_week_can_change_and_stay_in_their_limits() -> None:
    draft = draft_of(SAVED, CHOICES)

    changed = draft.with_sessions(4).with_long_day(5).with_max_week_minutes(99_999)
    too_many = draft.with_sessions(7)
    too_few = draft.with_sessions(0)

    assert changed.sessions == 4
    assert changed.long_day == 5
    assert changed.max_week_minutes == 6_000
    assert too_many.sessions == 5  # one a day at most
    assert too_few.sessions == 1


def test_unticking_a_day_keeps_the_sessions_and_the_long_day_among_the_ticked_days() -> None:
    draft = draft_of(SAVED, CHOICES)

    fewer_days = draft.with_day(2, False).with_day(4, False).with_day(5, False)
    long_gone = draft.with_day(6, False)

    assert fewer_days.days == {0, 6}
    assert fewer_days.sessions == 2  # three sessions cannot fit two days
    assert long_gone.long_day == 5  # the last ticked day
    assert long_gone.availability().long_day == 5


def test_ticking_a_day_adds_a_session_only_if_every_ticked_day_had_one() -> None:
    all_used = draft_of(WeeklyAvailability(frozenset({0, 6}), 2, 6, 600), CHOICES)
    some_free = draft_of(SAVED, CHOICES)

    assert all_used.with_day(3, True).sessions == 3
    assert some_free.with_day(1, True).sessions == 3


def test_a_long_day_that_is_not_ticked_gets_ticked() -> None:
    draft = draft_of(SAVED, CHOICES).with_long_day(1)

    assert draft.long_day == 1
    assert 1 in draft.days


def test_with_no_day_ticked_the_settings_are_empty_and_come_back_with_the_day() -> None:
    draft = draft_of(WeeklyAvailability(frozenset({3}), 1, 3, 600), CHOICES)

    none = draft.with_day(3, False)
    back = none.with_day(5, True)

    assert none.availability() == WeeklyAvailability.empty()
    assert hints_of(none) == (TrainingHint.NO_TIME,)
    assert back.availability() == WeeklyAvailability(frozenset({5}), 1, 5, 600)


def test_the_saved_empty_availability_fills_the_form_with_no_day() -> None:
    draft = draft_of(WeeklyAvailability.empty(), CHOICES)

    assert draft.days == frozenset()
    assert draft.availability().is_empty


@pytest.mark.parametrize(
    "arguments",
    [
        (frozenset({7}), 1, 0),
        (frozenset({1, 2}), 3, 1),
        (frozenset({1, 2}), 1, 5),
        (frozenset({1}), 0, 1),
    ],
)
def test_a_draft_that_does_not_hold_together_is_refused(
    arguments: tuple[frozenset[int], int, int],
) -> None:
    days, sessions, long_day = arguments
    with pytest.raises(ValueError, match=r"seven days|sessions"):
        TrainingDraft(days, sessions, long_day, 600, CHOICES)


def test_ticking_a_type_adds_it_and_unticking_the_last_one_drops_the_sport() -> None:
    draft = draft_of(SAVED, CHOICES)

    ticked = draft.with_type(Sport.CYCLING, SessionType.EASY, True)
    cleared = ticked.with_type(Sport.CYCLING, SessionType.EASY, False)

    assert ticked.choices.for_sport(Sport.CYCLING) == {SessionType.EASY}
    assert cleared.choices == CHOICES  # a sport with no type is not kept


def test_choices_of_sports_the_form_does_not_list_are_kept() -> None:
    other = SessionChoices.of({Sport.OTHER: {SessionType.ALTERNATIVE}})
    draft = draft_of(SAVED, other)

    changed = draft.with_type(Sport.RUNNING, SessionType.EASY, True)

    assert changed.choices.for_sport(Sport.OTHER) == {SessionType.ALTERNATIVE}


@pytest.mark.parametrize(
    "draft",
    [
        TrainingDraft(frozenset(), 1, 6, 600, CHOICES),
        TrainingDraft(frozenset(range(7)), 3, 6, 0, CHOICES),
    ],
    ids=["no day", "no hours"],
)
def test_no_time_at_all_is_said_before_saving(draft: TrainingDraft) -> None:
    assert TrainingHint.NO_TIME in hints_of(draft)


def test_good_settings_have_no_hint() -> None:
    assert hints_of(draft_of(SAVED, CHOICES)) == ()
