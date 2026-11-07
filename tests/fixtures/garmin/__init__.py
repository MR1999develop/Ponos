# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Synthetic JSON responses of the Garmin activity list (see README.md)."""

from __future__ import annotations

import json
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent


def load_json(name: str) -> object:
    """The JSON of fixture `name` (with the `NaN` and `Infinity` literals, as `requests`
    reads them)."""
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))
