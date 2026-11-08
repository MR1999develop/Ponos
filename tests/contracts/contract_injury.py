# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Contract of the injury store: injuries and illnesses with their check-ins and signs.

Tried through a profile created by the `ProfileStorage` of the `harness` fixture, on the in-memory
fake and on SQLite alike.
"""

from __future__ import annotations

from contextlib import closing
from datetime import timedelta

import pytest

from ponos_coach.injury.domain import (
    BodyZone,
    IllnessKind,
    Injury,
    InjuryId,
    Onset,
    PainFreeActivity,
    PainReport,
    Questionnaire,
    Side,
    WarningSign,
)
from ponos_coach.injury.ports import (
    InjuryAlreadyExistsError,
    InjuryNotFoundError,
)
from tests.contracts.contract_storage import NAME, PROFILE_ID, create
from tests.contracts.harness import StorageHarness
from tests.injury.builders import DAY0, checkin, illness, injury, pain, questionnaire


def a_rich_injury(ident: str = "knee1") -> Injury:
    """An injury with every kind of field: notes, signs, pain-free activities, no running pain,
    check-ins with and without activity and with signs, a review and a closing."""
    q = Questionnaire(
        BodyZone.KNEE,
        Side.BOTH,
        Onset.SUDDEN,
        DAY0 - timedelta(days=3),
        PainReport(pain(2), pain(3), None, pain(1)),
        frozenset({PainFreeActivity.SWIMMING, PainFreeActivity.WALKING}),
        frozenset({WarningSign.NIGHT_PAIN, WarningSign.SWELLING_OR_DEFORMITY}),
        "Notes of the doctor\nsecond line\twith a tab, accents: è ò, and 'quotes'",
    )
    checkins = [
        checkin(1, during=None, morning=1),
        checkin(2, during=4, morning=2, signs=[WarningSign.FEVER, WarningSign.CHEST_PAIN]),
        checkin(5, during=0, morning=0),
    ]
    return (
        injury(ident, q, checkins)
        .with_review(DAY0 + timedelta(days=2))
        .closed(DAY0 + timedelta(days=9))
    )


class InjuryStoreContract:
    """The injuries of a profile: ids chosen by the caller, kept whole, listed by start day."""

    def test_new_store_is_empty(self, harness: StorageHarness) -> None:
        with closing(create(harness)) as profile:
            assert profile.injuries.list_problems() == []
            assert profile.injuries.get(InjuryId("none")) is None

    def test_an_injury_is_kept_whole_with_all_its_parts(self, harness: StorageHarness) -> None:
        entry = a_rich_injury()
        with closing(create(harness)) as profile:
            profile.injuries.add(entry)

            assert profile.injuries.get(entry.injury_id) == entry
            assert profile.injuries.list_problems() == [entry]

    def test_a_simple_injury_and_an_illness_are_kept_whole_too(
        self, harness: StorageHarness
    ) -> None:
        simple = injury("simple", questionnaire(running=None, free=[]))
        sick = illness(IllnessKind.NECK_UP, "sick")
        recovered = illness(IllnessKind.SYSTEMIC, "past").recovered(DAY0 + timedelta(days=2))
        with closing(create(harness)) as profile:
            for record in (simple, sick, recovered):
                profile.injuries.add(record)

            assert profile.injuries.get(InjuryId("simple")) == simple
            assert profile.injuries.get(InjuryId("sick")) == sick
            assert profile.injuries.get(InjuryId("past")) == recovered

    def test_the_same_id_twice_is_refused_and_changes_nothing(
        self, harness: StorageHarness
    ) -> None:
        first = injury("a")
        with closing(create(harness)) as profile:
            profile.injuries.add(first)

            with pytest.raises(InjuryAlreadyExistsError):
                profile.injuries.add(injury("a", questionnaire(BodyZone.CALF)))
            with pytest.raises(InjuryAlreadyExistsError):
                profile.injuries.add(illness(illness_id="a"))

            assert profile.injuries.list_problems() == [first]

    def test_the_list_is_by_start_day_then_id(self, harness: StorageHarness) -> None:
        late = injury("a", opened_on=DAY0 + timedelta(days=5))
        early_b = injury("b")
        early_a = illness(illness_id="a0")
        with closing(create(harness)) as profile:
            for record in (late, early_b, early_a):
                profile.injuries.add(record)

            assert profile.injuries.list_problems() == [early_a, early_b, late]

    def test_replace_rewrites_the_check_ins_and_the_signs_all_together(
        self, harness: StorageHarness
    ) -> None:
        before = a_rich_injury("x")
        after = injury("x", questionnaire(BodyZone.SHIN, signs=[]), [checkin(1, 2, 1)])
        with closing(create(harness)) as profile:
            profile.injuries.add(before)
            profile.injuries.replace(after)

            assert profile.injuries.get(before.injury_id) == after

    def test_replace_can_turn_an_injury_into_an_illness_and_back(
        self, harness: StorageHarness
    ) -> None:
        with closing(create(harness)) as profile:
            profile.injuries.add(a_rich_injury("x"))
            profile.injuries.replace(illness(illness_id="x"))
            assert profile.injuries.get(InjuryId("x")) == illness(illness_id="x")

            profile.injuries.replace(a_rich_injury("x"))
            assert profile.injuries.get(InjuryId("x")) == a_rich_injury("x")

    def test_replacing_a_missing_record_is_refused(self, harness: StorageHarness) -> None:
        with closing(create(harness)) as profile, pytest.raises(InjuryNotFoundError):
            profile.injuries.replace(injury("ghost"))

    def test_remove_takes_the_check_ins_and_signs_with_it(self, harness: StorageHarness) -> None:
        keep = injury("keep")
        with closing(create(harness)) as profile:
            profile.injuries.add(keep)
            profile.injuries.add(a_rich_injury("drop"))

            profile.injuries.remove(InjuryId("drop"))

            assert profile.injuries.list_problems() == [keep]
            profile.injuries.add(a_rich_injury("drop"))  # the id is free again, nothing left over

    def test_removing_a_missing_record_is_refused(self, harness: StorageHarness) -> None:
        with closing(create(harness)) as profile, pytest.raises(InjuryNotFoundError):
            profile.injuries.remove(InjuryId("ghost"))

    def test_the_records_survive_closing_and_reopening(self, harness: StorageHarness) -> None:
        folder = harness.new_folder()
        entry = a_rich_injury()
        profile = harness.storage.create(folder, PROFILE_ID, NAME, "Europe/Rome")
        profile.injuries.add(entry)
        profile.close()

        with closing(harness.storage.open(folder)) as reopened:
            assert reopened.injuries.list_problems() == [entry]

    def test_a_failed_transaction_leaves_no_injury_behind(self, harness: StorageHarness) -> None:
        with closing(create(harness)) as profile:
            keep = injury("keep")
            profile.injuries.add(keep)
            with pytest.raises(RuntimeError), profile.activities.transaction():
                profile.injuries.add(injury("new"))
                profile.injuries.replace(injury("keep", checkins=[checkin(1)]))
                raise RuntimeError("stop")

            assert profile.injuries.list_problems() == [keep]

    def test_a_replace_that_fails_halfway_changes_nothing(self, harness: StorageHarness) -> None:
        with closing(create(harness)) as profile:
            original = a_rich_injury("x")
            profile.injuries.add(original)
            with pytest.raises(RuntimeError), profile.activities.transaction():
                profile.injuries.replace(injury("x"))
                raise RuntimeError("stop")

            assert profile.injuries.get(original.injury_id) == original
