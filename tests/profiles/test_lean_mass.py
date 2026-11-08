# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Lean mass use cases: set with an exact undo, remove, list (T-NUT-02) on the in-memory profile."""

from __future__ import annotations

from datetime import date

import pytest

from ponos_coach.nutrition.domain import LeanMass, LeanMassEntry
from ponos_coach.nutrition.ports import LeanMassNotFoundError, NutritionStoreError
from ponos_coach.profiles.domain import ProfileId, ProfileName, Weight, WeightEntry
from ponos_coach.profiles.lean_mass import (
    AppliedLeanMass,
    LeanMassChangedError,
    LeanMassDateInFutureError,
    LeanMassImplausibleError,
    LeanMassNeedsWeightError,
    ListLeanMass,
    RemoveLeanMass,
    RestoreLeanMass,
    SetLeanMass,
    UndoLeanMass,
)
from ponos_coach.profiles.ports import OpenProfile
from tests.builders import absolute_path
from tests.fakes.profile_storage import InMemoryProfileStorage

TODAY = date(2026, 3, 10)
D1, D2 = date(2026, 3, 1), date(2026, 3, 5)


class Rig:
    def __init__(self, *, weight: bool = True) -> None:
        self.profile: OpenProfile = InMemoryProfileStorage().create(
            absolute_path("p"),
            ProfileId.parse("00000000-0000-4000-8000-000000000001"),
            ProfileName.parse("Atleta di prova"),
            "Europe/Rome",
        )
        if weight:
            self.profile.weights.put(WeightEntry(date(2026, 2, 1), Weight(800)))
        p = self.profile
        self.set = SetLeanMass(p.activities, p.weights, p.lean_masses, lambda: TODAY)
        self.undo = UndoLeanMass(p.activities, p.lean_masses)
        self.remove = RemoveLeanMass(p.activities, p.lean_masses)
        self.restore = RestoreLeanMass(p.activities, p.lean_masses)
        self.list = ListLeanMass(p.lean_masses)


def test_setting_a_lean_mass_saves_it_and_says_nothing_was_replaced() -> None:
    rig = Rig()

    applied = rig.set(LeanMass(612), D1)

    assert applied.entry == LeanMassEntry(D1, LeanMass(612))
    assert applied.replaced is None
    assert rig.list() == (applied.entry,)


def test_setting_the_same_day_again_replaces_it_and_remembers_what_it_replaced() -> None:
    rig = Rig()
    first = rig.set(LeanMass(612), D1)

    second = rig.set(LeanMass(620), D1)

    assert second.replaced == first.entry
    assert rig.list() == (LeanMassEntry(D1, LeanMass(620)),)


def test_undoing_a_first_measurement_removes_it() -> None:
    rig = Rig()
    applied = rig.set(LeanMass(612), D1)

    rig.undo(applied)

    assert rig.list() == ()


def test_undoing_a_replacement_puts_back_exactly_the_old_entry() -> None:
    rig = Rig()
    rig.set(LeanMass(612), D1)
    other = rig.set(LeanMass(640), D2)
    before = rig.list()
    applied = rig.set(LeanMass(650), D2)

    rig.undo(applied)

    assert rig.list() == before
    assert other.entry in rig.list()


def test_undoing_a_change_that_was_changed_again_is_refused_and_changes_nothing() -> None:
    rig = Rig()
    applied = rig.set(LeanMass(612), D1)
    rig.set(LeanMass(630), D1)  # the user measured again the same day

    with pytest.raises(LeanMassChangedError):
        rig.undo(applied)

    assert rig.list() == (LeanMassEntry(D1, LeanMass(630)),)


def test_undoing_after_the_entry_was_removed_is_refused() -> None:
    rig = Rig()
    applied = rig.set(LeanMass(612), D1)
    rig.remove(D1)

    with pytest.raises(LeanMassChangedError):
        rig.undo(applied)

    assert rig.list() == ()


def test_removing_gives_back_the_entry_and_it_can_be_set_again() -> None:
    rig = Rig()
    rig.set(LeanMass(612), D1)

    removed = rig.remove(D1)

    assert removed == LeanMassEntry(D1, LeanMass(612))
    assert rig.list() == ()
    assert rig.set(removed.lean_mass, removed.valid_from).entry == removed


def test_removing_a_day_without_an_entry_is_an_error() -> None:
    with pytest.raises(LeanMassNotFoundError):
        Rig().remove(D1)


