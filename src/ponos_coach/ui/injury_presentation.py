# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""What the window knows about the injuries, testable without a window.

The use case `ListInjuries` gives each record with its path state; here it becomes a summary of
categories (`ProblemSummary`), the notices to show (`HealthNotice`) and the facts of the status bar.
The words are in `injury_texts`. Nothing here diagnoses: a warning sign is a reason to see a doctor,
a step is how much load the body is asked to take again. Health data: never in logs or errors.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from enum import Enum

from ponos_coach.injury.domain import (
    BodyZone,
    Illness,
    IllnessKind,
    Injury,
    Overdue,
    PathStatus,
    Side,
    Step,
    Verdict,
    WarningSign,
    defaults,
    required_pain_free_days,
)
from ponos_coach.injury.use_cases import InjuryView
from ponos_coach.planning.domain import (
    NoSessionPossible,
    PlanAdapted,
    PlanHalted,
    PlanWarning,
)

type HealthWarning = PlanHalted | PlanAdapted | NoSessionPossible

_SIGN_ORDER = {sign: position for position, sign in enumerate(WarningSign)}


class ProblemKind(Enum):
    """An injury or an illness."""

    INJURY = "injury"
    ILLNESS = "illness"


class ProblemPhase(Enum):
    """Where a record stands: on its way, stopped by a warning sign, or closed."""

    ACTIVE = "active"
    HALTED = "halted"
    CLOSED = "closed"


@dataclass(frozen=True, slots=True)
class ProblemSummary:
    """A record as the screen needs it, in categories (no words, no colours).

    `good_days` and `good_days_needed` are the run of good days and what the step asks to go up
    (0 on the last step); `pain_free_days` and `pain_free_needed` the days with no pain at all
    before running again (`pain_free_needed` is 0 when the step does not ask for them).
    """

    problem_id: str
    kind: ProblemKind
    phase: ProblemPhase
    started_on: date
    ended_on: date | None
    zone: BodyZone | None = None
    side: Side | None = None
    illness_kind: IllnessKind | None = None
    step: Step | None = None
    good_days: int = 0
    good_days_needed: int = 0
    pain_free_days: int = 0
    pain_free_needed: int = 0
    checkin_due: bool = False
    checkin_done: bool = False
    overdue: Overdue | None = None
    ready_to_close: bool = False
    stops_plan: bool = False
    last_verdict: Verdict | None = None
    alarms: tuple[WarningSign, ...] = ()

    @property
    def is_open(self) -> bool:
        """Not closed yet."""
        return self.phase is not ProblemPhase.CLOSED

    @property
    def can_check_in(self) -> bool:
        """An open injury takes the day's answers."""
        return self.kind is ProblemKind.INJURY and self.is_open

    @property
    def can_review(self) -> bool:
        """A doctor can have seen the warning signs."""
        return self.phase is ProblemPhase.HALTED


def summarize(view: InjuryView) -> ProblemSummary:
    """The summary of a record with where its path stands."""
    problem = view.problem
    if isinstance(problem, Illness):
        closed = not problem.is_open
        return ProblemSummary(
            problem.illness_id.value,
            ProblemKind.ILLNESS,
            ProblemPhase.CLOSED if closed else ProblemPhase.ACTIVE,
            problem.started_on,
            problem.recovered_on,
            illness_kind=problem.kind,
            stops_plan=not closed and problem.kind is IllnessKind.SYSTEMIC,
        )
    return _summarize_injury(problem, view)


def _summarize_injury(injury: Injury, view: InjuryView) -> ProblemSummary:
    state = view.state
    if state is None:  # an injury always has a path state; an invalid view is shown as closed
        raise ValueError("an injury view has a path state")
    questionnaire = injury.questionnaire
    if not injury.is_open:
        phase = ProblemPhase.CLOSED
    elif state.status is PathStatus.HALTED:
        phase = ProblemPhase.HALTED
    else:
        phase = ProblemPhase.ACTIVE
    needs_pain_free = phase is ProblemPhase.ACTIVE and state.step is Step.WALKING
    last_step = state.step is Step.INTENSITY
    return ProblemSummary(
        injury.injury_id.value,
        ProblemKind.INJURY,
        phase,
        injury.opened_on,
        injury.closed_on,
        zone=questionnaire.zone,
        side=questionnaire.side,
        step=state.step,
        good_days=state.good_days,
        good_days_needed=0 if last_step else defaults.GOOD_DAYS_TO_STEP_UP[state.step],
        pain_free_days=state.pain_free_days,
        pain_free_needed=(
            required_pain_free_days(questionnaire.zone, questionnaire.onset)
            if needs_pain_free
            else 0
        ),
        checkin_due=view.checkin_due,
        checkin_done=injury.is_open and not view.checkin_due,
        overdue=view.overdue,
        ready_to_close=state.ready_to_close,
        last_verdict=state.last_verdict,
        alarms=tuple(sorted(state.alarms, key=_SIGN_ORDER.__getitem__)),
    )


