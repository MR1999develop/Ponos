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

SAVED = WeeklyAvailability((60, 0, 60, 0, 60, 90, 120), 6, 600)
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


def test_a_day_a_long_day_and_a_week_can_change_and_stay_in_their_limits() -> None:
    draft = draft_of(SAVED, CHOICES)

    changed = draft.with_day_minutes(1, 45).with_long_day(5).with_max_week_minutes(99_999)
    clamped = draft.with_day_minutes(0, 5_000)

    assert changed.day_minutes[1] == 45
    assert changed.long_day == 5
    assert changed.max_week_minutes == 6_000
    assert clamped.day_minutes[0] == 720


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
        TrainingDraft((0, 0, 0, 0, 0, 0, 0), 6, 600, CHOICES),
        TrainingDraft((60, 60, 60, 60, 60, 60, 60), 6, 0, CHOICES),
    ],
)
def test_no_time_at_all_is_said_before_saving(draft: TrainingDraft) -> None:
    assert TrainingHint.NO_TIME in hints_of(draft)


def test_a_long_session_on_a_day_without_time_is_said() -> None:
    draft = TrainingDraft((60, 60, 60, 60, 60, 60, 0), 6, 600, CHOICES)

    assert hints_of(draft) == (TrainingHint.LONG_DAY_WITHOUT_TIME,)


def test_a_day_without_time_matters_only_if_a_long_session_is_chosen() -> None:
    easy_only = SessionChoices.of({Sport.RUNNING: {SessionType.EASY}})

    assert hints_of(TrainingDraft((60, 60, 60, 60, 60, 60, 0), 6, 600, easy_only)) == ()


def test_good_settings_have_no_hint() -> None:
    assert hints_of(draft_of(SAVED, CHOICES)) == ()
