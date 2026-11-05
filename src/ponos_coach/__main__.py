# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Composition Root: the only module allowed to import every package."""

import sys


def main() -> int:
    """Start the application and return the process exit code."""
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
