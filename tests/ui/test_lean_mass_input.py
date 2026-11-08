# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Reading the lean mass typed by the user, and the messages of the errors of the lean mass."""

from __future__ import annotations

import pytest

from ponos_coach.nutrition.domain import LeanMass
from ponos_coach.nutrition.ports import LeanMassNotFoundError
from ponos_coach.profiles.lean_mass import (
    LeanMassChangedError,
    LeanMassDateInFutureError,
    LeanMassImplausibleError,
    LeanMassNeedsWeightError,
)
from ponos_coach.ui.errors import describe_error
from ponos_coach.ui.lean_mass_input import LeanMassProblem, lean_mass_problem_text, read_lean_mass


@pytest.mark.parametrize(
    ("text", "tenths"),
    [("50.5", 505), ("50,5", 505), ("  58 ", 580), ("15", 150), ("200.0", 2000), ("+60,", 600)],
)
def test_a_lean_mass_is_read_with_a_comma_or_a_point(text: str, tenths: int) -> None:
    assert read_lean_mass(text) == LeanMass(tenths)


@pytest.mark.parametrize("text", ["", "   ", "\t"])
def test_a_blank_field_is_empty(text: str) -> None:
    assert read_lean_mass(text) is LeanMassProblem.EMPTY


FULLWIDTH_FIFTY = chr(0xFF15) + chr(0xFF10)  # digits that are not ASCII


@pytest.mark.parametrize(
    "text",
    ["abc", "5e1", "50.55", "14.9", "200.1", "1,000.5", "-50", "NaN", "5" * 40, FULLWIDTH_FIFTY],
)
def test_what_is_not_a_lean_mass_is_invalid(text: str) -> None:
    assert read_lean_mass(text) is LeanMassProblem.INVALID


def test_the_problem_texts_say_what_to_type_without_repeating_the_value() -> None:
    assert lean_mass_problem_text(LeanMassProblem.EMPTY) == "Enter the lean mass in kilograms."
    invalid = lean_mass_problem_text(LeanMassProblem.INVALID)
    assert invalid == ("Enter the lean mass in kilograms with at most one decimal, from 15 to 200.")


@pytest.mark.parametrize(
    "error",
    [
        LeanMassDateInFutureError(),
        LeanMassNeedsWeightError(),
        LeanMassImplausibleError(),
        LeanMassChangedError(),
        LeanMassNotFoundError(),
    ],
)
def test_every_refusal_of_the_lean_mass_has_what_and_remedy_without_the_exception_text(
    error: Exception,
) -> None:
    message = describe_error(error)

    assert message.title and message.what and message.remedy
    assert "Traceback" not in message.what
    assert "{" not in message.what + message.remedy
    assert message.title != "Something went wrong"  # not the generic message