def summaries_of(views: Iterable[InjuryView]) -> tuple[ProblemSummary, ...]:
    """The summaries of the records, in the order given."""
    return tuple(summarize(view) for view in views)


# --- Notices -------------------------------------------------------------------------------------


class NoticeKind(Enum):
    """What a notice is about. The first three ask for a doctor."""

    WARNING_SIGN = "warning_sign"  # an injury stopped by a sign
    ILLNESS = "illness"  # an illness with fever, malaise or aching muscles
    OVERDUE = "overdue"  # the recovery is slower than expected
    READY_TO_CLOSE = "ready_to_close"  # the last step is done
    PLAN = "plan"  # what the plan does because of the records


@dataclass(frozen=True, slots=True)
class HealthNotice:
    """Something the user should know, with the record or the plan warning it comes from."""

    kind: NoticeKind
    summary: ProblemSummary | None = None
    warning: HealthWarning | None = None


def health_warnings_of(warnings: Iterable[PlanWarning]) -> tuple[HealthWarning, ...]:
    """The warnings of the plan that come from injuries and illnesses."""
    return tuple(w for w in warnings if isinstance(w, PlanHalted | PlanAdapted | NoSessionPossible))


def notices_of(
    summaries: Sequence[ProblemSummary], warnings: Iterable[HealthWarning] = ()
) -> tuple[HealthNotice, ...]:
    """The notices, most important first: the doctor's visits, the slow recoveries, the end of the
    path, and last what the plan does."""
    notices: list[HealthNotice] = []
    for item in summaries:
        if item.phase is ProblemPhase.HALTED:
            notices.append(HealthNotice(NoticeKind.WARNING_SIGN, item))
        elif item.stops_plan:
            notices.append(HealthNotice(NoticeKind.ILLNESS, item))
    notices.extend(
        HealthNotice(NoticeKind.OVERDUE, item) for item in summaries if item.overdue is not None
    )
    notices.extend(
        HealthNotice(NoticeKind.READY_TO_CLOSE, item) for item in summaries if item.ready_to_close
    )
    notices.extend(HealthNotice(NoticeKind.PLAN, warning=warning) for warning in warnings)
    return tuple(notices)


def asks_for_doctor(notice: HealthNotice) -> bool:
    """True when the notice invites a visit to a doctor."""
    return notice.kind in {NoticeKind.WARNING_SIGN, NoticeKind.ILLNESS, NoticeKind.OVERDUE}


def halted_ids(summaries: Iterable[ProblemSummary]) -> frozenset[str]:
    """The ids of the records that ask for a doctor's visit now (a warning sign, a systemic
    illness)."""
    return frozenset(s.problem_id for s in summaries if s.can_review or s.stops_plan)


# --- Status bar ----------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class StatusFacts:
    """What the status bar says about the injuries: a doctor's visit is advised, and how many
    check-ins are missing today."""

    see_doctor: bool = False
    checkins_due: int = 0

    @property
    def is_empty(self) -> bool:
        """Nothing to say."""
        return not self.see_doctor and self.checkins_due == 0


def status_facts_of(summaries: Iterable[ProblemSummary]) -> StatusFacts:
    """The facts of the status bar from the records."""
    items = tuple(summaries)
    return StatusFacts(
        see_doctor=bool(halted_ids(items)),
        checkins_due=sum(1 for s in items if s.checkin_due),
    )


# --- The days of the calendar --------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ProblemSpan:
    """The days a record covers: from its start to its end, or to today while it lasts."""

    kind: ProblemKind
    zone: BodyZone | None
    illness_kind: IllnessKind | None
    start: date
    end: date | None
    problem_id: str = ""  # the record, so that a command on the day can find it

    def covers(self, day: date, today: date) -> bool:
        """True if the record is on that day (an open one reaches today, not the future)."""
        last = self.end if self.end is not None else today
        return self.start <= day <= last


def spans_of(summaries: Iterable[ProblemSummary]) -> tuple[ProblemSpan, ...]:
    """The spans of the records for the calendar."""
    return tuple(
        ProblemSpan(s.kind, s.zone, s.illness_kind, s.started_on, s.ended_on, s.problem_id)
        for s in summaries
    )
