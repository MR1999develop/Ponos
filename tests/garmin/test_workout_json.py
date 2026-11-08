# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Garmin's workout JSON: what is built from a spec and what is read from responses (T-SEC-03)."""

from __future__ import annotations

import json
from datetime import date

import pytest
from hypothesis import given
from hypothesis import strategies as st

from ponos_coach.activities.domain import Sport
from ponos_coach.garmin.workout_json import (
    MAX_ENTRIES,
    UnexpectedWorkoutResponseError,
    definition_payload,
    definition_text,
    entry_matches,
    parse_entries,
    parse_entry,
    parse_schedule_id,
    parse_workout_id,
    sport_type,
    workout_payload,
)
from ponos_coach.planning.domain import (
    WORKOUT_PREFIX,
    ScheduledWorkout,
    StepKind,
    WorkoutSpec,
    WorkoutStep,
)
from ponos_coach.planning.workout_calendar import MAX_BACKUP_PAYLOAD_CHARS
from tests.fixtures.garmin import load_json

NAME = f"{WORKOUT_PREFIX}Threshold 55 min"


def a_spec(*steps: WorkoutStep, sport: Sport = Sport.RUNNING) -> WorkoutSpec:
    return WorkoutSpec(NAME, sport, steps or (WorkoutStep(StepKind.INTERVAL, 600, 150, 165),))


def steps_of(payload: dict[str, object]) -> list[dict[str, object]]:
    segments = payload["workoutSegments"]
    assert isinstance(segments, list)
    steps = segments[0]["workoutSteps"]
    assert isinstance(steps, list)
    return steps


# --- Building ------------------------------------------------------------------------------------
def test_a_workout_is_one_segment_of_time_steps_with_the_heart_rate_as_target() -> None:
    spec = a_spec(
        WorkoutStep(StepKind.WARMUP, 600, 110, 130),
        WorkoutStep(StepKind.INTERVAL, 1200, 150, 165),
        WorkoutStep(StepKind.RECOVERY, 180, 90, 110),
        WorkoutStep(StepKind.COOLDOWN, 300, 100, 120),
    )

    payload = workout_payload(spec)

    assert payload["workoutName"] == NAME
    assert payload["estimatedDurationInSecs"] == 2280
    assert payload["sportType"] == {"sportTypeId": 1, "sportTypeKey": "running", "displayOrder": 1}
    steps = steps_of(payload)
    assert [s["stepOrder"] for s in steps] == [1, 2, 3, 4]
    assert [s["stepType"]["stepTypeKey"] for s in steps] == [  # type: ignore[index]
        "warmup",
        "interval",
        "recovery",
        "cooldown",
    ]
    interval = steps[1]
    assert interval["endCondition"]["conditionTypeKey"] == "time"  # type: ignore[index]
    assert interval["endConditionValue"] == 1200.0
    assert interval["targetType"]["workoutTargetTypeKey"] == "heart.rate.zone"  # type: ignore[index]
    assert (interval["targetValueOne"], interval["targetValueTwo"]) == (150.0, 165.0)
    assert "secondaryTargetType" not in interval


def test_a_pace_range_is_the_secondary_target_in_metres_per_second_slowest_first() -> None:
    step = WorkoutStep(
        StepKind.INTERVAL, 600, 150, 165, pace_slow_s_per_km=400, pace_fast_s_per_km=250
    )

    (built,) = steps_of(workout_payload(a_spec(step)))

    assert built["secondaryTargetType"]["workoutTargetTypeKey"] == "pace.zone"  # type: ignore[index]
    assert built["secondaryTargetValueOne"] == pytest.approx(2.5)
    assert built["secondaryTargetValueTwo"] == pytest.approx(4.0)


@pytest.mark.parametrize(
    ("sport", "key"),
    [
        (Sport.RUNNING, "running"),
        (Sport.TRAIL, "running"),
        (Sport.CYCLING, "cycling"),
        (Sport.STRENGTH, "other"),
        (Sport.HIKING, "other"),
    ],
)
def test_the_sport_type_is_running_cycling_or_other(sport: Sport, key: str) -> None:
    assert sport_type(sport)["sportTypeKey"] == key


@st.composite
def specs(draw: st.DrawFn) -> WorkoutSpec:
    steps = []
    for _ in range(draw(st.integers(min_value=1, max_value=12))):
        low = draw(st.integers(min_value=50, max_value=200))
        has_pace = draw(st.booleans())
        slow = draw(st.integers(min_value=100, max_value=900)) if has_pace else None
        fast = draw(st.integers(min_value=50, max_value=slow)) if slow is not None else None
        steps.append(
            WorkoutStep(
                draw(st.sampled_from(StepKind)),
                draw(st.integers(min_value=1, max_value=7200)),
                low,
                low + draw(st.integers(min_value=0, max_value=40)),
                slow,
                fast,
            )
        )
    return WorkoutSpec(NAME, Sport.RUNNING, tuple(steps))


