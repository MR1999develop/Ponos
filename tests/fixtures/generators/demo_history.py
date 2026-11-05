# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Crea una cartella dimostrativa di file FIT sintetici per provare l'app.

Uso: `uv run --locked python -m tests.fixtures.generators.demo_history <cartella> [--days N]`.
Stesso seme e stessa data di partenza, stessi file: lo storico finisce oggi.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .history import generate_history

DEFAULT_DAYS = 120
MAX_DAYS = 1500  # circa quattro anni: file piccoli, ma la demo resta rapida
SEED = 20261105


def main(argv: Sequence[str] | None = None) -> int:
    """Scrive lo storico e ne stampa il riepilogo su stdout."""
    parser = argparse.ArgumentParser(prog="demo_history", description=__doc__)
    parser.add_argument("folder", type=Path, help="cartella di destinazione (creata se manca)")
    parser.add_argument(
        "--days", type=int, default=DEFAULT_DAYS, help=f"giorni di storico (default {DEFAULT_DAYS})"
    )
    args = parser.parse_args(argv)
    if not 1 <= args.days <= MAX_DAYS:
        parser.error(f"--days deve stare tra 1 e {MAX_DAYS}")
    start = datetime.now(UTC).date() - timedelta(days=args.days - 1)
    paths = generate_history(args.folder, args.days, SEED, start)
    sys.stdout.write(f"{len(paths)} file FIT in {args.folder} (da {start.isoformat()})\n")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
