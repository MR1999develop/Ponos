# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Use cases of the injuries: open, check in, review, close, report an illness, list.

Every command returns what it did, and with it what is needed to take it back exactly:

- `OpenInjury` and `ReportIllness` give `ProblemOpened`; undo with `RemoveProblem(id)`, redo with
  `RestoreProblem(problem)`.
- `RecordCheckIn`, `ConfirmMedicalReview` and `CloseProblem` give `ProblemChanged(before, after)`;
  undo with `RestoreProblem(before)`, redo with `RestoreProblem(after)`.
- `RemoveProblem` gives `ProblemRemoved(problem)`; undo with `RestoreProblem(problem)`.

Ids are chosen by the caller, so a repeated command is refused instead of duplicated. The plan is
not touched here: it follows from the injuries when it is rebuilt (`profiles.RebuildPlan`). The
app never diagnoses and never decides the return: it records, follows the path and invites a doctor.
Health data: never in logs or error messages.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from ponos_coach.activities.ports import ActivityIndex
from ponos_coach.injury.domain import (
    CheckIn,
    Illness,
    Injury,
    InjuryId,
    InvalidChangeError,
    Overdue,
    PathState,
    PathStatus,
    Problem,
    checkin_due,
    follow,
    overdue,
    problem_id,
)
from ponos_coach.injury.ports import InjuryNotFoundError, InjuryStore, InjuryStoreError


class NotAnInjuryError(InjuryStoreError):
    """The id is an illness, and the command is only for injuries (check-in, review)."""


class IllnessAlreadyOpenError(InjuryStoreError):
    """An illness is already open: say first that it is over."""


class NoWarningSignToReviewError(InjuryStoreError):
    """No warning sign is waiting for a doctor: nothing to confirm."""


class InvalidInjuryChangeError(InjuryStoreError):
    """The change does not fit the record (closed, before the opening...)."""


@dataclass(frozen=True, slots=True)
class ProblemOpened:
    """An injury or an illness was opened."""

    problem: Problem


@dataclass(frozen=True, slots=True)
class ProblemChanged:
    """A record changed: `before` is how to put it back."""

    before: Problem
    after: Problem


@dataclass(frozen=True, slots=True)
class ProblemRemoved:
    """A record was removed: `problem` is how to put it back."""

    problem: Problem


@dataclass(frozen=True, slots=True)
class InjuryView:
    """A record for the screen: the injury (or illness), where its path stands (`None` for an
    illness), whether today's check-in is missing, and whether the recovery is slower than
    expected."""

    problem: Problem
    state: PathState | None
    checkin_due: bool
    overdue: Overdue | None


class OpenInjury:
    """Opens an injury from the questionnaire."""

    def __init__(self, store: InjuryStore) -> None:
        self._store = store

    def __call__(self, injury: Injury) -> ProblemOpened:
        """Saves the injury. A warning sign in the questionnaire halts the path from the start.

        Raises:
            InjuryAlreadyExistsError: the id is taken.
            InjuryStoreError: write failed.
        """
        self._store.add(injury)
        return ProblemOpened(injury)


class ReportIllness:
    """Not feeling well: opens an illness, which stops or limits the plan."""

    def __init__(self, activities: ActivityIndex, store: InjuryStore) -> None:
        self._activities = activities
        self._store = store

    def __call__(self, illness: Illness) -> ProblemOpened:
        """Saves the illness.

        Raises:
            IllnessAlreadyOpenError: another one is open.
            InjuryAlreadyExistsError: the id is taken.
            InjuryStoreError: write failed; nothing changes.
        """
        with self._activities.transaction():
            if any(isinstance(p, Illness) and p.is_open for p in self._store.list_problems()):
                raise IllnessAlreadyOpenError("an illness is already open")
            self._store.add(illness)
        return ProblemOpened(illness)


def _injury(store: InjuryStore, injury_id: InjuryId) -> Injury:
    problem = store.get(injury_id)
    if problem is None:
        raise InjuryNotFoundError("no injury with this id")
    if not isinstance(problem, Injury):
        raise NotAnInjuryError("this is an illness")
    return problem


