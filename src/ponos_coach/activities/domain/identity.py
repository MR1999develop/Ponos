# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Formato del file e identità di un'attività (PLAN.md sezione 4.3).

L'identità è un'unione di due casi: un FIT si riconosce dal suo `file_id`, un GPX (che non ne
ha uno) dallo SHA-256 dei suoi byte. `identity_key` e `identity_from_key` sono l'unica
rappresentazione testuale stabile, per i dati salvati.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import Enum

_UINT16_MAX = 0xFFFF
_UINT32_MAX = 0xFFFFFFFF
_SHA256_HEX = re.compile(r"[0-9a-f]{64}")
_FIT_KEY_PREFIX = "fit:"
_GPX_KEY_PREFIX = "gpx:"
_FIT_KEY_PARTS = 4  # produttore, prodotto, numero di serie, ora di creazione


class FileFormat(Enum):
    """Formato del file originale di un'attività. Il valore è l'estensione, senza punto."""

    FIT = "fit"
    GPX = "gpx"


@dataclass(frozen=True, slots=True)
class FileId:
    """Identità di un FIT: il `file_id` (produttore, prodotto, numero di serie, ora).

    Uguale per lo stesso file scaricato da Garmin o copiato dall'orologio. `time_created`
    è in UTC.
    """

    manufacturer: int
    product: int
    serial_number: int
    time_created: datetime

    def __post_init__(self) -> None:
        if not 0 <= self.manufacturer <= _UINT16_MAX or not 0 <= self.product <= _UINT16_MAX:
            raise ValueError("produttore o prodotto fuori da uint16")
        if not 0 <= self.serial_number <= _UINT32_MAX:
            raise ValueError("numero di serie fuori da uint32")
        if self.time_created.utcoffset() != timedelta(0):
            raise ValueError("time_created deve essere un istante UTC con fuso")


@dataclass(frozen=True, slots=True)
class FitIdentity:
    """Identità di un'attività arrivata come FIT."""

    file_id: FileId


@dataclass(frozen=True, slots=True)
class GpxIdentity:
    """Identità di un'attività arrivata come GPX: SHA-256 del file (64 cifre minuscole)."""

    sha256_hex: str

    def __post_init__(self) -> None:
        if not _SHA256_HEX.fullmatch(self.sha256_hex):
            raise ValueError("lo SHA-256 deve essere di 64 cifre esadecimali minuscole")


ActivityIdentity = FitIdentity | GpxIdentity


def format_of(identity: ActivityIdentity) -> FileFormat:
    """Formato del file a cui l'identità appartiene."""
    return FileFormat.FIT if isinstance(identity, FitIdentity) else FileFormat.GPX


def identity_key(identity: ActivityIdentity) -> str:
    """Chiave testuale univoca e stabile dell'identità (per colonne UNIQUE e ordinamenti).

    `fit:<produttore>:<prodotto>:<serie>:<ora ISO 8601 UTC>` oppure `gpx:<sha256>`.
    """
    if isinstance(identity, GpxIdentity):
        return f"{_GPX_KEY_PREFIX}{identity.sha256_hex}"
    f = identity.file_id
    created = f.time_created.astimezone(UTC).isoformat()
    return f"{_FIT_KEY_PREFIX}{f.manufacturer}:{f.product}:{f.serial_number}:{created}"


def identity_from_key(key: str) -> ActivityIdentity:
    """Inverso di `identity_key`.

    Raises:
        ValueError: chiave non prodotta da `identity_key`.
    """
    if key.startswith(_GPX_KEY_PREFIX):
        return GpxIdentity(key.removeprefix(_GPX_KEY_PREFIX))
    if not key.startswith(_FIT_KEY_PREFIX):
        raise ValueError("chiave di identità sconosciuta")
    parts = key.removeprefix(_FIT_KEY_PREFIX).split(":", _FIT_KEY_PARTS - 1)
    if len(parts) != _FIT_KEY_PARTS:
        raise ValueError("chiave di identità FIT incompleta")
    manufacturer, product, serial = (int(part) for part in parts[:3])
    identity = FitIdentity(FileId(manufacturer, product, serial, datetime.fromisoformat(parts[3])))
    if identity_key(identity) != key:
        raise ValueError("chiave di identità non canonica")
    return identity
