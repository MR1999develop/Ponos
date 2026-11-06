# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Modulo "Nuovo profilo": destinazione, stima della soglia e validazione, senza finestra."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from ponos_coach.load.domain import HeartRateLimits, Physiology, Sex, ThresholdHeartRate
from ponos_coach.profiles.domain import BirthDate, FolderState, Height, Weight
from ponos_coach.profiles.use_cases import NewProfileRequest
from ponos_coach.ui.body_input import (
    latest_birth_date,
    read_birth_date,
    read_height,
    read_weight,
)
from ponos_coach.ui.heart_rate_rules import MAX_ALLOWED_BPM, MIN_ALLOWED_BPM, MIN_THRESHOLD_BPM
from ponos_coach.ui.new_profile_form import (
    Field,
    NewProfileForm,
    Problem,
    estimated_threshold,
    folder_name_from,
)

BASE = Path("profiles").absolute()
TODAY = date(2026, 5, 1)


def make_form(name: str = "Ada") -> NewProfileForm:
    result = NewProfileForm("Europe/Rome")
    result.name = name
    result.choose_folder(str(BASE))
    result.height_text = "175"
    result.weight_text = "68,5"
    result.birth_date = date(1990, 5, 1)
    return result


def test_the_folder_starts_empty_and_blocks_creation_until_chosen() -> None:
    form = NewProfileForm("Europe/Rome")
    form.name = "Ada"

    assert form.folder_text == ""
    assert form.destination == ""
    assert form.folder_problem() is Problem.FOLDER_EMPTY

    form.choose_folder(str(BASE))
    assert form.folder_problem() is None


def test_an_empty_or_missing_folder_is_the_destination() -> None:
    form = make_form("Ada")

    form.folder_state = FolderState.EMPTY
    assert form.destination == str(BASE)
    form.folder_state = FolderState.MISSING
    assert form.destination == str(BASE)
    assert not form.uses_subfolder


def test_a_non_empty_folder_gets_a_subfolder_named_after_the_profile() -> None:
    form = make_form("Ada")
    form.folder_state = FolderState.NOT_EMPTY

    assert form.uses_subfolder
    assert form.destination == str(BASE / "Ada")
    assert form.problems(TODAY) == ()
    form.name = "Ada B"
    assert form.destination == str(BASE / "Ada B")
    request = form.build_request(TODAY)
    assert isinstance(request, NewProfileRequest)
    assert request.folder == BASE / "Ada B"


def test_choosing_another_folder_forgets_the_previous_examination() -> None:
    form = make_form("Ada")
    form.folder_state = FolderState.NOT_EMPTY

    form.choose_folder(str(BASE / "other"))

    assert not form.uses_subfolder
    assert form.destination == str(BASE / "other")


def test_characters_not_allowed_in_a_folder_name_are_replaced() -> None:
    assert folder_name_from('a/b:c*"d') == "a_b_c__d"
    assert folder_name_from("trailing. ") == "trailing"
    form = make_form("")
    form.folder_state = FolderState.NOT_EMPTY
    assert form.destination == ""


def test_threshold_is_an_estimate_at_90_percent_until_the_user_sets_it() -> None:
    form = make_form()
    assert form.threshold_is_estimate
    assert form.threshold_bpm == 171  # 90% di 190
    form.max_bpm = 200
    assert form.threshold_bpm == 180


def test_a_threshold_set_by_the_user_is_no_longer_an_estimate() -> None:
    form = make_form()

    form.set_threshold(175)
    form.max_bpm = 200

    assert not form.threshold_is_estimate
    assert form.threshold_bpm == 175


def test_estimated_threshold_stays_in_the_allowed_range() -> None:
    assert estimated_threshold(MAX_ALLOWED_BPM) == 225
    assert estimated_threshold(MIN_ALLOWED_BPM) == MIN_THRESHOLD_BPM


def test_allowed_ranges_match_the_domain() -> None:
    HeartRateLimits(MIN_ALLOWED_BPM, MAX_ALLOWED_BPM)
    ThresholdHeartRate(MIN_THRESHOLD_BPM)
    ThresholdHeartRate(MAX_ALLOWED_BPM)
    with pytest.raises(ValueError, match="FC"):
        HeartRateLimits(MIN_ALLOWED_BPM - 1, 100)
    with pytest.raises(ValueError, match="FC"):
        HeartRateLimits(50, MAX_ALLOWED_BPM + 1)
    with pytest.raises(ValueError, match="FC"):
        ThresholdHeartRate(MIN_THRESHOLD_BPM - 1)


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("", Problem.NAME_EMPTY),
        ("   ", Problem.NAME_EMPTY),
        ("x" * 61, Problem.NAME_TOO_LONG),
        ("bad" + chr(7) + "name", Problem.NAME_INVALID),
    ],
)
def test_invalid_names_are_reported(name: str, expected: Problem) -> None:
    form = make_form(name)
    form.choose_folder(str(BASE / "x"))

    assert form.problems(TODAY) == (expected,)


