# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Menu commands run their command with no argument.

A `QAction` passes its "checked" flag to a slot that accepts one: `show_new_profile(form=None)`
received `False` as the form and "File > New profile..." failed with an AttributeError as soon as a
profile was open (2026-11-09). Made-up profiles (no real person).
"""

from __future__ import annotations

from pathlib import Path

from pytestqt.qtbot import QtBot

from tests.ui.test_profile_data_window import window_with_activities
from tests.ui.workspace_support import create_profile, make_settings


def test_file_new_profile_from_the_menu_creates_a_second_profile(
    qtbot: QtBot, tmp_path: Path
) -> None:
    window = window_with_activities(qtbot, tmp_path, make_settings(tmp_path))

    window.new_profile_action.trigger()  # what the menu and Ctrl+N do
    create_profile(qtbot, window, "Second profile")

    assert window.controller.profile is not None
    assert window.controller.profile.name.value == "Second profile"
