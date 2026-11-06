# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Tipi di dominio dei profili: id, nome, voce del registro, informazioni."""

from __future__ import annotations

import unicodedata
import uuid
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from ponos_coach.profiles.domain import (
    NAME_MAX_CHARS,
    FolderKind,
    InvalidProfileNameError,
    ProfileId,
    ProfileInfo,
    ProfileName,
    RegistryEntry,
)
from tests.builders import IMPORTED_AT, absolute_path

# --- ProfileId ------------------------------------------------------------------------------


def test_profile_id_accepts_a_random_uuid_and_prints_it_canonically() -> None:
    generated = uuid.uuid4()

    assert str(ProfileId(generated)) == str(generated)


@pytest.mark.parametrize(
    "value",
    [
        uuid.UUID(int=0),
        uuid.uuid1(),
        uuid.uuid3(uuid.NAMESPACE_DNS, "x"),
        uuid.uuid5(uuid.NAMESPACE_DNS, "x"),
    ],
)
def test_profile_id_refuses_other_uuid_versions(value: uuid.UUID) -> None:
    with pytest.raises(ValueError, match="versione 4"):
        ProfileId(value)


def test_profile_id_parse_round_trips() -> None:
    text = str(uuid.uuid4())

    assert str(ProfileId.parse(text)) == text


@pytest.mark.parametrize(
    "text",
    [
        "",
        "non un uuid",
        "11111111-1111-4111-8111-11111111111",  # troppo corto
        "11111111-1111-4111-8111-1111111111111",  # troppo lungo
        "11111111-1111-1111-8111-111111111111",  # versione 1
        "11111111-1111-4111-8111-11111111111G",
        "{11111111-1111-4111-8111-111111111111}",  # forma non canonica
        "11111111111141118111111111111111",  # senza trattini
        "AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA",  # maiuscole
        "../../etc/passwd",
    ],
)
def test_profile_id_parse_refuses_other_forms(text: str) -> None:
    with pytest.raises(ValueError, match="id del profilo"):
        ProfileId.parse(text)


# --- ProfileName ----------------------------------------------------------------------------


COMBINING_ACUTE = chr(0x0301)
E_ACUTE = chr(0x00E9)
NO_BREAK_SPACE = chr(0x00A0)


def test_name_is_stripped_and_normalised_to_nfc() -> None:
    assert ProfileName.parse("  Atleta di prova  ").value == "Atleta di prova"
    assert ProfileName.parse("Atl" + "e" + COMBINING_ACUTE).value == "Atl" + E_ACUTE
    assert ProfileName.parse(chr(9) + " Atleta A" + NO_BREAK_SPACE).value == "Atleta A"


def test_name_length_on_and_off_point() -> None:
    assert len(ProfileName.parse("a").value) == 1
    assert len(ProfileName.parse("a" * NAME_MAX_CHARS).value) == NAME_MAX_CHARS
    for text in ("", "   ", "a" * (NAME_MAX_CHARS + 1)):
        with pytest.raises(InvalidProfileNameError):
            ProfileName.parse(text)


def test_length_is_measured_after_normalisation() -> None:
    decomposed = ("e" + COMBINING_ACUTE) * NAME_MAX_CHARS  # 120 punti di codice, 60 dopo NFC

    assert len(ProfileName.parse(decomposed).value) == NAME_MAX_CHARS


def test_huge_input_is_refused_before_any_processing() -> None:
    with pytest.raises(InvalidProfileNameError, match="lungo"):
        ProfileName.parse(" " * 100_000)


@pytest.mark.parametrize(
    "special",
    [
        chr(0x0A),  # a capo
        chr(0x09),  # tabulazione
        chr(0x00),  # NUL
        chr(0x1B),  # ESC
        chr(0x7F),  # DEL
        chr(0x200B),  # zero width space (formato)
        chr(0x202E),  # inversione della direzione del testo
        chr(0x2028),  # separatore di riga
        chr(0x2029),  # separatore di paragrafo
        chr(0xD800),  # surrogato isolato
        chr(0xE000),  # uso privato
    ],
)
def test_names_with_control_or_format_characters_are_refused(special: str) -> None:
    with pytest.raises(InvalidProfileNameError):
        ProfileName.parse("a" + special + "b")


def test_constructor_only_takes_already_normalised_text() -> None:
    assert ProfileName("Atleta di prova").value == "Atleta di prova"
    for text in (" Atleta di prova", "Atleta di prova ", "Atle" + COMBINING_ACUTE):
        with pytest.raises(InvalidProfileNameError, match="normalizzato"):
            ProfileName(text)


@given(st.text(max_size=80))
def test_parse_is_idempotent_and_yields_valid_names(text: str) -> None:
    try:
        name = ProfileName.parse(text)
    except InvalidProfileNameError:
        return

    assert ProfileName.parse(name.value) == name
    assert unicodedata.normalize("NFC", name.value) == name.value
    assert 1 <= len(name.value) <= NAME_MAX_CHARS


def test_invalid_name_error_is_a_value_error() -> None:
    assert issubclass(InvalidProfileNameError, ValueError)


# --- FolderKind, RegistryEntry, ProfileInfo -------------------------------------------------


def test_folder_kinds_have_stable_values() -> None:
    assert {k.value for k in FolderKind} == {"local", "network", "cloud_synced", "unknown"}


def entry(**changes: object) -> RegistryEntry:
    fields: dict[str, object] = {
        "profile_id": ProfileId.parse("11111111-1111-4111-8111-111111111111"),
        "folder": absolute_path("p"),
        "cached_name": ProfileName.parse("Atleta di prova"),
        "last_used_utc": IMPORTED_AT,
    }
    fields.update(changes)
    return RegistryEntry(**fields)  # type: ignore[arg-type]


def test_registry_entry_needs_an_absolute_folder_and_a_utc_time() -> None:
    assert entry().folder.is_absolute()
    with pytest.raises(ValueError, match="assoluto"):
        entry(folder=Path("profili/marta"))
    with pytest.raises(ValueError, match="UTC"):
        entry(last_used_utc=datetime(2026, 4, 1, 12, 0))
    with pytest.raises(ValueError, match="UTC"):
        entry(last_used_utc=datetime(2026, 4, 1, 12, 0, tzinfo=timezone(timedelta(hours=1))))
    assert entry(last_used_utc=datetime(2026, 4, 1, tzinfo=UTC)).last_used_utc.tzinfo is UTC


def info(**changes: object) -> ProfileInfo:
    fields: dict[str, object] = {
        "profile_id": ProfileId.parse("11111111-1111-4111-8111-111111111111"),
        "name": ProfileName.parse("Atleta di prova"),
        "timezone_name": "Europe/Rome",
        "format_version": 1,
    }
    fields.update(changes)
    return ProfileInfo(**fields)  # type: ignore[arg-type]


def test_profile_info_accepts_known_zones_and_versions() -> None:
    assert info().timezone_name == "Europe/Rome"
    assert info(timezone_name="UTC", format_version=7).format_version == 7


@pytest.mark.parametrize("zone", ["", "x" * 65])
def test_profile_info_refuses_empty_or_overlong_zone_names(zone: str) -> None:
    with pytest.raises(ValueError, match="fuso"):
        info(timezone_name=zone)


def test_profile_info_version_starts_at_one() -> None:
    with pytest.raises(ValueError, match="versione"):
        info(format_version=0)
