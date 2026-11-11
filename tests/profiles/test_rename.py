# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""`RenameProfile`: prima il database, poi l'elenco; con i fake in memoria."""

from __future__ import annotations

from datetime import timedelta

import pytest

from ponos_coach.profiles.domain import InvalidProfileNameError, ProfileName
from ponos_coach.profiles.ports import ProfileStorageError
from ponos_coach.profiles.use_cases import OpenProfileFromFolder, RenameProfile
from tests.builders import absolute_path
from tests.profiles.test_use_cases import (
    NAME,
    NOW,
    World,
    _FailingRegistry,
    request,
)

LATER = NOW + timedelta(hours=2)
FOLDER = absolute_path("profili", "atleta")


@pytest.fixture
def world() -> World:
    return World()


def _rename(world: World) -> RenameProfile:
    return RenameProfile(world.registry, lambda: LATER)


def test_a_new_name_goes_to_the_database_then_to_the_registry(world: World) -> None:
    with world.create()(request(FOLDER)) as session:
        result = _rename(world)(session, "  Atleta  nuovo ")

        assert result.name == ProfileName.parse("Atleta  nuovo")
        assert result.registry_updated
        assert session.info.name == result.name
    [entry] = world.registry.list_entries()
    assert entry.cached_name == result.name
    assert entry.last_used_utc == LATER
    reopened = OpenProfileFromFolder(world.ports(), lambda: LATER)(FOLDER)
    with reopened:
        assert reopened.info.name == result.name  # il database lo ricorda


@pytest.mark.parametrize("text", ["", "   ", "a" * 61, "due\nrighe"])
def test_an_invalid_name_is_refused_and_nothing_changes(world: World, text: str) -> None:
    with world.create()(request(FOLDER)) as session:
        with pytest.raises(InvalidProfileNameError):
            _rename(world)(session, text)

        assert session.info.name == NAME
    [entry] = world.registry.list_entries()
    assert entry.cached_name == NAME
    assert entry.last_used_utc == NOW


def test_the_same_name_writes_nothing(world: World) -> None:
    with world.create()(request(FOLDER)) as session:
        result = _rename(world)(session, " Atleta di prova ")

        assert result == type(result)(NAME, registry_updated=True)
    [entry] = world.registry.list_entries()
    assert entry.last_used_utc == NOW  # l'elenco non è stato toccato


def test_a_failing_registry_keeps_the_database_name_and_realigns_at_the_next_opening(
    world: World,
) -> None:
    with world.create()(request(FOLDER)) as session:
        result = RenameProfile(_FailingRegistry(), lambda: LATER)(session, "Altro nome")

        assert not result.registry_updated
        assert session.info.name == ProfileName.parse("Altro nome")
    [stale] = world.registry.list_entries()
    assert stale.cached_name == NAME

    OpenProfileFromFolder(world.ports(), lambda: LATER)(FOLDER).close()

    [entry] = world.registry.list_entries()
    assert entry.cached_name == ProfileName.parse("Altro nome")


def test_a_failing_database_leaves_the_registry_alone(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    def failing(_name: ProfileName) -> None:
        raise ProfileStorageError("disco pieno")

    with world.create()(request(FOLDER)) as session:
        monkeypatch.setattr(session, "rename", failing)
        with pytest.raises(ProfileStorageError):
            _rename(world)(session, "Altro nome")

    [entry] = world.registry.list_entries()
    assert entry.cached_name == NAME
