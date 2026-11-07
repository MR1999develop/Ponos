# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Form "Sport weights": limits, reset and changes, without a window."""

from __future__ import annotations

import pytest

from ponos_coach.activities.domain import Sport
from ponos_coach.load.domain import SportWeight, default_weight, effective_weights
from ponos_coach.ui.sport_weights_form import SportWeightsForm


def defaults_only(form: SportWeightsForm) -> bool:
    """`is_default` read through a function: the type is not narrowed between changes."""
    return form.is_default


def make_form() -> SportWeightsForm:
    return SportWeightsForm(effective_weights({}))


def test_a_new_form_shows_the_defaults_and_has_no_changes() -> None:
    form = make_form()

    assert {sport: form.value(sport) for sport in Sport} == {
        sport: default_weight(sport).value for sport in Sport
    }
    assert form.is_default
    assert not form.has_changes


@pytest.mark.parametrize(
    ("typed", "shown"),
    [
        (0.0, 0.0),  # on point
        (5.0, 5.0),  # on point
        (-0.01, 0.0),  # off point: brought to the limit
        (5.01, 5.0),  # off point
        (1.234, 1.23),  # two decimals
        (100.0, 5.0),
    ],
)
def test_a_weight_is_kept_between_zero_and_five_with_two_decimals(
    typed: float, shown: float
) -> None:
    form = make_form()

    form.set_value(Sport.CYCLING, typed)

    assert form.value(Sport.CYCLING) == shown


def test_changes_are_relative_to_the_weights_in_force_at_opening() -> None:
    form = make_form()

    form.set_value(Sport.RUNNING, 1.5)
    assert form.has_changes
    assert not form.is_default
    form.set_value(Sport.RUNNING, default_weight(Sport.RUNNING).value)
    assert not form.has_changes


def test_restoring_the_defaults_resets_every_sport() -> None:
    saved = {sport: SportWeight(2.0) for sport in Sport}
    form = SportWeightsForm(saved)
    assert not defaults_only(form)

    form.restore_defaults()

    assert defaults_only(form)
    assert form.has_changes  # compared to what was in force
    assert form.build() == {sport: default_weight(sport) for sport in Sport}


def test_build_gives_one_valid_weight_per_sport() -> None:
    form = make_form()
    form.set_value(Sport.TRAIL, 1.75)

    built = form.build()

    assert set(built) == set(Sport)
    assert built[Sport.TRAIL] == SportWeight(1.75)


@pytest.mark.parametrize("typed", [float("nan"), -float("nan")])
def test_a_nan_changes_nothing_and_build_never_raises(typed: float) -> None:
    form = make_form()
    form.set_value(Sport.TRAIL, 1.75)

    form.set_value(Sport.TRAIL, typed)

    assert form.value(Sport.TRAIL) == 1.75  # zeroing it would remove the sport from the load
    assert form.build()[Sport.TRAIL] == SportWeight(1.75)


@pytest.mark.parametrize(("typed", "shown"), [(float("inf"), 5.0), (float("-inf"), 0.0)])
def test_infinities_are_brought_to_the_nearest_limit(typed: float, shown: float) -> None:
    form = make_form()

    form.set_value(Sport.RUNNING, typed)

    assert form.value(Sport.RUNNING) == shown
    assert form.build()[Sport.RUNNING] == SportWeight(shown)
