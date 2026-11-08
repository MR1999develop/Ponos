# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""The injury commands: open, check in, review, close, report an illness, list; their exact undo
and their refusals. On the in-memory profile; the same store is tried on SQLite by the contract."""

from __future__ import annotations

from datetime import timedelta

import pytest

from ponos_coach.injury.domain import (
    BodyZone,
    IllnessKind,
    Injury,
    InjuryId,
    Overdue,
    PathStatus,
    Step,
    WarningSign,
    defaults,
)
from ponos_coach.injury.ports import (
    InjuryAlreadyExistsError,
    InjuryNotFoundError,
    InjuryStoreError,
)
from ponos_coach.injury.use_cases import (
    CloseProblem,
    ConfirmMedicalReview,
    IllnessAlreadyOpenError,
    InvalidInjuryChangeError,
    ListInjuries,
    NotAnInjuryError,
    NoWarningSignToReviewError,
    OpenInjury,
    ProblemChanged,
    RecordCheckIn,
    RemoveProblem,
    ReportIllness,
    RestoreProblem,
)
from ponos_coach.profiles.domain import ProfileId, ProfileName
from ponos_coach.profiles.ports import OpenProfile
from tests.builders import absolute_path
from tests.fakes.profile_storage import InMemoryProfileStorage
from tests.injury.builders import DAY0, checkin, good_days, illness, injury, questionnaire


def a_profile() -> OpenProfile:
    return InMemoryProfileStorage().create(
        absolute_path("p"),
        ProfileId.parse("00000000-0000-4000-8000-000000000001"),
        ProfileName.parse("Atleta di prova"),
        "Europe/Rome",
    )


class Rig:
    """The use cases over one in-memory profile."""

    def __init__(self) -> None:
        self.profile = a_profile()
        store, index = self.profile.injuries, self.profile.activities
        self.open = OpenInjury(store)
        self.illness = ReportIllness(index, store)
        self.check_in = RecordCheckIn(index, store)
        self.review = ConfirmMedicalReview(index, store)
        self.close = CloseProblem(index, store)
        self.remove = RemoveProblem(index, store)
        self.restore = RestoreProblem(index, store)
        self.list = ListInjuries(store)

    def stored(self) -> list[object]:
        return list(self.profile.injuries.list_problems())


# --- Open and its undo --------------------------------------------------------------------------
def test_opening_gives_what_is_needed_to_undo_and_redo_it_exactly() -> None:
    rig = Rig()
    opened = rig.open(injury("a"))

    assert rig.stored() == [opened.problem]
    removed = rig.remove(InjuryId("a"))  # the undo
    assert rig.stored() == []
    rig.restore(removed.problem)  # the redo
    assert rig.stored() == [opened.problem]


def test_the_same_id_twice_is_refused() -> None:
    rig = Rig()
    rig.open(injury("a"))
    with pytest.raises(InjuryAlreadyExistsError):
        rig.open(injury("a"))
    assert len(rig.stored()) == 1


def test_a_warning_sign_in_the_questionnaire_opens_a_halted_injury() -> None:
    rig = Rig()
    rig.open(injury("a", questionnaire(signs=[WarningSign.NIGHT_PAIN])))
    [view] = rig.list(DAY0)
    assert view.state is not None
    assert view.state.see_doctor


# --- Check-in -----------------------------------------------------------------------------------
def test_a_check_in_is_saved_and_its_undo_puts_the_injury_back_exactly() -> None:
    rig = Rig()
    rig.open(injury("a"))
    done = rig.check_in(InjuryId("a"), checkin(1))

    assert isinstance(done, ProblemChanged)
    assert isinstance(done.after, Injury) and len(done.after.checkins) == 1
    assert rig.stored() == [done.after]
    rig.restore(done.before)  # undo
    assert rig.stored() == [done.before]
    rig.restore(done.after)  # redo
    assert rig.stored() == [done.after]


def test_a_check_in_for_the_same_day_replaces_it_and_the_undo_brings_the_first_back() -> None:
    rig = Rig()
    rig.open(injury("a"))
    first = rig.check_in(InjuryId("a"), checkin(1, during=1))
    second = rig.check_in(InjuryId("a"), checkin(1, during=6))

    assert second.before == first.after
    rig.restore(second.before)
    [kept] = rig.stored()
    assert isinstance(kept, Injury)
    assert kept.checkins[0].pain_during is not None
    assert kept.checkins[0].pain_during.value == 1


def test_the_check_ins_move_the_path_and_the_list_says_where_it_stands() -> None:
    rig = Rig()
    rig.open(injury("a"))
    for item in good_days(1, defaults.GOOD_DAYS_TO_STEP_UP[Step.ACTIVATION]):
        rig.check_in(InjuryId("a"), item)
    last = DAY0 + timedelta(days=3)
    [view] = rig.list(last)

    assert view.state is not None
    assert view.state.step is Step.WALKING
    assert not view.checkin_due
    [tomorrow] = rig.list(last + timedelta(days=1))
    assert tomorrow.checkin_due


def test_a_check_in_on_a_missing_injury_an_illness_or_a_closed_one_is_refused() -> None:
    rig = Rig()
    with pytest.raises(InjuryNotFoundError):
        rig.check_in(InjuryId("ghost"), checkin(1))
    rig.illness(illness(illness_id="s"))
    with pytest.raises(NotAnInjuryError):
        rig.check_in(InjuryId("s"), checkin(1))
    rig.open(injury("a"))
    rig.close(InjuryId("a"), DAY0)
    with pytest.raises(InvalidInjuryChangeError):
        rig.check_in(InjuryId("a"), checkin(1))
    with pytest.raises(InvalidInjuryChangeError):
        rig.open(injury("b"))
        rig.check_in(InjuryId("b"), checkin(-1))  # before the opening


def test_every_refusal_is_a_store_error_so_one_handler_is_enough() -> None:
    for error in (
        NotAnInjuryError,
        IllnessAlreadyOpenError,
        NoWarningSignToReviewError,
        InvalidInjuryChangeError,
        InjuryNotFoundError,
        InjuryAlreadyExistsError,
    ):
        assert issubclass(error, InjuryStoreError)


# --- Medical review -----------------------------------------------------------------------------
def test_the_review_restarts_the_path_and_the_undo_halts_it_again() -> None:
    rig = Rig()
    rig.open(injury("a", questionnaire(signs=[WarningSign.SWELLING_OR_DEFORMITY])))
    done = rig.review(InjuryId("a"), DAY0)
    [after] = rig.list(DAY0)
    assert after.state is not None
    assert after.state.status is PathStatus.ACTIVE

    rig.restore(done.before)
    [undone] = rig.list(DAY0)
    assert undone.state is not None
    assert undone.state.see_doctor


def test_the_review_is_refused_when_no_sign_is_waiting() -> None:
    rig = Rig()
    rig.open(injury("a"))
    with pytest.raises(NoWarningSignToReviewError):
        rig.review(InjuryId("a"), DAY0)


def test_the_review_is_refused_for_an_illness_and_for_a_day_out_of_order() -> None:
    rig = Rig()
    rig.illness(illness(illness_id="s"))
    with pytest.raises(NotAnInjuryError):
        rig.review(InjuryId("s"), DAY0)
    rig.open(injury("a", questionnaire(signs=[WarningSign.FEVER])))
    with pytest.raises(InvalidInjuryChangeError):
        rig.review(InjuryId("a"), DAY0 - timedelta(days=1))


# --- Close --------------------------------------------------------------------------------------
def test_closing_an_injury_and_its_undo() -> None:
    rig = Rig()
    rig.open(injury("a"))
    done = rig.close(InjuryId("a"), DAY0 + timedelta(days=20))
    [closed] = rig.stored()
    assert isinstance(closed, Injury)
    assert closed.closed_on == DAY0 + timedelta(days=20)

    rig.restore(done.before)
    [reopened] = rig.stored()
    assert isinstance(reopened, Injury)
    assert reopened.is_open


def test_closing_twice_or_a_missing_record_is_refused() -> None:
    rig = Rig()
    rig.open(injury("a"))
    rig.close(InjuryId("a"), DAY0)
    with pytest.raises(InvalidInjuryChangeError):
        rig.close(InjuryId("a"), DAY0 + timedelta(days=1))
    with pytest.raises(InjuryNotFoundError):
        rig.close(InjuryId("ghost"), DAY0)


# --- Illness ------------------------------------------------------------------------------------
def test_reporting_an_illness_and_saying_it_is_over_with_the_exact_undo() -> None:
    rig = Rig()
    opened = rig.illness(illness(IllnessKind.NECK_UP, "s"))
    over = rig.close(InjuryId("s"), DAY0 + timedelta(days=4))

    assert rig.stored() == [over.after]
    rig.restore(over.before)
    assert rig.stored() == [opened.problem]
    [view] = rig.list(DAY0)
    assert view.state is None  # an illness has no path


def test_only_one_illness_can_be_open_at_a_time() -> None:
    rig = Rig()
    rig.illness(illness(illness_id="s1"))
    with pytest.raises(IllnessAlreadyOpenError):
        rig.illness(illness(illness_id="s2"))
    rig.close(InjuryId("s1"), DAY0)
    rig.illness(illness(illness_id="s2"))  # the first is over: a new one may be reported
    assert len(rig.stored()) == 2


def test_an_illness_does_not_stop_an_injury_from_being_opened() -> None:
    rig = Rig()
    rig.illness(illness(illness_id="s"))
    rig.open(injury("a", questionnaire(BodyZone.CALF)))
    assert len(rig.stored()) == 2


# --- Restore and list ---------------------------------------------------------------------------
def test_restoring_a_record_that_is_gone_adds_it_back() -> None:
    rig = Rig()
    rig.open(injury("a"))
    removed = rig.remove(InjuryId("a"))
    rig.restore(removed.problem)
    assert rig.stored() == [removed.problem]


def test_removing_a_missing_record_is_refused() -> None:
    with pytest.raises(InjuryNotFoundError):
        Rig().remove(InjuryId("ghost"))


def test_the_list_says_when_the_recovery_is_slower_than_expected() -> None:
    rig = Rig()
    rig.open(injury("a"))
    [fresh] = rig.list(DAY0 + timedelta(days=5))
    [slow] = rig.list(DAY0 + timedelta(days=defaults.STEP_EXPECTED_DAYS))
    assert fresh.overdue is None
    assert slow.overdue is Overdue.STEP


def test_a_command_that_fails_inside_its_transaction_changes_nothing() -> None:
    rig = Rig()
    rig.open(injury("a"))
    before = rig.stored()
    with pytest.raises(InvalidInjuryChangeError):
        rig.check_in(InjuryId("a"), checkin(-5))
    assert rig.stored() == before
