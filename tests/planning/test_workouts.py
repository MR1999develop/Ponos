# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Phase 7: a planned session becomes a structured workout, durations and targets kept."""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from hypothesis import given
from hypothesis import strategies as st

from ponos_coach.activities.domain import Sport
from ponos_coach.goals.domain import Priority, SessionType
from ponos_coach.planning.domain import (
    WORKOUT_PREFIX,
    Block,
    BlockRole,
    Plan,
    PlannedSession,
    ScheduledWorkout,
    StepKind,
    WorkoutSpec,
    WorkoutStep,
    build_plan,
    workout_from_session,
)
from tests.planning.builders import TODAY, inputs, race


def a_block(
    role: BlockRole,
    minutes: int = 10,
    repeats: int = 1,
    *,
    zone: int = 2,
    hr: tuple[int, int] = (120, 140),
    pace: tuple[int | None, int | None] = (None, None),
    race_pace: int | None = None,
) -> Block:
    return Block(role, minutes, zone, repeats, hr[0], hr[1], pace[0], pace[1], race_pace)


def a_session(*blocks: Block, session_type: SessionType = SessionType.EASY) -> PlannedSession:
    minutes = sum(b.minutes * b.repeats for b in blocks)
    return PlannedSession(Sport.RUNNING, session_type, minutes, blocks, 50.0)


# --- Partitions ----------------------------------------------------------------------------------
def test_a_continuous_session_is_one_step_per_block_with_the_zone_heart_rate() -> None:
    session = a_session(
        a_block(BlockRole.WARMUP, 10, hr=(110, 130)),
        a_block(BlockRole.STEADY, 40, hr=(130, 150)),
        a_block(BlockRole.COOLDOWN, 10, hr=(100, 120)),
    )

    spec = workout_from_session(session)

    assert [(s.kind, s.seconds, s.hr_low_bpm, s.hr_high_bpm) for s in spec.steps] == [
        (StepKind.WARMUP, 600, 110, 130),
        (StepKind.INTERVAL, 2400, 130, 150),
        (StepKind.COOLDOWN, 600, 100, 120),
    ]


def test_an_interval_session_alternates_work_and_rest_and_ends_with_work() -> None:
    session = a_session(
        a_block(BlockRole.WARMUP, 10),
        a_block(BlockRole.WORK, 4, repeats=3, zone=4, hr=(160, 170)),
        a_block(BlockRole.REST, 3, repeats=2, zone=1, hr=(90, 110)),
        a_block(BlockRole.COOLDOWN, 10),
        session_type=SessionType.VO2MAX,
    )

    kinds = [s.kind for s in workout_from_session(session).steps]

    assert kinds == [
        StepKind.WARMUP,
        *[StepKind.INTERVAL, StepKind.RECOVERY] * 2,
        StepKind.INTERVAL,
        StepKind.COOLDOWN,
    ]


def test_a_pace_range_is_kept_when_the_zone_has_both_ends_and_dropped_when_it_is_open() -> None:
    closed = a_block(BlockRole.STEADY, pace=(340, 300))
    open_ended = a_block(BlockRole.STEADY, pace=(None, 280))

    steps = workout_from_session(a_session(closed, open_ended)).steps

    assert (steps[0].pace_slow_s_per_km, steps[0].pace_fast_s_per_km) == (340, 300)
    assert (steps[1].pace_slow_s_per_km, steps[1].pace_fast_s_per_km) == (None, None)


def test_a_race_pace_block_targets_the_race_pace_with_a_tolerance_on_each_side() -> None:
    block = a_block(BlockRole.WORK, pace=(310, 290), race_pace=300)

    (step,) = workout_from_session(a_session(block)).steps

    assert (step.pace_slow_s_per_km, step.pace_fast_s_per_km) == (305, 295)


def test_the_name_has_the_prefix_the_type_and_the_minutes() -> None:
    session = a_session(a_block(BlockRole.STEADY, 45), session_type=SessionType.LONG)

    assert workout_from_session(session).name == f"{WORKOUT_PREFIX}Long 45 min"
    assert WORKOUT_PREFIX == "Ponos · "


def test_a_session_with_more_steps_than_a_watch_takes_is_refused() -> None:
    block = a_block(BlockRole.STEADY, 1, repeats=41)

    with pytest.raises(ValueError, match="steps"):
        workout_from_session(a_session(block))


# --- Domain values -------------------------------------------------------------------------------
def test_a_workout_not_named_by_the_app_is_not_a_spec() -> None:
    step = WorkoutStep(StepKind.INTERVAL, 60, 100, 120)

    with pytest.raises(ValueError, match="prefix"):
        WorkoutSpec("Morning run", Sport.RUNNING, (step,))


@pytest.mark.parametrize(
    "bad",
    [
        {"seconds": 0},
        {"hr_low_bpm": 0},
        {"hr_low_bpm": 150, "hr_high_bpm": 140},
        {"pace_slow_s_per_km": 300},
        {"pace_slow_s_per_km": 280, "pace_fast_s_per_km": 300},
    ],
)
def test_an_invalid_step_cannot_be_built(bad: dict[str, int]) -> None:
    values = {"kind": StepKind.INTERVAL, "seconds": 60, "hr_low_bpm": 100, "hr_high_bpm": 120}

    with pytest.raises(ValueError, match=r"step|range"):
        WorkoutStep(**(values | bad))  # type: ignore[arg-type]


def test_a_scheduled_workout_knows_if_the_app_created_it() -> None:
    day = date(2026, 6, 1)

    assert ScheduledWorkout(1, 2, f"{WORKOUT_PREFIX}Easy 30 min", day).is_app_created
    assert not ScheduledWorkout(1, 2, "Tempo run", day).is_app_created
    with pytest.raises(ValueError, match="positive"):
        ScheduledWorkout(0, 2, "x", day)