@given(specs())
def test_durations_and_targets_of_the_spec_are_what_the_json_says(spec: WorkoutSpec) -> None:
    payload = json.loads(json.dumps(workout_payload(spec)))  # it must be plain JSON

    built = steps_of(payload)
    assert payload["estimatedDurationInSecs"] == sum(s.seconds for s in spec.steps)
    assert [s["endConditionValue"] for s in built] == [float(s.seconds) for s in spec.steps]
    assert [(s["targetValueOne"], s["targetValueTwo"]) for s in built] == [
        (float(s.hr_low_bpm), float(s.hr_high_bpm)) for s in spec.steps
    ]
    for step, one in zip(spec.steps, built, strict=True):
        if step.pace_slow_s_per_km is None or step.pace_fast_s_per_km is None:
            assert "secondaryTargetValueOne" not in one
        else:
            assert 1000.0 / one["secondaryTargetValueOne"] == pytest.approx(step.pace_slow_s_per_km)  # type: ignore[operator]
            assert 1000.0 / one["secondaryTargetValueTwo"] == pytest.approx(step.pace_fast_s_per_km)  # type: ignore[operator]
            assert one["secondaryTargetValueOne"] <= one["secondaryTargetValueTwo"]  # type: ignore[operator]


# --- Reading the calendar ------------------------------------------------------------------------
def test_a_calendar_response_gives_the_entries_with_their_ids_names_and_days() -> None:
    found, skipped = parse_entries(load_json("scheduled_workouts_by_period.json"))

    assert skipped == 0
    assert [(w.schedule_id, w.workout_id, w.name, w.day) for w in found] == [
        (2000000001, 1000000001, f"{WORKOUT_PREFIX}Easy 30 min", date(2026, 6, 10)),
        (2000000002, 1000000002, "Allenamento di prova", date(2026, 6, 11)),
        (2000000003, 1000000003, f"{WORKOUT_PREFIX}Threshold 55 min", date(2026, 6, 12)),
    ]
    assert [w.is_app_created for w in found] == [True, False, True]


def test_hostile_entries_are_left_out_one_by_one_and_never_repaired() -> None:
    raw = load_json("scheduled_workouts_hostile.json")
    assert isinstance(raw, list)

    found, skipped = parse_entries(raw)

    assert [w.name for w in found] == ["<script>alert(1)</script> ' OR 1=1"]  # text is only text
    assert skipped == len(raw) - 1


@pytest.mark.parametrize("response", [None, {}, "text", 5, {"calendarItems": []}])
def test_a_calendar_response_that_is_not_a_list_is_not_interpretable(response: object) -> None:
    with pytest.raises(UnexpectedWorkoutResponseError):
        parse_entries(response)


def test_a_calendar_response_with_too_many_entries_is_refused_before_reading_them() -> None:
    with pytest.raises(UnexpectedWorkoutResponseError):
        parse_entries([{}] * (MAX_ENTRIES + 1))


def test_an_entry_is_the_chosen_one_only_for_the_same_workout_and_day() -> None:
    raw = load_json("workout_schedule_response.json")
    chosen = ScheduledWorkout(2000000001, 1000000001, NAME, date(2026, 6, 10))

    assert entry_matches(raw, chosen)
    assert not entry_matches(raw, ScheduledWorkout(2000000001, 1000000099, NAME, chosen.day))
    assert not entry_matches(raw, ScheduledWorkout(2000000001, 1000000001, NAME, date(2026, 6, 11)))
    assert not entry_matches({}, chosen)


@pytest.mark.parametrize(
    "value", [{"workoutId": "7"}, {"workoutId": 0}, {"workoutId": 2.0}, [], None]
)
def test_a_workout_id_must_be_a_positive_integer(value: object) -> None:
    with pytest.raises(UnexpectedWorkoutResponseError):
        parse_workout_id(value)


def test_the_ids_of_the_recorded_responses_are_read() -> None:
    assert parse_workout_id(load_json("workout_upload_response.json")) == 1000000001
    assert parse_schedule_id(load_json("workout_schedule_response.json")) == 2000000001
    assert parse_entry("x") is None


# --- Definitions kept for a copy -----------------------------------------------------------------
def test_a_kept_definition_loses_the_ids_and_dates_of_the_original() -> None:
    text = definition_text(load_json("workout_definition.json"))

    body = definition_payload(text)

    assert body["workoutName"] == f"{WORKOUT_PREFIX}Easy 30 min"
    assert not {"workoutId", "ownerId", "createdDate", "updatedDate"} & body.keys()
    assert body["workoutSegments"] == json.loads(text)["workoutSegments"]  # the steps are intact


def test_a_definition_that_is_too_big_or_not_an_object_is_refused() -> None:
    with pytest.raises(UnexpectedWorkoutResponseError):
        definition_text({"workoutName": "x" * MAX_BACKUP_PAYLOAD_CHARS})
    with pytest.raises(UnexpectedWorkoutResponseError):
        definition_text([1, 2])
    with pytest.raises(UnexpectedWorkoutResponseError):
        definition_text({"workoutName": float("nan")})


@pytest.mark.parametrize(
    "text",
    [
        "",
        "not json",
        "[1]",
        '{"workoutName": ""}',
        '{"noName": 1}',
        '{"workoutName": 5}',
        "x" * 300_000,
    ],
)
def test_a_kept_text_that_is_not_a_definition_does_not_become_a_request(text: str) -> None:
    with pytest.raises(UnexpectedWorkoutResponseError):
        definition_payload(text)
