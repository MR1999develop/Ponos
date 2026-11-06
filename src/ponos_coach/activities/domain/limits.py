# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Tetti di default del progetto per i file di attività e le tracce (una sola definizione)."""

from datetime import UTC, datetime

MAX_ACTIVITY_FILE_BYTES = 50 * 1024 * 1024  # FIT o GPX: oltre, il file non si legge
MAX_TRACK_POINTS = 2000  # punti della traccia semplificata salvata nell'indice
TRACK_TOLERANCE_M = 10.0  # scostamento massimo della traccia semplificata dall'originale

# Finestra degli orari ammessi per un'attività, [inizio, fine): fuori, il file è corrotto. Un
# orologio azzerato (anno 1) o impazzito gonfierebbe la serie del carico a secoli di giorni.
MIN_ACTIVITY_INSTANT = datetime(1990, 1, 1, tzinfo=UTC)
MAX_ACTIVITY_INSTANT = datetime(2100, 1, 1, tzinfo=UTC)
