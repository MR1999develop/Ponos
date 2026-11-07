# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Creates a demonstration folder of synthetic FIT files to try the app.

Usage: `uv run --locked python -m tests.fixtures.generators.demo_history <folder> [--days N]`.
Same seed and same start date, same files: the history ends today. The folder is
loaded into a profile with "Import from folder" (`activities.use_cases.import_activities`).
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .history import generate_history

DEFAULT_DAYS = 120
MAX_DAYS = 1500  # about four years: small files, but the demo stays quick
SEED = 20261105


def main(argv: Sequence[str] | None = None) -> int:
    """Writes the history and prints its summary on stdout."""
    parser = argparse.ArgumentParser(prog="demo_history", description=__doc__)
    parser.add_argument("folder", type=Path, help="destination folder (created if missing)")
    parser.add_argument(
        "--days", type=int, default=DEFAULT_DAYS, help=f"days of history (default {DEFAULT_DAYS})"
    )
    args = parser.parse_args(argv)
    if not 1 <= args.days <= MAX_DAYS:
        parser.error(f"--days must be between 1 and {MAX_DAYS}")
    start = datetime.now(UTC).date() - timedelta(days=args.days - 1)
    paths = generate_history(args.folder, args.days, SEED, start)
    sys.stdout.write(f"{len(paths)} file FIT in {args.folder} (da {start.isoformat()})\n")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
