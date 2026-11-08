# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Measured lean mass on the real stack: plausibility at the borders, hostile numbers, the exact
undo, and its use in the limit on energy availability (30 kcal per kg of lean mass).

Hand-worked athlete ("Atleta di prova", male, 180 cm, born 1990-05-05, 80 kg, sedentary day 1.2):
basal 1755 kcal, expenditure 2106 kcal. The limit on the deficit is `2106 - 30 x lean mass`, cut
by the floor of the basal rate (at most 351) and by 20% of the expenditure (at most 421):

- Boer: 0.407 x 80 + 0.267 x 180 - 19.2 = 61.42 kg -> 2106 - 1842.6 = 263.4 -> 263
- measured 61.4 kg -> 264;  measured 70.0 kg -> 6;  measured 55.0 kg -> the basal floor, 351
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from ponos_coach.goals.domain import Priority
from ponos_coach.nutrition.domain import (
    Advisory,
    GoalPeriod,
    LeanMass,
    LeanMassEntry,
    Person,
    WeightLossGoal,
    energy_availability,
)
from ponos_coach.nutrition.ports import LeanMassNotFoundError
from ponos_coach.profiles.domain import Weight, WeightEntry
from ponos_coach.profiles.lean_mass import (
    LeanMassChangedError,
    LeanMassDateInFutureError,
    LeanMassImplausibleError,
    LeanMassNeedsWeightError,
    ListLeanMass,
    RemoveLeanMass,
    SetLeanMass,
    UndoLeanMass,
)
from tests.collaudo.fase5_support import FIRST_DAY, TODAY, World, make_world, race
from tests.collaudo.support import create_profile

DAY = TODAY - timedelta(days=3)  # 2026-02-27
START = date(2026, 2, 1)
BORN_YEAR_AGE = 35  # on 2026-02-27, born 1990-05-05
BASAL, EXPENDITURE = 1755, 2106


@pytest.fixture
def world(tmp_path: Path) -> Iterator[World]:
    opened = make_world(tmp_path / "w")
    opened.profile.session.goal_periods.add(GoalPeriod(FIRST_DAY, None, WeightLossGoal(500)))
    yield opened
    opened.close()


class Cases:
    """The use cases of the lean mass over the world's profile."""

    def __init__(self, world: World, today: date = TODAY) -> None:
        s = world.profile.session
        self.set = SetLeanMass(s.activities, s.weights, s.lean_masses, lambda: today)
        self.undo = UndoLeanMass(s.activities, s.lean_masses)
        self.remove = RemoveLeanMass(s.activities, s.lean_masses)
        self.list = ListLeanMass(s.lean_masses)


def kg(text: str) -> LeanMass:
    return LeanMass.from_kg(Decimal(text))


# --- The limit on energy availability, by hand ----------------------------------------------------
def test_without_a_measurement_the_boer_estimate_limits_the_deficit(world: World) -> None:
    report = world.report(DAY)

    assert (report.nutrition.basal_kcal, report.nutrition.expenditure_kcal) == (BASAL, EXPENDITURE)
    assert report.nutrition.deficit_kcal == 263  # 2106 - 30 x 61.42
    assert report.nutrition.target_kcal == EXPENDITURE - 263
    assert Advisory.DEFICIT_LIMITED in report.nutrition.advisories


@pytest.mark.parametrize(
    ("lean", "deficit"),
    [
        ("61.4", 264),  # 2106 - 1842
        ("70.0", 6),  # 2106 - 2100: nearly nothing to lose for such a body
        ("65.0", 156),  # 2106 - 1950
        ("55.0", 351),  # 2106 - 1650 = 456, but the basal floor leaves 351
        ("32.0", 351),  # the lowest plausible lean mass of an 80 kg man
    ],
)
def test_a_measured_lean_mass_replaces_the_estimate_in_the_limit(
    world: World, lean: str, deficit: int
) -> None:
    Cases(world).set(kg(lean), START)

    report = world.report(DAY)

    assert report.nutrition.deficit_kcal == deficit
    assert report.nutrition.target_kcal == EXPENDITURE - deficit
    assert report.nutrition.target_kcal >= BASAL  # never under the basal rate


