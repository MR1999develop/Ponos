# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Reading a saved plan back (T-SEC: the text from the database is input, never trusted)."""

from __future__ import annotations

from datetime import timedelta

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from ponos_coach.goals.domain import Priority, SessionType
from ponos_coach.planning.domain import Plan, build_plan
from ponos_coach.planning.domain.serialization import canonical_json
from ponos_coach.planning.plan_codec import (
    MAX_PLAN_DAY_JSON_CHARS,
    MAX_PLAN_JSON_CHARS,
    PlanDecodingError,
    decode_plan,
    decode_plan_day,
    digest,
)
from tests.planning.builders import TODAY, fitness_goal, inputs, race

_PLANS = [
    build_plan(inputs([race("a", TODAY + timedelta(weeks=12), target_time_s=3600)]), TODAY),
    build_plan(
        inputs(
            [
                race("a", TODAY + timedelta(weeks=20), Priority.A, 42_195),
                race("b", TODAY + timedelta(weeks=9), Priority.B, 21_097),
            ],
            [fitness_goal("f", TODAY + timedelta(weeks=5), 60.0)],
            ctl=0.0,
            atl=0.0,
        ),
        TODAY,
    ),
    build_plan(inputs([race("a", TODAY + timedelta(days=400))], ctl=130.0, atl=190.0), TODAY),
    build_plan(
        inputs(
            [race("a", TODAY + timedelta(weeks=3))],
            types={SessionType.EASY},
            pace=None,
        ),
        TODAY,
    ),
]
PLANS = [p for p in _PLANS if isinstance(p, Plan)]


def test_the_scenarios_are_all_real_plans() -> None:
    assert len(PLANS) == len(_PLANS)


@pytest.mark.parametrize("plan", PLANS, ids=range(len(PLANS)))
def test_a_plan_read_back_writes_the_same_text(plan: Plan) -> None:
    text = plan.to_json()

    back = decode_plan(text, digest(text))

    assert back.to_json() == text
    assert back.method_version == plan.method_version
    assert back.inputs.races == plan.inputs.races
    assert back.days[0].day == plan.days[0].day


@given(
    st.integers(1, 56),
    st.sampled_from(list(Priority)),
    st.sampled_from([5_000, 10_000, 21_097, 42_195]),
    st.floats(0, 120),
    st.floats(0, 160),
    st.one_of(st.none(), st.integers(180, 600)),
)
@settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.too_slow])
def test_any_plan_of_the_engine_survives_the_round_trip(
    weeks: int, priority: Priority, distance: int, ctl: float, atl: float, pace: int | None
) -> None:
    built = build_plan(
        inputs(
            [race("a", TODAY + timedelta(weeks=weeks), priority, distance)],
            ctl=ctl,
            atl=atl,
            pace=pace,
        ),
        TODAY,
    )
    assert isinstance(built, Plan)
    text = built.to_json()

    assert decode_plan(text, digest(text)).to_json() == text


@pytest.mark.parametrize("plan", PLANS, ids=range(len(PLANS)))
def test_every_day_of_a_plan_survives_the_round_trip(plan: Plan) -> None:
    for day in plan.days:
        text = canonical_json(day)

        assert canonical_json(decode_plan_day(text, digest(text))) == text


def test_a_wrong_checksum_is_refused_before_anything_else() -> None:
    text = PLANS[0].to_json()

    with pytest.raises(PlanDecodingError, match="checksum"):
        decode_plan(text, digest(text + "x"))


def test_text_beyond_the_size_cap_is_refused_before_it_is_parsed() -> None:
    text = "[" * (MAX_PLAN_JSON_CHARS + 1)  # parsing this would exhaust the stack

    with pytest.raises(PlanDecodingError, match="too big"):
        decode_plan(text, digest(text))
    with pytest.raises(PlanDecodingError, match="too big"):
        decode_plan_day("0" * (MAX_PLAN_DAY_JSON_CHARS + 1), digest("0"))


def test_a_day_is_not_a_plan_and_a_plan_is_not_a_day() -> None:
    day_text = canonical_json(PLANS[0].days[0])
    plan_text = PLANS[0].to_json()

    with pytest.raises(PlanDecodingError):
        decode_plan(day_text, digest(day_text))
    with pytest.raises(PlanDecodingError):
        decode_plan_day(plan_text, digest(plan_text))


@given(st.data())
@settings(max_examples=300, deadline=None, suppress_health_check=[HealthCheck.too_slow])
def test_a_plan_damaged_in_one_place_is_either_read_as_a_valid_plan_or_refused(
    data: st.DataObject,
) -> None:
    text = data.draw(st.sampled_from(PLANS)).to_json()
    position = data.draw(st.integers(0, len(text) - 1))
    damage = data.draw(st.text(max_size=3))
    damaged = text[:position] + damage + text[position + 1 :]

    try:
        plan = decode_plan(damaged, digest(damaged))  # the checksum matches: only content is judged
    except PlanDecodingError:
        return
    # whatever was accepted is a whole plan that writes itself again as canonical text
    assert decode_plan(plan.to_json(), digest(plan.to_json())).to_json() == plan.to_json()
