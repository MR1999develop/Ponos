# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Struttura anonima di una risposta JSON, per scrivere fixture realistiche.

`shape_of(value)` restituisce una descrizione che contiene solo: nomi delle
chiavi, tipo JSON di ogni valore, lunghezza di stringhe e array, ordine di
grandezza dei numeri. Nessun valore originale. Funzione pura: niente I/O.
"""

from __future__ import annotations

import math
import re
from typing import Any

MAX_DEPTH = 12
MAX_KEYS = 300
MAX_KEY_LENGTH = 48

# Una chiave "dinamica" (email, id, data, esadecimale lungo) puo' contenere dati reali.
_EMAIL_KEY = re.compile(r"@")
_DIGITS_KEY = re.compile(r"\d{4,}")
_HEX_KEY = re.compile(r"[0-9a-fA-F-]{16,}")


def _key_placeholder(key: str) -> str | None:
    """Segnaposto per una chiave che potrebbe contenere un dato reale, o None."""
    if _EMAIL_KEY.search(key):
        return "email-key"
    if _DIGITS_KEY.search(key) or _HEX_KEY.fullmatch(key):
        return "id-key"
    if len(key) > MAX_KEY_LENGTH:
        return "long-key"
    return None


def _magnitude(number: float) -> str:
    """Ordine di grandezza (`10^k`) del valore assoluto; `0` per lo zero."""
    if number == 0:
        return "0"
    if not math.isfinite(number):
        return "non-finite"
    return f"10^{math.floor(math.log10(abs(number)))}"


def _shape_of_object(value: dict[Any, Any], depth: int) -> dict[str, Any]:
    keys: dict[str, Any] = {}
    counters: dict[str, int] = {}
    for position, (key, item) in enumerate(value.items()):
        if position >= MAX_KEYS:
            keys["<altre-chiavi-omesse>"] = {"count": len(value) - MAX_KEYS}
            break
        text = str(key)
        placeholder = _key_placeholder(text)
        if placeholder is not None:
            counters[placeholder] = counters.get(placeholder, 0) + 1
            text = f"<{placeholder}-{counters[placeholder]}>"
        keys[text] = shape_of(item, depth + 1)
    return {"type": "object", "keys": keys}


def _shape_of_scalar(value: object) -> dict[str, Any] | None:
    """Forma di null, booleani, numeri e stringhe; None se non e' uno scalare."""
    if value is None:
        return {"type": "null"}
    if isinstance(value, bool):
        return {"type": "boolean"}
    if isinstance(value, (int, float)):
        kind = "integer" if isinstance(value, int) else "number"
        shape: dict[str, Any] = {"type": kind, "magnitude": _magnitude(value)}
        if value < 0:
            shape["negative"] = True
        return shape
    if isinstance(value, str):
        return {"type": "string", "length": len(value)}
    return None


def shape_of(value: object, depth: int = 0) -> dict[str, Any]:
    """Descrive la struttura di `value` senza mai riportarne i valori.

    Oggetto: `{"type": "object", "keys": {nome: forma}}`; array: tipo,
    lunghezza e forma del primo elemento; stringa: lunghezza; numero: ordine
    di grandezza (e `negative` se negativo); chiavi dinamiche sostituite da
    segnaposto; oltre `MAX_DEPTH` livelli `{"type": "truncated"}`.
    """
    if depth > MAX_DEPTH:
        return {"type": "truncated"}
    scalar = _shape_of_scalar(value)
    if scalar is not None:
        return scalar
    if isinstance(value, dict):
        return _shape_of_object(value, depth)
    if isinstance(value, (list, tuple)):
        first = shape_of(value[0], depth + 1) if value else None
        return {"type": "array", "length": len(value), "first": first}
    return {"type": f"other:{type(value).__name__}"}