def test_a_lean_mass_so_high_that_it_leaves_no_deficit_never_makes_the_deficit_negative(
    world: World,
) -> None:
    Cases(world).set(kg("76.0"), START)  # 2106 - 2280 < 0

    report = world.report(DAY)

    assert report.nutrition.deficit_kcal == 0
    assert report.nutrition.target_kcal == EXPENDITURE
    assert Advisory.DEFICIT_LIMITED in report.nutrition.advisories


def test_the_limit_does_not_move_with_the_training_of_the_day(world: World) -> None:
    Cases(world).set(kg("65.0"), START)
    rest = world.report(DAY)
    world.import_run(DAY, minutes=75, bpm=150, hour=7)
    busy = world.report(DAY)

    assert busy.activities  # there is exercise now
    assert busy.nutrition.deficit_kcal == rest.nutrition.deficit_kcal == 156
    # and the energy availability, with the net energy of the exercise, stays at 30
    net = sum(a.energy.net_kcal for a in busy.activities)
    person = Person(
        world.profile.session.physiology.list_entries()[0].physiology.sex,
        BORN_YEAR_AGE,
        180,
        80.0,
        lean_mass_kg=65.0,
    )
    assert energy_availability(person, busy.nutrition.target_kcal, net) == pytest.approx(
        30, abs=0.1
    )
    assert busy.nutrition.target_kcal > rest.nutrition.target_kcal  # you eat what you burn


# --- Validity in time -----------------------------------------------------------------------------
def test_a_measurement_does_not_hold_for_the_days_before_it(world: World) -> None:
    Cases(world).set(kg("70.0"), date(2026, 2, 20))

    before = world.report(date(2026, 2, 19))
    on_day = world.report(date(2026, 2, 20))
    later = world.report(DAY)

    assert before.nutrition.deficit_kcal == 263  # Boer
    assert on_day.nutrition.deficit_kcal == later.nutrition.deficit_kcal == 6


def test_a_newer_measurement_takes_over_from_its_own_day_only(world: World) -> None:
    cases = Cases(world)
    cases.set(kg("70.0"), date(2026, 2, 10))
    cases.set(kg("55.0"), date(2026, 2, 20))

    assert world.report(date(2026, 2, 19)).nutrition.deficit_kcal == 6
    assert world.report(date(2026, 2, 20)).nutrition.deficit_kcal == 351


def test_a_measurement_that_a_later_weight_makes_implausible_falls_back_to_the_estimate(
    world: World,
) -> None:
    Cases(world).set(kg("70.0"), START)  # 87.5% of 80 kg
    world.profile.session.weights.put(WeightEntry(date(2026, 2, 10), Weight(720)))  # 72.0 kg

    report = world.report(DAY)  # 70.0 of 72.0 is 97%: not a measurement to trust

    # Boer for 72 kg: 0.407 x 72 + 48.06 - 19.2 = 58.164; basal 1675, expenditure 2010;
    # 2010 - 30 x 58.164 = 265
    assert report.nutrition.basal_kcal == 1675
    assert report.nutrition.deficit_kcal == 265


def test_the_lean_mass_of_a_day_ahead_is_the_one_valid_that_day(world: World) -> None:
    Cases(world).set(kg("70.0"), START)
    world.add_goal(race("a", TODAY + timedelta(days=100), Priority.A, 10_000))
    world.built()
    far = TODAY + timedelta(days=20)

    # the planned exercise adds its MET energy, which the limit does not depend on (it may differ by
    # one kcal from the rounding of the expenditure)
    assert world.report(far).nutrition.deficit_kcal == pytest.approx(6, abs=1)
    world.profile.session.lean_masses.put(LeanMassEntry(TODAY + timedelta(days=10), kg("55.0")))
    assert world.report(far).nutrition.deficit_kcal == pytest.approx(
        456, abs=1
    )  # 2106 - 1650; the floor of the basal rate only binds on a day without exercise