def test_the_list_is_by_ascending_date() -> None:
    rig = Rig()
    rig.set(LeanMass(640), D2)
    rig.set(LeanMass(612), D1)

    assert [e.valid_from for e in rig.list()] == [D1, D2]


def test_a_measurement_cannot_start_in_the_future() -> None:
    rig = Rig()

    with pytest.raises(LeanMassDateInFutureError):
        rig.set(LeanMass(612), date(2026, 3, 11))

    assert rig.set(LeanMass(612), TODAY).entry.valid_from == TODAY  # today is fine
    assert len(rig.list()) == 1


def test_a_measurement_needs_a_weight_to_be_checked_against() -> None:
    rig = Rig(weight=False)

    with pytest.raises(LeanMassNeedsWeightError):
        rig.set(LeanMass(612), D1)

    assert rig.list() == ()


@pytest.mark.parametrize(
    ("tenths", "accepted"), [(319, False), (320, True), (760, True), (761, False)]
)
def test_a_measurement_must_fit_the_weight_of_that_day(tenths: int, accepted: bool) -> None:
    rig = Rig()  # 80.0 kg: 32.0 to 76.0 kg of lean mass

    if accepted:
        assert rig.set(LeanMass(tenths), D1).entry.lean_mass == LeanMass(tenths)
    else:
        with pytest.raises(LeanMassImplausibleError):
            rig.set(LeanMass(tenths), D1)
        assert rig.list() == ()


def test_the_weight_checked_is_the_one_valid_on_the_day_of_the_measurement() -> None:
    rig = Rig()
    rig.profile.weights.put(WeightEntry(date(2026, 3, 3), Weight(1000)))  # 100 kg from the 3rd

    with pytest.raises(LeanMassImplausibleError):
        rig.set(LeanMass(320), D2)  # 32 kg is under 40% of 100 kg
    assert rig.set(LeanMass(320), D1).entry.valid_from == D1  # 80 kg on the 1st: fine


def test_a_failed_write_changes_nothing() -> None:
    rig = Rig()
    rig.set(LeanMass(612), D1)

    class Failing:
        def put(self, entry: LeanMassEntry) -> None:  # noqa: ARG002 - a store that fails
            raise NutritionStoreError("write failed")

        def list_entries(self) -> tuple[LeanMassEntry, ...]:
            return tuple(rig.profile.lean_masses.list_entries())

    failing = SetLeanMass(
        rig.profile.activities,
        rig.profile.weights,
        Failing(),  # type: ignore[arg-type]
        lambda: TODAY,
    )

    with pytest.raises(NutritionStoreError):
        failing(LeanMass(650), D1)

    assert rig.list() == (LeanMassEntry(D1, LeanMass(612)),)


# --- Restore: the exact entry comes back whatever the weight is now ---------------------------
def test_restoring_a_removed_entry_puts_it_back_even_if_the_weight_made_it_implausible() -> None:
    rig = Rig()
    entry = rig.set(LeanMass(612), D1).entry  # 61.2 kg of 80 kg
    removed = rig.remove(D1)
    rig.profile.weights.put(
        WeightEntry(date(2026, 2, 20), Weight(500))
    )  # now 50 kg: 61.2 is too much
    with pytest.raises(LeanMassImplausibleError):
        rig.set(removed.lean_mass, D1)  # a new set refuses it...

    applied = rig.restore(removed)  # ...the undo of the removal does not

    assert rig.list() == (entry,)
    assert applied == AppliedLeanMass(entry, None)


def test_restoring_over_a_day_that_holds_something_else_changes_nothing() -> None:
    rig = Rig()
    removed = rig.set(LeanMass(612), D1).entry
    rig.remove(D1)
    other = rig.set(LeanMass(620), D1).entry

    with pytest.raises(LeanMassChangedError):
        rig.restore(removed)

    assert rig.list() == (other,)


def test_restoring_a_set_that_replaced_an_entry_expects_the_entry_it_replaced() -> None:
    rig = Rig()
    first = rig.set(LeanMass(612), D1)
    second = rig.set(LeanMass(650), D1)
    rig.undo(second)  # the day holds the first value again

    with pytest.raises(LeanMassChangedError):
        rig.restore(second.entry)  # expects a free day
    redone = rig.restore(second.entry, second.replaced)  # redo of the second set

    assert redone == second
    assert rig.list() == (second.entry,)
    assert first.entry != second.entry
