# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Tipi di dominio dei profili: id, nome, tipo di cartella, voce del registro, informazioni."""

from __future__ import annotations

import unicodedata
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

NAME_MAX_CHARS = 60
_NAME_MAX_INPUT_CHARS = 1000  # tetto prima di normalizzare: la lunghezza si controlla subito
_UUID_VERSION = 4
_TIMEZONE_MAX_CHARS = 64


class InvalidProfileNameError(ValueError):
    """Il testo non è un nome di profilo valido."""


@dataclass(frozen=True, slots=True)
class ProfileId:
    """Identificativo di un profilo: un UUID casuale (versione 4).

    Si genera fuori dal dominio (`ProfileId(uuid.uuid4())`) o si legge da testo con `parse`.
    """

    value: uuid.UUID

    def __post_init__(self) -> None:
        if self.value.version != _UUID_VERSION or self.value.variant != uuid.RFC_4122:
            raise ValueError("l'id del profilo deve essere un UUID versione 4")

    @classmethod
    def parse(cls, text: str) -> ProfileId:
        """Id da testo nella forma canonica (36 caratteri, minuscole, con trattini).

        Raises:
            ValueError: testo di un'altra forma o UUID di un'altra versione.
        """
        try:
            parsed = uuid.UUID(text)
        except ValueError as exc:
            raise ValueError("id del profilo non valido") from exc
        if str(parsed) != text:
            raise ValueError("id del profilo non in forma canonica")
        return cls(parsed)

    def __str__(self) -> str:
        return str(self.value)


@dataclass(frozen=True, slots=True)
class ProfileName:
    """Nome di un profilo: 1-60 caratteri, Unicode NFC, senza spazi esterni né caratteri di
    controllo o di formato.

    Il costruttore accetta solo testo già normalizzato; `parse` normalizza il testo
    dell'utente (NFC, spazi esterni tolti) e poi lo valida.
    """

    value: str

    def __post_init__(self) -> None:
        if (
            unicodedata.normalize("NFC", self.value) != self.value
            or self.value != self.value.strip()
        ):
            raise InvalidProfileNameError("il nome non è normalizzato (NFC, senza spazi esterni)")
        if not 1 <= len(self.value) <= NAME_MAX_CHARS:
            raise InvalidProfileNameError(f"il nome deve avere da 1 a {NAME_MAX_CHARS} caratteri")
        if any(unicodedata.category(char).startswith(("C", "Zl", "Zp")) for char in self.value):
            raise InvalidProfileNameError("il nome contiene caratteri di controllo")

    @classmethod
    def parse(cls, text: str) -> ProfileName:
        """Nome dal testo inserito dall'utente.

        Raises:
            InvalidProfileNameError: vuoto dopo aver tolto gli spazi esterni, più lungo di 60
                caratteri (dopo NFC) o con caratteri di controllo.
        """
        if len(text) > _NAME_MAX_INPUT_CHARS:
            raise InvalidProfileNameError("il nome è troppo lungo")
        return cls(unicodedata.normalize("NFC", text).strip())

    def __str__(self) -> str:
        return self.value


class FolderKind(Enum):
    """Dove sta una cartella: decide se avvisare l'utente (rete e sincronizzazione sono rischi
    per SQLite)."""

    LOCAL = "local"
    NETWORK = "network"
    CLOUD_SYNCED = "cloud_synced"
    UNKNOWN = "unknown"


class FolderState(Enum):
    """Stato di una cartella scelta per un profilo."""

    MISSING = "missing"
    EMPTY = "empty"
    NOT_EMPTY = "not_empty"


@dataclass(frozen=True, slots=True)
class FolderInspection:
    """Esito dell'ispezione di una cartella: stato, tipo (per una cartella mancante, il tipo
    del posto in cui verrebbe creata) e se contiene il database di un profilo."""

    state: FolderState
    kind: FolderKind
    has_profile_database: bool


@dataclass(frozen=True, slots=True)
class RegistryEntry:
    """Voce dell'elenco dei profili nella configurazione dell'app.

    `folder` è assoluta; `cached_name` è l'ultimo nome noto (la fonte di verità è il database
    del profilo); `last_used_utc` è l'ultimo uso, in UTC.
    """

    profile_id: ProfileId
    folder: Path
    cached_name: ProfileName
    last_used_utc: datetime

    def __post_init__(self) -> None:
        if not self.folder.is_absolute():
            raise ValueError("la cartella del profilo deve essere un percorso assoluto")
        if self.last_used_utc.utcoffset() != timedelta(0):
            raise ValueError("l'ultimo uso deve essere un istante UTC con fuso")


@dataclass(frozen=True, slots=True)
class ProfileInfo:
    """Ciò che il database dichiara del profilo: id, nome, fuso orario e versione del formato.

    `timezone_name` è un nome IANA noto (es. `Europe/Rome`); `format_version` è la versione
    dello schema, 1 o più.
    """

    profile_id: ProfileId
    name: ProfileName
    timezone_name: str
    format_version: int

    def __post_init__(self) -> None:
        if not 0 < len(self.timezone_name) <= _TIMEZONE_MAX_CHARS:
            raise ValueError("nome del fuso orario vuoto o troppo lungo")
        try:
            ZoneInfo(self.timezone_name)  # ricerca nei dati di `tzdata`, senza rete
        except (ZoneInfoNotFoundError, ValueError, OSError) as exc:
            raise ValueError("fuso orario sconosciuto") from exc
        if self.format_version < 1:
            raise ValueError("la versione del formato parte da 1")
