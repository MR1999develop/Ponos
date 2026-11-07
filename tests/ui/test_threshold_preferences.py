# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""The threshold test protocol remembered between sessions."""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import QSettings

from ponos_coach.assessment.domain import DEFAULT_PROTOCOL, ThresholdProtocol
from ponos_coach.ui.threshold_preferences import ProtocolPreference
from tests.ui.workspace_support import make_settings


@pytest.fixture
def settings(tmp_path: Path) -> QSettings:
    return make_settings(tmp_path)


def test_without_a_choice_the_default_protocol_is_used(settings: QSettings) -> None:
    assert ProtocolPreference(settings).load() is DEFAULT_PROTOCOL


@pytest.mark.parametrize("protocol", list(ThresholdProtocol))
def test_the_last_choice_comes_back_in_the_next_session(
    settings: QSettings, protocol: ThresholdProtocol
) -> None:
    ProtocolPreference(settings).save(protocol)

    assert ProtocolPreference(settings).load() is protocol


@pytest.mark.parametrize("stored", ["", "forty_minutes", 30, True])
def test_a_value_that_is_not_understood_gives_the_default_never_an_error(
    settings: QSettings, stored: object
) -> None:
    settings.setValue("threshold/protocol", stored)

    assert ProtocolPreference(settings).load() is DEFAULT_PROTOCOL
