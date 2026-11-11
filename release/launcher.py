# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Entry point of the packaged app (PyInstaller runs a script, not a module)."""

from ponos_coach.__main__ import main

raise SystemExit(main())
