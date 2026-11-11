# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Phase 6 acceptance campaign on the real stack (SQLite profile, imported FIT runs, the use cases
and `RebuildPlan` as the app wires them): T-INF-01, T-INF-03, T-PIANO-02 with injuries and
illnesses, the 28-day lower ramp and the undo of every injury command.

Every value comes from an invented athlete; the doctor's notes are neutral text.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterator
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest

from ponos_coach.goals.domain import INTENSE_TYPES, Priority, SessionType
from ponos_coach.injury.domain import (
    BodyZone,
    CheckIn,
    HaltReason,
    Illness,
    IllnessKind,
    Injury,
    InjuryId,
    PainScore,
    PathStatus,
    Questionnaire,
    Scope,
    WarningSign,
    follow,
    scope_of,
)
from ponos_coach.injury.ports import InjuryStoreError
from ponos_coach.injury.use_cases import (
    CloseProblem,
    ConfirmMedicalReview,
    InvalidInjuryChangeError,
    NoWarningSignToReviewError,
    OpenInjury,
    RecordCheckIn,
    RemoveProblem,
    ReportIllness,
    RestoreProblem,
)
from ponos_coach.planning.domain import Plan, PlanHalted
from ponos_coach.profiles.rebuild_plan import PlanBuilt
from tests.collaudo.fase5_support import TODAY, World, clone_world, make_world, race
from tests.injury import builders
from tests.planning.invariants import violations

SIGNS = list(WarningSign)


def quest(zone: BodyZone = BodyZone.SHIN, **kwargs: Any) -> Questionnaire:
    """The builders' questionnaire, with the onset long before any opening used here."""
    return replace(builders.questionnaire(zone, **kwargs), onset_day=date(2026, 1, 1))


RACE_DAY = TODAY + timedelta(weeks=16)


@pytest.fixture(scope="module")
def template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    base = tmp_path_factory.mktemp("f6-template") / "p"
    base.mkdir()
    built = make_world(base)
    built.import_history(TODAY - timedelta(days=42), TODAY - timedelta(days=1), every=2)
    built.add_goal(race("a", RACE_DAY, Priority.A, 10_000))
    built.close()
    return base


@pytest.fixture
def world(template: Path, tmp_path: Path) -> Iterator[World]:
    opened = clone_world(template, tmp_path / "w")
    yield opened
    opened.close()


def commands(
    world: World,
) -> tuple[
    OpenInjury,
    RecordCheckIn,
    ConfirmMedicalReview,
    CloseProblem,
    RemoveProblem,
    RestoreProblem,
    ReportIllness,
]:
    s = world.profile.session
    return (
        OpenInjury(s.injuries),
        RecordCheckIn(s.activities, s.injuries),
        ConfirmMedicalReview(s.activities, s.injuries),
        CloseProblem(s.activities, s.injuries),
        RemoveProblem(s.activities, s.injuries),
        RestoreProblem(s.activities, s.injuries),
        ReportIllness(s.activities, s.injuries),
    )


def plan_of(world: World) -> Plan:
    built = world.rebuild(world.today)
    assert isinstance(built, PlanBuilt), built
    return built.plan


def kinds(plan: Plan) -> Counter[SessionType]:
    return Counter(d.session.session_type for d in plan.days if d.session)


def parameter(plan: Plan, name: str) -> float:
    return float(dict(plan.parameters)[name])


def test_the_plan_without_problems_has_sessions_and_the_normal_ramp(world: World) -> None:
    plan = plan_of(world)

    assert sum(kinds(plan).values()) > 20
    assert parameter(plan, "max_ramp_per_week") == 5.0


# --- T-INF-01 ----------------------------------------------------------------------------------
@pytest.mark.parametrize("sign", SIGNS)
def test_t_inf_01_every_sign_in_the_questionnaire_empties_the_plan_until_the_review(
    world: World, sign: WarningSign
) -> None:
    open_injury, _checkin, review, *_ = commands(world)
    open_injury(Injury(InjuryId("i1"), TODAY, quest(BodyZone.CALF, signs=[sign])))

    plan = plan_of(world)

    assert kinds(plan) == Counter()
    expected = (
        HaltReason.WHOLE_PLAN_SIGN if scope_of(sign) is Scope.WHOLE_PLAN else HaltReason.ZONE_SIGN
    )
    assert PlanHalted(expected, ("i1",)) in plan.warnings
    assert [d.race.goal_id for d in plan.days if d.race] == ["a"]  # the race is still there

    review(InjuryId("i1"), TODAY)  # the doctor has seen it: the path restarts from the first step
    reviewed = plan_of(world)
    assert not any(isinstance(w, PlanHalted) for w in reviewed.warnings)
    assert violations(reviewed) == []
    # activation step: no running at all, whatever the sign was
    assert not set(kinds(reviewed)) & {t for t in SessionType if t is not SessionType.ALTERNATIVE}


