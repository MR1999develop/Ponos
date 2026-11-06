# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Verifica dei nomi dei fusi orari IANA: l'unico punto che legge i dati dei fusi.

Sta fuori dal dominio puro perché la ricerca (`ZoneInfo`) legge i file di `tzdata` dal disco.
La usano il caso d'uso che crea un profilo e l'adattatore del database.
"""

from __future__ import annotations

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

_TIMEZONE_MAX_CHARS = 64


def require_known_timezone(name: str) -> None:
    """Controlla che `name` sia un fuso IANA noto (ricerca nei dati di `tzdata`, senza rete).

    Raises:
        ValueError: nome vuoto, più lungo di 64 caratteri, non valido o sconosciuto.
    """
    if not 0 < len(name) <= _TIMEZONE_MAX_CHARS:
        raise ValueError("nome del fuso orario vuoto o troppo lungo")
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, OSError) as exc:
        raise ValueError("fuso orario sconosciuto") from exc