def test_a_relative_folder_is_reported() -> None:
    form = make_form()
    form.choose_folder("relative/folder")
    assert form.problems(TODAY) == (Problem.FOLDER_NOT_ABSOLUTE,)


def test_heart_rate_relations_are_reported_on_the_right_field() -> None:
    form = make_form()
    form.min_bpm, form.max_bpm = 120, 100
    assert Problem.MIN_NOT_BELOW_MAX in form.problems(TODAY)
    assert Problem.MIN_NOT_BELOW_MAX.field is Field.MIN_BPM

    form = make_form()
    form.set_threshold(form.min_bpm)
    assert form.problems(TODAY) == (Problem.THRESHOLD_NOT_ABOVE_MIN,)
    form.set_threshold(form.max_bpm + 1)
    assert form.problems(TODAY) == (Problem.THRESHOLD_ABOVE_MAX,)


def test_a_valid_form_builds_the_request() -> None:
    form = make_form("  Ada  ")
    form.sex = Sex.FEMALE

    request = form.build_request(TODAY)

    assert isinstance(request, NewProfileRequest)
    assert request.name.value == "Ada"
    assert request.timezone_name == "Europe/Rome"
    assert request.today == TODAY
    assert request.physiology == Physiology(
        HeartRateLimits(50, 190), ThresholdHeartRate(171), Sex.FEMALE
    )


def test_an_invalid_form_returns_the_problems_instead() -> None:
    form = make_form("")
    form.choose_folder("")
    assert form.build_request(TODAY) == (Problem.NAME_EMPTY, Problem.FOLDER_EMPTY)


def test_a_new_form_asks_for_the_body_data_without_inventing_any() -> None:
    form = NewProfileForm("Europe/Rome")
    form.name = "Ada"
    form.choose_folder(str(BASE))

    assert form.problems(TODAY) == (
        Problem.HEIGHT_EMPTY,
        Problem.WEIGHT_EMPTY,
        Problem.BIRTH_EMPTY,
    )
    assert Problem.HEIGHT_EMPTY.field is Field.HEIGHT
    assert Problem.WEIGHT_EMPTY.field is Field.WEIGHT
    assert Problem.BIRTH_EMPTY.field is Field.BIRTH_DATE


def test_the_request_carries_height_weight_and_birth_date() -> None:
    request = make_form().build_request(TODAY)

    assert isinstance(request, NewProfileRequest)
    assert request.fixed is not None
    assert request.fixed.height == Height(175)
    assert request.fixed.birth_date == BirthDate(date(1990, 5, 1))
    assert request.weight == Weight.from_kg(Decimal("68.5"))


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("100", None),  # on point: il minimo
        ("250", None),  # on point: il massimo
        ("99", Problem.HEIGHT_INVALID),  # off point
        ("251", Problem.HEIGHT_INVALID),  # off point
        ("", Problem.HEIGHT_EMPTY),
        ("  ", Problem.HEIGHT_EMPTY),
        ("17.5", Problem.HEIGHT_INVALID),
        ("1_70", Problem.HEIGHT_INVALID),
        ("-175", Problem.HEIGHT_INVALID),
        (
            "".join(chr(0x660 + digit) for digit in (1, 7, 5)),
            Problem.HEIGHT_INVALID,
        ),  # cifre arabo-indiche
        ("1750", Problem.HEIGHT_INVALID),
        ("x" * 5000, Problem.HEIGHT_INVALID),  # la lunghezza prima del resto
    ],
)
def test_height_is_a_whole_number_of_centimetres_between_100_and_250(
    text: str, expected: Problem | None
) -> None:
    form = make_form()
    form.height_text = text

    assert form.problems(TODAY) == (() if expected is None else (expected,))


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("68,5", None),  # la virgola
        ("68.5", None),  # il punto
        ("20", None),  # on point: il minimo
        ("300,0", None),  # on point: il massimo
        ("19,9", Problem.WEIGHT_INVALID),  # off point
        ("300,1", Problem.WEIGHT_INVALID),  # off point
        ("70,55", Problem.WEIGHT_INVALID),  # più di un decimale
        ("abc", Problem.WEIGHT_INVALID),
        ("nan", Problem.WEIGHT_INVALID),
        ("1e400", Problem.WEIGHT_INVALID),
        ("", Problem.WEIGHT_EMPTY),
        ("9" * 5000, Problem.WEIGHT_INVALID),
    ],
)
def test_weight_accepts_comma_and_point_between_20_and_300_with_one_decimal(
    text: str, expected: Problem | None
) -> None:
    form = make_form()
    form.weight_text = text

    assert form.problems(TODAY) == (() if expected is None else (expected,))