# --- What the use case accepts and refuses --------------------------------------------------------
@pytest.mark.parametrize(
    ("text", "ok"),
    [
        ("32.0", True),  # 40% of 80 kg
        ("31.9", False),
        ("76.0", True),  # 95% of 80 kg
        ("76.1", False),
        ("15.0", False),  # a valid type, not plausible for 80 kg
        ("200.0", False),
    ],
)
def test_the_plausibility_borders_are_40_and_95_percent_of_the_weight_of_the_day(
    world: World, text: str, ok: bool
) -> None:
    cases = Cases(world)
    if ok:
        assert cases.set(kg(text), START).entry.lean_mass == kg(text)
    else:
        with pytest.raises(LeanMassImplausibleError):
            cases.set(kg(text), START)
        assert cases.list() == ()


@pytest.mark.parametrize(
    "text",
    ["NaN", "Infinity", "-Infinity", "-1", "0", "14.9", "200.1", "80.05", "1e400", "1E+3", "6.1e1"],
)
def test_hostile_numbers_never_become_a_lean_mass(text: str) -> None:
    try:
        value = Decimal(text)
    except ArithmeticError:  # pragma: no cover - the parser itself refuses
        return
    if text == "6.1e1":  # 61 kg written with an exponent is a number like any other
        assert LeanMass.from_kg(value).kg == 61.0
        return
    with pytest.raises(ValueError):
        LeanMass.from_kg(value)


def test_a_future_date_and_a_date_before_1900_are_refused(world: World) -> None:
    cases = Cases(world)
    with pytest.raises(LeanMassDateInFutureError):
        cases.set(kg("65.0"), TODAY + timedelta(days=1))
    with pytest.raises(ValueError, match="1900"):
        cases.set(kg("65.0"), date(1899, 12, 31))
    assert cases.list() == ()


def test_without_any_weight_no_lean_mass_can_be_checked(tmp_path: Path) -> None:
    profile = create_profile(tmp_path / "bare")
    try:
        s = profile.session
        cases = SetLeanMass(s.activities, s.weights, s.lean_masses, lambda: TODAY)
        with pytest.raises(LeanMassNeedsWeightError):
            cases(kg("65.0"), START)
        assert ListLeanMass(s.lean_masses)() == ()
    finally:
        profile.close()


def test_the_date_of_today_is_accepted(world: World) -> None:
    assert Cases(world).set(kg("65.0"), TODAY).entry.valid_from == TODAY


# --- Undo -----------------------------------------------------------------------------------------
def test_the_undo_of_a_replacement_puts_back_exactly_the_old_entry(world: World) -> None:
    cases = Cases(world)
    first = cases.set(kg("65.0"), START)
    second = cases.set(kg("66.4"), START)
    assert second.replaced == first.entry

    cases.undo(second)

    assert cases.list() == (first.entry,)
    assert world.report(DAY).nutrition.deficit_kcal == 156


def test_the_undo_of_a_first_measurement_leaves_none_and_the_estimate_again(world: World) -> None:
    cases = Cases(world)
    applied = cases.set(kg("70.0"), START)
    assert world.report(DAY).nutrition.deficit_kcal == 6

    cases.undo(applied)

    assert cases.list() == ()
    assert world.report(DAY).nutrition.deficit_kcal == 263


def test_an_undo_after_another_change_refuses_and_changes_nothing(world: World) -> None:
    cases = Cases(world)
    applied = cases.set(kg("65.0"), START)
    cases.set(kg("66.0"), START)  # the same day, edited afterwards

    with pytest.raises(LeanMassChangedError):
        cases.undo(applied)
    assert cases.list() == (LeanMassEntry(START, kg("66.0")),)


def test_remove_returns_the_entry_and_refuses_an_unknown_day(world: World) -> None:
    cases = Cases(world)
    cases.set(kg("65.0"), START)

    removed = cases.remove(START)

    assert removed == LeanMassEntry(START, kg("65.0"))
    assert cases.list() == ()
    with pytest.raises(LeanMassNotFoundError):
        cases.remove(START)