class RecordCheckIn:
    """Records the day's answers; one for the same day is replaced."""

    def __init__(self, activities: ActivityIndex, store: InjuryStore) -> None:
        self._activities = activities
        self._store = store

    def __call__(self, injury_id: InjuryId, checkin: CheckIn) -> ProblemChanged:
        """Saves the check-in and returns the injury before and after.

        Raises:
            InjuryNotFoundError: no injury has this id.
            NotAnInjuryError: it is an illness.
            InvalidInjuryChangeError: the injury is closed or the day precedes the opening.
            InjuryStoreError: write failed; nothing changes.
        """
        with self._activities.transaction():
            before = _injury(self._store, injury_id)
            try:
                after = before.with_checkin(checkin)
            except InvalidChangeError:
                raise InvalidInjuryChangeError("the check-in does not fit") from None
            self._store.replace(after)
        return ProblemChanged(before, after)


class ConfirmMedicalReview:
    """The user says a doctor has seen the warning signs: the path starts again from the first
    step. The app does not read the doctor's notes and does not decide the return."""

    def __init__(self, activities: ActivityIndex, store: InjuryStore) -> None:
        self._activities = activities
        self._store = store

    def __call__(self, injury_id: InjuryId, day: date) -> ProblemChanged:
        """Marks the signs reported until `day` as reviewed.

        Raises:
            InjuryNotFoundError: no injury has this id.
            NotAnInjuryError: it is an illness.
            NoWarningSignToReviewError: the path is not halted.
            InvalidInjuryChangeError: closed, or the day is out of order.
            InjuryStoreError: write failed; nothing changes.
        """
        with self._activities.transaction():
            before = _injury(self._store, injury_id)
            if follow(before).status is not PathStatus.HALTED:
                raise NoWarningSignToReviewError("no warning sign is waiting")
            try:
                after = before.with_review(day)
            except InvalidChangeError:
                raise InvalidInjuryChangeError("the review does not fit") from None
            self._store.replace(after)
        return ProblemChanged(before, after)


class CloseProblem:
    """Closes an injury (the user and the doctor call it done) or says an illness is over."""

    def __init__(self, activities: ActivityIndex, store: InjuryStore) -> None:
        self._activities = activities
        self._store = store

    def __call__(self, injury_id: InjuryId, day: date) -> ProblemChanged:
        """Closes the record on `day`; the weekly ramp stays lower for a while after it.

        Raises:
            InjuryNotFoundError: no record has this id.
            InvalidInjuryChangeError: already closed, or before its last record.
            InjuryStoreError: write failed; nothing changes.
        """
        with self._activities.transaction():
            before = self._store.get(injury_id)
            if before is None:
                raise InjuryNotFoundError("no injury with this id")
            try:
                after = before.closed(day) if isinstance(before, Injury) else before.recovered(day)
            except InvalidChangeError:
                raise InvalidInjuryChangeError("the closing does not fit") from None
            self._store.replace(after)
        return ProblemChanged(before, after)


class RemoveProblem:
    """Deletes a record (the undo of opening one)."""

    def __init__(self, activities: ActivityIndex, store: InjuryStore) -> None:
        self._activities = activities
        self._store = store

    def __call__(self, injury_id: InjuryId) -> ProblemRemoved:
        """Deletes the record and returns it, to be put back by `RestoreProblem`.

        Raises:
            InjuryNotFoundError: no record has this id.
            InjuryStoreError: write failed; nothing changes.
        """
        with self._activities.transaction():
            problem = self._store.get(injury_id)
            if problem is None:
                raise InjuryNotFoundError("no injury with this id")
            self._store.remove(injury_id)
        return ProblemRemoved(problem)


class RestoreProblem:
    """Puts a record back exactly as it was: the undo and redo of every command."""

    def __init__(self, activities: ActivityIndex, store: InjuryStore) -> None:
        self._activities = activities
        self._store = store

    def __call__(self, problem: Problem) -> None:
        """Replaces the record with this version, or adds it if it is gone.

        Raises:
            InjuryStoreError: write failed; nothing changes.
        """
        with self._activities.transaction():
            if self._store.get(problem_id(problem)) is None:
                self._store.add(problem)
            else:
                self._store.replace(problem)


class ListInjuries:
    """The injuries and illnesses of the profile, with where each stands."""

    def __init__(self, store: InjuryStore) -> None:
        self._store = store

    def __call__(self, today: date) -> tuple[InjuryView, ...]:
        """All the records by ascending start day, then id.

        Raises:
            InjuryStoreError: unreadable data.
        """
        return tuple(self._view(problem, today) for problem in self._store.list_problems())

    @staticmethod
    def _view(problem: Problem, today: date) -> InjuryView:
        if isinstance(problem, Illness):
            return InjuryView(problem, None, False, None)
        state = follow(problem)
        return InjuryView(
            problem, state, checkin_due(problem, today), overdue(problem, state, today)
        )