@pytest.mark.parametrize("sign", SIGNS)
def test_t_inf_01_every_sign_on_a_check_in_empties_the_plan_the_same_day(
    world: World, sign: WarningSign
) -> None:
    open_injury, record, *_ = commands(world)
    open_injury(Injury(InjuryId("i1"), TODAY - timedelta(days=5), quest(BodyZone.SHIN)))
    assert sum(kinds(plan_of(world)).values()) > 0

    record(InjuryId("i1"), CheckIn(TODAY, None, PainScore(1), frozenset([sign])))

    plan = plan_of(world)
    assert kinds(plan) == Counter()
    assert any(isinstance(w, PlanHalted) for w in plan.warnings)


def test_a_review_with_nothing_to_review_is_refused_and_changes_nothing(world: World) -> None:
    open_injury, _record, review, *_ = commands(world)
    open_injury(Injury(InjuryId("i1"), TODAY, quest()))
    before = world.profile.session.injuries.get(InjuryId("i1"))

    with pytest.raises(NoWarningSignToReviewError):
        review(InjuryId("i1"), TODAY)

    assert world.profile.session.injuries.get(InjuryId("i1")) == before


def test_a_review_before_the_opening_or_after_a_close_is_refused(world: World) -> None:
    open_injury, _record, review, close, *_ = commands(world)
    open_injury(Injury(InjuryId("i1"), TODAY, quest(signs=[WarningSign.NIGHT_PAIN])))

    with pytest.raises(InvalidInjuryChangeError):
        review(InjuryId("i1"), TODAY - timedelta(days=1))
    close(InjuryId("i1"), TODAY)
    with pytest.raises(InvalidInjuryChangeError):
        review(InjuryId("i1"), TODAY)


# --- Illness -----------------------------------------------------------------------------------
def test_an_illness_above_the_neck_keeps_only_short_easy_sessions(world: World) -> None:
    *_, report = commands(world)
    report(Illness(InjuryId("c1"), IllnessKind.NECK_UP, TODAY))

    plan = plan_of(world)

    used = set(kinds(plan))
    assert used <= {SessionType.RECOVERY, SessionType.ALTERNATIVE}
    assert not used & (INTENSE_TYPES | {SessionType.LONG, SessionType.STRENGTH})
    assert parameter(plan, "max_ramp_per_week") == 3.0
    assert violations(plan) == []


def test_a_systemic_illness_stops_the_plan_until_the_user_says_they_feel_well(
    world: World,
) -> None:
    *_, report = commands(world)
    close = commands(world)[3]
    report(Illness(InjuryId("c1"), IllnessKind.SYSTEMIC, TODAY))

    halted = plan_of(world)
    close(InjuryId("c1"), TODAY)
    back = plan_of(world)

    assert kinds(halted) == Counter() and PlanHalted(HaltReason.ILLNESS, ("c1",)) in halted.warnings
    assert sum(kinds(back).values()) > 20
    assert parameter(back, "max_ramp_per_week") == 3.0  # after the illness: the lower ramp


def test_only_one_illness_can_be_open_and_the_second_changes_nothing(world: World) -> None:
    *_, report = commands(world)
    report(Illness(InjuryId("c1"), IllnessKind.NECK_UP, TODAY))
    before = world.profile.session.injuries.list_problems()

    with pytest.raises(Exception, match="already open"):
        report(Illness(InjuryId("c2"), IllnessKind.SYSTEMIC, TODAY))

    assert world.profile.session.injuries.list_problems() == before


# --- The lower ramp: +3 CTL a week while open and for 28 days after ------------------------------
@pytest.mark.parametrize(
    ("after_days", "reduced"), [(0, True), (27, True), (28, True), (29, False)]
)
def test_the_ramp_is_3_for_28_days_after_the_return_and_5_from_the_29th(
    world: World, after_days: int, reduced: bool
) -> None:
    open_injury, _record, _review, close, *_ = commands(world)
    open_injury(Injury(InjuryId("i1"), TODAY - timedelta(days=60), quest()))
    close(InjuryId("i1"), TODAY - timedelta(days=after_days))

    plan = plan_of(world)

    assert parameter(plan, "max_ramp_per_week") == (3.0 if reduced else 5.0)
    assert violations(plan) == []


def test_while_the_injury_is_open_every_week_of_the_plan_respects_the_ramp_of_3(
    world: World,
) -> None:
    open_injury, *_ = commands(world)
    open_injury(Injury(InjuryId("i1"), TODAY, quest(BodyZone.THIGH, morning=1)))

    plan = plan_of(world)

    assert violations(plan) == []  # includes the ramp check against `max_ramp_per_week`


