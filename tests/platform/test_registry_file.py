# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Il file JSON dell'elenco: campi sconosciuti conservati, file non valido mai sovrascritto."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ponos_coach.platform.profile_registry import (
    REGISTRY_FILE_NAME,
    REGISTRY_LOCK_NAME,
    REGISTRY_MAX_BYTES,
    JsonProfileRegistry,
    RegistryCorruptError,
    RegistryFormatTooNewError,
)
from ponos_coach.profiles.ports import ProfileRegistryError
from tests.builders import IMPORTED_AT, registry_entry

_UNREADABLE = [
    pytest.param(b"{ non json", id="not-json"),
    pytest.param(b"\xff\xfe\x00", id="not-utf8"),
    pytest.param(b"[1, 2]", id="root-not-object"),
    pytest.param(b"{}", id="no-version"),
    pytest.param(b'{"format_version": "1", "profiles": []}', id="version-not-int"),
    pytest.param(b'{"format_version": true, "profiles": []}', id="version-bool"),
    pytest.param(b'{"format_version": 0, "profiles": []}', id="version-zero"),
    pytest.param(b'{"format_version": 1}', id="no-profiles"),
    pytest.param(b'{"format_version": 1, "profiles": [3]}', id="entry-not-object"),
    pytest.param(b'{"format_version": 1, "profiles": [{"id": "x"}]}', id="entry-incomplete"),
    pytest.param(b"[" * 100_000, id="deeply-nested"),
]


def _config_file(config_dir: Path) -> Path:
    return config_dir / REGISTRY_FILE_NAME


def _document(config_dir: Path) -> dict[str, object]:
    loaded = json.loads(_config_file(config_dir).read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def test_unknown_fields_are_kept_when_the_file_is_rewritten(tmp_path: Path) -> None:
    config = tmp_path / "config"
    registry = JsonProfileRegistry(config)
    registry.add(registry_entry(1))
    document = _document(config)
    document["future_setting"] = {"a": [1, 2]}
    profiles = document["profiles"]
    assert isinstance(profiles, list)
    profiles[0]["color"] = "blue"
    _config_file(config).write_text(json.dumps(document), encoding="utf-8")

    registry.add(registry_entry(2))
    registry.mark_used(registry_entry(1).profile_id, IMPORTED_AT.replace(year=2027))
    registry.remove(registry_entry(2).profile_id)

    rewritten = _document(config)
    assert rewritten["future_setting"] == {"a": [1, 2]}
    rewritten_profiles = rewritten["profiles"]
    assert isinstance(rewritten_profiles, list)
    assert rewritten_profiles[0]["color"] == "blue"
    assert rewritten_profiles[0]["last_used_utc"].startswith("2027-")


def test_the_file_declares_its_format_version(tmp_path: Path) -> None:
    JsonProfileRegistry(tmp_path).add(registry_entry(1))

    assert _document(tmp_path)["format_version"] == 1


def test_a_missing_file_is_an_empty_list_and_is_not_created_by_reading(tmp_path: Path) -> None:
    registry = JsonProfileRegistry(tmp_path / "config")

    assert registry.list_entries() == []
    assert not (tmp_path / "config").exists()


@pytest.mark.parametrize("content", _UNREADABLE)
def test_an_invalid_file_is_an_explicit_error_and_is_never_overwritten(
    tmp_path: Path, content: bytes
) -> None:
    _config_file(tmp_path).write_bytes(content)
    registry = JsonProfileRegistry(tmp_path)

    with pytest.raises(RegistryCorruptError):
        registry.list_entries()
    with pytest.raises(RegistryCorruptError):
        registry.add(registry_entry(1))

    assert _config_file(tmp_path).read_bytes() == content
    names = {p.name for p in tmp_path.iterdir()} - {REGISTRY_LOCK_NAME}  # il lock resta
    assert names == {REGISTRY_FILE_NAME}


def test_a_file_of_a_future_version_is_refused_and_kept(tmp_path: Path) -> None:
    content = b'{"format_version": 2, "profiles": [], "novita": true}'
    _config_file(tmp_path).write_bytes(content)
    registry = JsonProfileRegistry(tmp_path)

    with pytest.raises(RegistryFormatTooNewError):
        registry.list_entries()
    with pytest.raises(RegistryFormatTooNewError):
        registry.remove(registry_entry(1).profile_id)

    assert _config_file(tmp_path).read_bytes() == content


def test_a_file_over_the_size_limit_is_refused_without_reading_it_all(tmp_path: Path) -> None:
    padding = b" " * (REGISTRY_MAX_BYTES + 1)
    _config_file(tmp_path).write_bytes(b'{"format_version": 1, "profiles": []}' + padding)

    with pytest.raises(RegistryCorruptError):
        JsonProfileRegistry(tmp_path).list_entries()


def test_duplicate_entries_in_the_file_are_corruption(tmp_path: Path) -> None:
    registry = JsonProfileRegistry(tmp_path)
    registry.add(registry_entry(1))
    document = _document(tmp_path)
    profiles = document["profiles"]
    assert isinstance(profiles, list)
    profiles.append(dict(profiles[0]))
    _config_file(tmp_path).write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(RegistryCorruptError):
        registry.list_entries()


@pytest.mark.parametrize(
    "bad_entry",
    [
        pytest.param({"folder": "relative/path"}, id="relative-folder"),
        pytest.param({"name": ""}, id="empty-name"),
        pytest.param({"last_used_utc": "2026-04-01T12:00:00"}, id="naive-instant"),
        pytest.param({"last_used_utc": "ieri"}, id="not-an-instant"),
        pytest.param({"id": "00000000-0000-1000-8000-000000000001"}, id="not-uuid4"),
        pytest.param({"id": 7}, id="id-not-text"),
    ],
)
def test_an_entry_with_an_invalid_value_is_corruption(
    tmp_path: Path, bad_entry: dict[str, object]
) -> None:
    registry = JsonProfileRegistry(tmp_path)
    registry.add(registry_entry(1))
    document = _document(tmp_path)
    profiles = document["profiles"]
    assert isinstance(profiles, list)
    profiles[0].update(bad_entry)
    _config_file(tmp_path).write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(RegistryCorruptError):
        registry.list_entries()


def test_an_unreadable_config_path_is_a_registry_error(tmp_path: Path) -> None:
    _config_file(tmp_path).mkdir()  # una cartella al posto del file

    with pytest.raises(ProfileRegistryError):
        JsonProfileRegistry(tmp_path).list_entries()
