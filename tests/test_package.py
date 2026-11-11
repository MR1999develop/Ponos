# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Skeleton test: package importable and entry point present."""

import importlib
import importlib.metadata
import re

import ponos_coach


def test_package_exposes_semantic_version() -> None:
    assert re.fullmatch(r"\d+\.\d+\.\d+", ponos_coach.__version__)


def test_version_matches_installed_metadata() -> None:
    assert importlib.metadata.version("ponos-coach") == ponos_coach.__version__


def test_gui_script_entry_point_resolves_to_main() -> None:
    entry_points = importlib.metadata.entry_points(group="gui_scripts")
    (entry,) = [e for e in entry_points if e.name == "ponos-coach"]
    assert entry.value == "ponos_coach.__main__:main"
    module_name, _, function_name = entry.value.partition(":")
    assert callable(getattr(importlib.import_module(module_name), function_name))
