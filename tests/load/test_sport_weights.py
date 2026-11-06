# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Pesi degli sport salvati nel profilo: default, casi d'uso, effetto sulla panoramica."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from ponos_coach.activities.domain import Sport
from ponos_coach.load.domain import (
    SportWeight,
    customised_weights,
    default_weight,
    effective_weights,
)
from ponos_coach.load.use_cases import GetSportWeights, SetSportWeights
from tests.builders import physiology_entry
from tests.load.test_profile_overview import FIRST_PARAMS_DAY, TODAY, World, two_runs

# Default di PLAN.md sezione 2 punto 7.
PLAN_DEFAULTS = {
    Sport.RUNNING: 1.0,
    Sport.TRAIL: 1.1,
    Sport.CYCLING: 0.8,
    Sport.HIKING: 0.8,
    Sport.WALKING: 0.6,
    Sport.STRENGTH: 1.2,
    Sport.OTHER: 0.8,
}


def use_cases(world: World) -> tuple[GetSportWeights, SetSportWeights]:
    store = world.profile.sport_weights
    return GetSportWeights(store), SetSportWeights(world.profile.activities, store)


def test_defaults_are_the_ones_of_the_plan() -> None:
    assert {sport: default_weight(sport).value for sport in Sport} == PLAN_DEFAULTS


def test_a_profile_that_never_set_weights_reads_the_defaults_for_every_sport() -> None:
    get, _ = use_cases(World())

    assert {sport: weight.value for sport, weight in get().items()} == PLAN_DEFAULTS


def test_set_then_get_gives_the_chosen_weights_and_defaults_for_the_rest() -> None:
    world = World()
    get, set_ = use_cases(world)

    set_({Sport.CYCLING: SportWeight(0.9), Sport.STRENGTH: SportWeight(1.5)})

    weights = get()
    assert (weights[Sport.CYCLING].value, weights[Sport.STRENGTH].value) == (0.9, 1.5)
    assert weights[Sport.RUNNING] == default_weight(Sport.RUNNING)
    assert set(weights) == set(Sport)


def test_setting_again_replaces_everything_sports_not_given_return_to_default() -> None:
    world = World()
    get, set_ = use_cases(world)
    set_({Sport.CYCLING: SportWeight(0.9)})

    set_({Sport.HIKING: SportWeight(0.5)})

    assert get()[Sport.CYCLING] == default_weight(Sport.CYCLING)
    assert get()[Sport.HIKING].value == 0.5


def test_only_the_weights_different_from_the_default_are_saved() -> None:
    world = World()
    _, set_ = use_cases(world)

    set_({Sport.RUNNING: SportWeight(1.0), Sport.CYCLING: SportWeight(0.85)})

    assert dict(world.profile.sport_weights.load()) == {Sport.CYCLING: SportWeight(0.85)}


def test_a_zero_weight_is_a_real_choice_not_a_missing_value() -> None:
    world = World()
    get, set_ = use_cases(world)

    set_({Sport.WALKING: SportWeight(0.0)})

    assert get()[Sport.WALKING].value == 0.0


def test_effective_weights_and_customised_weights_are_inverse_views() -> None:
    saved = {Sport.TRAIL: SportWeight(1.4)}

    assert effective_weights(saved)[Sport.TRAIL] == SportWeight(1.4)
    assert customised_weights(effective_weights(saved)) == saved


def test_the_overview_uses_the_saved_weights() -> None:
    world = World()
    world.profile.physiology.put(physiology_entry(FIRST_PARAMS_DAY))
    two_runs(world)
    plain = world.overview()
    _, set_ = use_cases(world)

    set_({Sport.RUNNING: SportWeight(1.2)})
    heavier = world.overview()

    for before, after in zip(plain.activities, heavier.activities, strict=True):
        assert after.load.load == pytest.approx(before.load.load * 1.2)
        assert after.load.hr_tss == pytest.approx(before.load.hr_tss)  # il peso non tocca l'hrTSS


def test_changing_the_weight_of_a_sport_recalculates_only_that_sport() -> None:
    world = World()
    world.profile.physiology.put(physiology_entry(FIRST_PARAMS_DAY))
    two_runs(world)
    world.overview()
    reads = world.archive.reads
    _, set_ = use_cases(world)

    set_({Sport.CYCLING: SportWeight(0.5)})  # nessuna bici nel profilo
    world.overview()
    assert world.archive.reads == reads  # le corse restano in cache

    set_({Sport.CYCLING: SportWeight(0.5), Sport.RUNNING: SportWeight(1.1)})
    world.overview()
    assert world.archive.reads == reads + 2  # le due corse si ricalcolano


def test_going_back_to_the_default_weight_gives_the_original_load() -> None:
    world = World()
    world.profile.physiology.put(physiology_entry(FIRST_PARAMS_DAY))
    two_runs(world)
    original = world.overview()
    _, set_ = use_cases(world)

    set_({Sport.RUNNING: SportWeight(1.2)})
    world.overview()
    set_({})
    back = world.overview()

    assert back.activities == original.activities


def test_a_failed_save_leaves_the_previous_weights() -> None:
    world = World()
    _, set_ = use_cases(world)
    set_({Sport.RUNNING: SportWeight(1.2)})
    store = world.profile.sport_weights

    class Failing:
        def load(self) -> object:
            return store.load()

        def replace(self, weights: object) -> None:
            del weights
            raise RuntimeError("scrittura non riuscita")

    failing = SetSportWeights(world.profile.activities, Failing())  # type: ignore[arg-type]
    with pytest.raises(RuntimeError, match="scrittura"):
        failing({Sport.CYCLING: SportWeight(2.0)})

    assert dict(store.load()) == {Sport.RUNNING: SportWeight(1.2)}


# --- Aggregazioni dalla panoramica ------------------------------------------------------------


def test_the_overview_gives_the_weekly_load_and_zone_seconds() -> None:
    world = World()
    world.profile.physiology.put(physiology_entry(FIRST_PARAMS_DAY))
    before, after = two_runs(world)  # 15 gennaio e 10 febbraio, 30 minuti a 150 bpm (Z2)
    overview = world.overview()

    weekly = overview.weekly_load(TODAY)
    zones = overview.weekly_zones(TODAY)

    assert weekly.weeks[0] == date(2026, 1, 12)  # il lunedì della settimana del 15 gennaio
    assert weekly.weeks[-1] == date(2026, 2, 23)  # la settimana di oggi (1 marzo è domenica)
    assert weekly.sports == (Sport.RUNNING,)
    assert float(weekly.load.sum()) == pytest.approx(sum(a.load.load for a in overview.activities))
    assert weekly.total_by_week[0] == pytest.approx(overview.activities[0].load.load)
    assert (weekly.weeks[-1] - weekly.weeks[0]).days % 7 == 0
    assert zones.weeks == weekly.weeks
    assert zones.seconds[:, 1].sum() == pytest.approx(2 * 1800.0)  # Z2
    assert zones.seconds[:, [0, 2, 3, 4]].sum() == 0.0
    assert before.start.date() < after.start.date() < TODAY + timedelta(days=1)


def test_an_empty_overview_has_empty_aggregations() -> None:
    overview = World().overview()

    assert overview.weekly_load(TODAY).weeks == ()
    assert overview.weekly_zones(TODAY).weeks == ()
