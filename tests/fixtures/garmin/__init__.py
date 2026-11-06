# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Risposte JSON sintetiche dell'elenco delle attività di Garmin (vedi README.md)."""

from __future__ import annotations

import json
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent


def load_json(name: str) -> object:
    """Il JSON della fixture `name` (con i letterali `NaN` e `Infinity`, come li legge
    `requests`)."""
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))