def test_birth_date_limits_come_from_the_domain_on_and_off_point() -> None:
    latest = latest_birth_date(TODAY)
    assert latest == date(2021, 5, 1)  # cinque anni prima di oggi
    form = make_form()

    form.birth_date = latest  # on point
    assert form.problems(TODAY) == ()
    form.birth_date = latest + timedelta(days=1)  # off point
    assert form.problems(TODAY) == (Problem.BIRTH_INVALID,)
    form.birth_date = date(1900, 1, 1)  # on point
    assert form.problems(TODAY) == ()
    form.birth_date = date(1899, 12, 31)  # off point
    assert form.problems(TODAY) == (Problem.BIRTH_INVALID,)
    form.birth_date = None
    assert form.problems(TODAY) == (Problem.BIRTH_EMPTY,)


def test_the_latest_birth_date_follows_the_leap_day_rule_of_the_domain() -> None:
    today = date(2028, 2, 29)

    latest = latest_birth_date(today)

    BirthDate.on(latest, today)
    with pytest.raises(ValueError, match="anni"):
        BirthDate.on(latest + timedelta(days=1), today)


def test_body_problems_never_repeat_the_value_that_was_typed() -> None:
    form = make_form()
    form.height_text = "31337"
    form.weight_text = "66.66"

    texts = {str(problem) for problem in form.problems(TODAY)}

    assert not any("31337" in text or "66.66" in text for text in texts)


@pytest.mark.parametrize(
    ("name", "folder"),
    [
        ("CON", "CON_profile"),
        ("con", "con_profile"),
        ("Prn.txt", "Prn_profile.txt"),
        ("COM1", "COM1_profile"),
        ("lpt9", "lpt9_profile"),
        ("Aux ", "Aux_profile"),
        ("NUL.tar.gz", "NUL_profile.tar.gz"),
        ("COM¹", "COM¹_profile"),
    ],
)
def test_the_folder_name_never_uses_a_windows_reserved_device_name(name: str, folder: str) -> None:
    assert folder_name_from(name) == folder


@pytest.mark.parametrize("name", ["Console", "COM10", "LPT0", "CON:", "Ada con", "a.CON", "COM"])
def test_names_that_only_look_reserved_are_left_alone(name: str) -> None:
    derived = folder_name_from(name)

    assert derived == name.replace(":", "_")


def test_the_subfolder_for_a_reserved_name_is_suffixed() -> None:
    form = make_form("AUX")
    form.folder_state = FolderState.NOT_EMPTY

    assert form.destination == str(BASE / "AUX_profile")
    assert form.problems(TODAY) == ()


def counting(calls: list[str], name: str, reader: Callable[..., object]) -> Callable[..., object]:
    """Lo stesso lettore, che annota ogni chiamata."""

    def read(*args: object) -> object:
        calls.append(name)
        return reader(*args)

    return read


def test_building_the_request_reads_height_weight_and_birth_date_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    target = "ponos_coach.ui.new_profile_form"
    monkeypatch.setattr(f"{target}.read_height", counting(calls, "height", read_height))
    monkeypatch.setattr(f"{target}.read_weight", counting(calls, "weight", read_weight))
    monkeypatch.setattr(f"{target}.read_birth_date", counting(calls, "birth", read_birth_date))
    form = make_form("Atleta di prova")
    form.choose_folder(str(BASE))
    form.height_text = "172"
    form.weight_text = "68,5"
    form.birth_date = date(1990, 5, 1)

    result = form.build_request(TODAY)

    assert isinstance(result, NewProfileRequest)
    assert sorted(calls) == ["birth", "height", "weight"]  # una lettura per valore, non due