def test_closing_with_a_day_after_today_still_lowers_the_ramp(world: World) -> None:
    """D-F6-01 (low): `recently_returned` needs `0 <= today - closed_on`, and an injury with a
    `closed_on` is not open, so closing on a future day leaves the plan at +5 for those days."""
    open_injury, _record, _review, close, *_ = commands(world)
    open_injury(Injury(InjuryId("i1"), TODAY - timedelta(days=3), quest()))
    close(InjuryId("i1"), TODAY + timedelta(days=3))

    assert parameter(plan_of(world), "max_ramp_per_week") == 3.0


# --- Two problems at once ----------------------------------------------------------------------
def test_two_zones_follow_the_stricter_one_and_a_review_of_one_leaves_the_other(
    world: World,
) -> None:
    open_injury, _record, review, *_ = commands(world)
    open_injury(Injury(InjuryId("a"), TODAY, quest(BodyZone.CALF, signs=[WarningSign.NIGHT_PAIN])))
    open_injury(Injury(InjuryId("b"), TODAY, quest(BodyZone.SHIN)))

    both = plan_of(world)
    review(InjuryId("a"), TODAY)
    one = plan_of(world)

    assert kinds(both) == Counter() and PlanHalted(HaltReason.ZONE_SIGN, ("a",)) in both.warnings
    assert not any(isinstance(w, PlanHalted) for w in one.warnings)
    states = {}
    for ident in ("a", "b"):
        problem = world.profile.session.injuries.get(InjuryId(ident))
        assert isinstance(problem, Injury)
        states[ident] = follow(problem).status
    assert states == {"a": PathStatus.ACTIVE, "b": PathStatus.ACTIVE}


def test_the_plan_is_the_same_whatever_the_order_the_problems_were_opened(
    template: Path, tmp_path: Path
) -> None:
    results = []
    for n, order in enumerate((("a", "b"), ("b", "a"))):
        opened = clone_world(template, tmp_path / f"o{n}")
        try:
            open_injury = commands(opened)[0]
            for ident in order:
                zone = BodyZone.KNEE if ident == "a" else BodyZone.FOOT
                open_injury(Injury(InjuryId(ident), TODAY, quest(zone)))
            results.append(plan_of(opened).to_json())
        finally:
            opened.close()

    assert results[0] == results[1]


def test_a_checkin_replaces_the_one_of_the_same_day_and_replays_to_the_same_state(
    world: World,
) -> None:
    open_injury, record, *_ = commands(world)
    open_injury(Injury(InjuryId("i1"), TODAY - timedelta(days=10), quest()))
    for day in range(1, 8):
        record(
            InjuryId("i1"), CheckIn(TODAY - timedelta(days=10 - day), PainScore(1), PainScore(1))
        )
    before = world.profile.session.injuries.get(InjuryId("i1"))
    assert isinstance(before, Injury)

    record(
        InjuryId("i1"), CheckIn(TODAY - timedelta(days=3), PainScore(2), PainScore(1))
    )  # same day, again
    after = world.profile.session.injuries.get(InjuryId("i1"))

    assert isinstance(after, Injury)
    assert len(after.checkins) == len(before.checkins)
    assert follow(replace(after, checkins=before.checkins)) == follow(before)


# --- No health data in the logs ------------------------------------------------------------------
def test_t_sec_08_a_whole_injury_life_on_the_real_stack_leaves_no_health_value_in_any_log(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    import logging  # noqa: PLC0415

    caplog.set_level(logging.DEBUG)
    note = "SENTINEL-NOTE-5521 right shin"
    ident = "SENTINEL-ID-5521"
    open_injury, record, review, close, remove, restore, report = commands(world)
    opened = open_injury(
        Injury(
            InjuryId(ident),
            TODAY - timedelta(days=9),
            replace(
                quest(BodyZone.LOWER_BACK, signs=[WarningSign.BLADDER_BOWEL_OR_SPREADING_WEAKNESS]),
                doctor_notes=note,
            ),
        )
    )
    plan_of(world)
    record(InjuryId(ident), CheckIn(TODAY - timedelta(days=4), PainScore(7), PainScore(6)))
    review(InjuryId(ident), TODAY - timedelta(days=3))
    plan_of(world)
    report(Illness(InjuryId("SENTINEL-ILL-5521"), IllnessKind.SYSTEMIC, TODAY))
    plan_of(world)
    changed = close(InjuryId(ident), TODAY)
    removed = remove(InjuryId(ident))
    restore(removed.problem)
    restore(changed.before)
    assert opened.problem is not None
    with pytest.raises(InjuryStoreError) as caught:
        open_injury(Injury(InjuryId(ident), TODAY, quest()))  # the same id again
    text = caplog.text + str(caught.value) + repr(caught.value.__cause__)

    for secret in (note, ident, "SENTINEL-ILL-5521", "lower_back", "bladder", "systemic"):
        assert secret not in text, secret