# --- Properties: durations and targets are conserved ---------------------------------------------
@st.composite
def sessions(draw: st.DrawFn) -> PlannedSession:
    reps = draw(st.integers(min_value=1, max_value=6))
    hr = st.integers(min_value=60, max_value=190)
    pace = st.integers(min_value=150, max_value=600)

    def block(role: BlockRole, repeats: int = 1) -> Block:
        low = draw(hr)
        high = low + draw(st.integers(min_value=0, max_value=30))
        has_pace = draw(st.booleans())
        slow = draw(pace) if has_pace else None
        fast = slow - draw(st.integers(min_value=0, max_value=40)) if slow is not None else None
        race_pace = draw(st.one_of(st.none(), st.integers(min_value=100, max_value=600)))
        zone = draw(st.integers(min_value=1, max_value=5))
        minutes = draw(st.integers(min_value=1, max_value=30))
        return Block(role, minutes, zone, repeats, low, high, slow, fast, race_pace)

    blocks = [block(BlockRole.WARMUP), block(BlockRole.WORK, reps)]
    if reps > 1:
        blocks.append(block(BlockRole.REST, reps - 1))
    blocks.append(block(BlockRole.COOLDOWN))
    return a_session(*blocks)


@given(sessions())
def test_the_workout_lasts_as_long_as_the_blocks(session: PlannedSession) -> None:
    spec = workout_from_session(session)

    assert spec.total_seconds == sum(b.minutes * b.repeats for b in session.blocks) * 60


@given(sessions())
def test_every_step_keeps_the_heart_rate_range_of_its_block(session: PlannedSession) -> None:
    spec = workout_from_session(session)

    expected = [
        (b.minutes * 60, b.hr_low_bpm, b.hr_high_bpm)
        for b in session.blocks
        for _ in range(b.repeats)
    ]
    got = [(s.seconds, s.hr_low_bpm, s.hr_high_bpm) for s in spec.steps]
    assert sorted(got) == sorted(expected)  # alternating changes the order, never the content


@given(sessions())
def test_an_interval_session_has_warm_up_first_cool_down_last_and_an_interval_per_repeat(
    session: PlannedSession,
) -> None:
    kinds = [s.kind for s in workout_from_session(session).steps]

    assert kinds[0] is StepKind.WARMUP
    assert kinds[-1] is StepKind.COOLDOWN
    assert kinds.count(StepKind.INTERVAL) == session.blocks[1].repeats


# --- Against the real plan -----------------------------------------------------------------------
def a_real_plan() -> Plan:
    built = build_plan(inputs([race("a", TODAY + timedelta(days=84), Priority.A)]), TODAY)
    assert isinstance(built, Plan)
    return built


def test_every_session_of_a_real_plan_becomes_a_workout_of_the_same_length() -> None:
    plan = a_real_plan()
    sessions_of_plan = [d.session for d in plan.days if d.session is not None]

    assert sessions_of_plan
    for session in sessions_of_plan:
        spec = workout_from_session(session)
        assert spec.total_seconds == session.minutes * 60
        assert spec.name.startswith(WORKOUT_PREFIX)
        assert all(s.hr_low_bpm <= s.hr_high_bpm for s in spec.steps)


# --- Hills -----------------------------------------------------------------------------------
def hill_plan() -> Plan:
    goal = race("a", TODAY + timedelta(weeks=20), Priority.A, 30_000, ascent_m=1_500)
    built = build_plan(inputs([goal], pace=300), TODAY)
    assert isinstance(built, Plan)
    return built


def test_a_hill_session_has_its_own_name() -> None:
    repeats = PlannedSession(
        Sport.RUNNING, SessionType.VO2MAX, 52, (a_block(BlockRole.STEADY, 52),), 50.0, ascent_m=120
    )
    hilly_long = PlannedSession(
        Sport.RUNNING, SessionType.LONG, 90, (a_block(BlockRole.STEADY, 90),), 80.0, ascent_m=520
    )

    assert workout_from_session(repeats).name == f"{WORKOUT_PREFIX}Hill repeats 52 min"
    assert workout_from_session(hilly_long).name == f"{WORKOUT_PREFIX}Hilly long 90 min"


def test_the_hill_sessions_of_a_real_plan_are_steps_of_heart_rate_only() -> None:
    sessions = [d.session for d in hill_plan().days if d.session and d.session.ascent_m > 0]

    assert any(s.is_hill_repeats for s in sessions)
    assert any(s.is_hilly_long for s in sessions)
    for session in sessions:
        spec = workout_from_session(session)
        assert spec.total_seconds == session.minutes * 60
        assert all(
            s.pace_slow_s_per_km is None and s.pace_fast_s_per_km is None for s in spec.steps
        )
        assert spec.name.startswith(f"{WORKOUT_PREFIX}Hill")


def test_a_hill_repeats_workout_alternates_the_uphill_work_with_the_recovery() -> None:
    repeats = next(
        d.session for d in hill_plan().days if d.session is not None and d.session.is_hill_repeats
    )

    kinds = [s.kind for s in workout_from_session(repeats).steps]

    assert kinds[0] is StepKind.WARMUP
    assert kinds[-1] is StepKind.COOLDOWN
    body = kinds[1:-1]
    assert body[0] is StepKind.INTERVAL
    assert body[-1] is StepKind.INTERVAL
    assert body[1::2] == [StepKind.RECOVERY] * (len(body) // 2)
