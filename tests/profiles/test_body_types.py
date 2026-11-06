# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Tipi dei dati personali (altezza, peso, data di nascita): limiti con on e off point."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from ponos_coach.profiles.domain import BirthDate, FixedProfileData, Height, Weight

TODAY = date(2026, 11, 6)


@pytest.mark.parametrize("cm", [100, 101, 172, 249, 250])
def test_height_accepts_the_limits_and_the_values_between(cm: int) -> None:
    assert Height(cm).cm == cm


@pytest.mark.parametrize("cm", [-1, 0, 99, 251, 10_000])
def test_height_refuses_values_outside_100_to_250_cm(cm: int) -> None:
    with pytest.raises(ValueError, match="altezza"):
        Height(cm)


@pytest.mark.parametrize("tenths", [200, 201, 705, 2999, 3000])
def test_weight_accepts_the_limits_and_the_values_between(tenths: int) -> None:
    assert Weight(tenths).tenths_kg == tenths


@pytest.mark.parametrize("tenths", [-1, 0, 199, 3001])
def test_weight_refuses_values_outside_20_to_300_kg(tenths: int) -> None:
    with pytest.raises(ValueError, match="peso"):
        Weight(tenths)


@pytest.mark.parametrize(
    ("kg", "tenths"),
    [
        (Decimal("20"), 200),
        (Decimal("20.0"), 200),
        (Decimal("70.5"), 705),
        (Decimal("70.50"), 705),  # zeri finali: stessa quantità
        (Decimal("300"), 3000),
    ],
)
def test_weight_from_decimal_kg(kg: Decimal, tenths: int) -> None:
    assert Weight.from_kg(kg) == Weight(tenths)


@pytest.mark.parametrize(
    "kg",
    [
        Decimal("19.9"),  # off point sotto il minimo
        Decimal("300.1"),  # off point sopra il massimo
        Decimal("70.55"),  # due cifre decimali
        Decimal("70.01"),
        Decimal("NaN"),
        Decimal("Infinity"),
        Decimal("1E+999999999"),  # esponente enorme: rifiutato senza costruire l'intero
    ],
)
def test_weight_from_decimal_refuses_what_is_not_a_tenth_in_range(kg: Decimal) -> None:
    with pytest.raises(ValueError, match="peso"):
        Weight.from_kg(kg)


@pytest.mark.parametrize(
    ("text", "tenths"),
    [("70,5", 705), ("70.5", 705), (" 70.5 ", 705), ("70", 700), ("20", 200), ("300,0", 3000)],
)
def test_weight_parse_accepts_comma_and_point(text: str, tenths: int) -> None:
    assert Weight.parse(text) == Weight(tenths)


@pytest.mark.parametrize("text", ["", "abc", "70,5,1", "70.55", "19,9", "300,1", "1e3", "x" * 100])
def test_weight_parse_refuses_non_numbers_and_out_of_range(text: str) -> None:
    with pytest.raises(ValueError, match="peso"):
        Weight.parse(text)


def test_weight_error_messages_do_not_repeat_the_value() -> None:
    with pytest.raises(ValueError) as error:
        Weight.parse("12345,6789")

    assert "12345" not in str(error.value)


def test_weight_is_compared_without_binary_floats() -> None:
    assert Weight.parse("70.1").kg == Decimal("70.1")
    assert Weight.parse("0070.1") == Weight.parse("70,10")


@given(st.integers(min_value=200, max_value=3000))
def test_weight_kg_round_trips_through_text(tenths: int) -> None:
    weight = Weight(tenths)

    assert Weight.parse(str(weight.kg)) == weight
    assert Weight.from_kg(weight.kg) == weight


# --- Data di nascita --------------------------------------------------------------------------


def test_birth_date_on_point_is_five_years_before_today() -> None:
    assert BirthDate.on(date(2021, 11, 6), TODAY).value == date(2021, 11, 6)


def test_birth_date_off_point_the_day_after_is_refused() -> None:
    with pytest.raises(ValueError, match="almeno 5 anni"):
        BirthDate.on(date(2021, 11, 7), TODAY)


def test_birth_date_lower_limit_is_the_first_of_january_1900() -> None:
    assert BirthDate.on(date(1900, 1, 1), TODAY).value == date(1900, 1, 1)
    with pytest.raises(ValueError, match="1900"):
        BirthDate.on(date(1899, 12, 31), TODAY)
    with pytest.raises(ValueError, match="1900"):
        BirthDate(date(1899, 12, 31))


def test_birth_date_in_the_future_is_refused() -> None:
    with pytest.raises(ValueError, match="almeno 5 anni"):
        BirthDate.on(date(2027, 1, 1), TODAY)


def test_birth_date_upper_limit_on_a_leap_day_today() -> None:
    leap_day = date(2028, 2, 29)

    assert BirthDate.on(date(2023, 2, 28), leap_day).value == date(2023, 2, 28)
    with pytest.raises(ValueError, match="almeno 5 anni"):
        BirthDate.on(date(2023, 3, 1), leap_day)


def test_birth_date_error_messages_do_not_repeat_the_date() -> None:
    with pytest.raises(ValueError) as error:
        BirthDate.on(date(2025, 6, 15), TODAY)

    assert "2025" not in str(error.value)


@given(st.dates(min_value=date(1900, 1, 1), max_value=date(2021, 11, 6)))
def test_any_date_from_1900_to_five_years_ago_is_valid(day: date) -> None:
    assert BirthDate.on(day, TODAY).value == day


@given(st.dates(min_value=date(2021, 11, 7), max_value=date(2100, 1, 1)))
def test_any_date_less_than_five_years_ago_is_refused(day: date) -> None:
    with pytest.raises(ValueError, match="almeno 5 anni"):
        BirthDate.on(day, TODAY)


def test_fixed_data_holds_height_and_birth_date() -> None:
    data = FixedProfileData(Height(172), BirthDate(date(1990, 5, 17)))

    assert (data.height.cm, data.birth_date.value) == (172, date(1990, 5, 17))
