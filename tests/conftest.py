# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Configurazione comune dei test: profili hypothesis e marker."""

import os

from hypothesis import HealthCheck, settings

# Nessuna scadenza per esempio: i test devono essere deterministici in CI.
settings.register_profile(
    "ci",
    max_examples=100,
    deadline=None,
    derandomize=True,
    suppress_health_check=[HealthCheck.too_slow],
)
settings.register_profile("collaudo", max_examples=5000, deadline=None)
settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "ci"))
