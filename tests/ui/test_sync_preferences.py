# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Preferenze della sincronizzazione: una scelta per profilo, valori illeggibili = predefinito."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from PySide6.QtCore import QSettings

from ponos_coach.profiles.domain import ProfileId
from ponos_coach.ui.sync_preferences import (
    MemorySyncPreferences,
    QSettingsSyncPreferences,
    parse_stored_flag,
)

FIRST = ProfileId(uuid.UUID("00000000-0000-4000-8000-000000000001"))
SECOND = ProfileId(uuid.UUID("00000000-0000-4000-8000-000000000002"))


def settings_at(folder: Path) -> QSettings:
    return QSettings(str(folder / "settings.ini"), QSettings.Format.IniFormat)


def test_a_profile_without_a_saved_choice_synchronises_when_it_opens(tmp_path: Path) -> None:
    assert MemorySyncPreferences().auto_sync(FIRST)
    assert QSettingsSyncPreferences(settings_at(tmp_path)).auto_sync(FIRST)


def test_the_choice_is_remembered_per_profile_and_survives_a_restart(tmp_path: Path) -> None:
    first_run = QSettingsSyncPreferences(settings_at(tmp_path))
    first_run.set_auto_sync(FIRST, enabled=False)
    first_run.set_auto_sync(SECOND, enabled=True)
    first_run._settings.sync()

    second_run = QSettingsSyncPreferences(settings_at(tmp_path))

    assert not second_run.auto_sync(FIRST)
    assert second_run.auto_sync(SECOND)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (True, True),
        (False, False),
        ("true", True),
        ("false", False),
        (" FALSE ", False),
        ("1", True),
        ("0", False),
        ("forse", True),
        ("", True),
        (None, True),
        (7, True),
        (["false"], True),
    ],
)
def test_a_stored_value_is_validated_and_anything_odd_means_the_default(
    raw: object, *, expected: bool
) -> None:
    assert parse_stored_flag(raw, default=True) is expected


def test_a_garbage_value_in_the_settings_does_not_stop_the_synchronisation(
    tmp_path: Path,
) -> None:
    settings = settings_at(tmp_path)
    settings.setValue(f"garmin/autoSyncOnOpen/{FIRST}", "sì, grazie")

    assert QSettingsSyncPreferences(settings).auto_sync(FIRST)
