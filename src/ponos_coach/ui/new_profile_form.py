# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Modulo "Nuovo profilo": stato e validazione, testabili senza finestra.

Il modello tiene i valori, propone la cartella dal nome, stima la FC di soglia dalla massima
finché l'utente non la fissa, e dice quali campi hanno un problema (categorie, non testi: i
messaggi li sceglie la vista). Dà una `NewProfileRequest` solo se tutto è valido.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date
from enum import Enum
from pathlib import Path

from ponos_coach.load.domain import HeartRateLimits, Physiology, Sex, ThresholdHeartRate
from ponos_coach.profiles.domain import (
    NAME_MAX_CHARS,
    FolderState,
    InvalidProfileNameError,
    ProfileName,
)
from ponos_coach.profiles.use_cases import NewProfileRequest

DEFAULT_MIN_BPM = 50
DEFAULT_MAX_BPM = 190
THRESHOLD_SHARE_OF_MAX = 0.9  # soglia stimata: 90% della FC massima

# Intervalli dei controlli; il dominio li impone (un test verifica che coincidano).
MIN_ALLOWED_BPM = 20
MAX_ALLOWED_BPM = 250
MIN_THRESHOLD_BPM = 40

_FORBIDDEN_IN_FOLDER_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
# Nomi che Windows riserva ai dispositivi, anche con un'estensione (CON.txt); COM e LPT con le
# cifre in apice sono riservati allo stesso modo.
_RESERVED_DEVICE_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"{prefix}{digit}" for prefix in ("COM", "LPT") for digit in "123456789¹²³"}
)
RESERVED_NAME_SUFFIX = "_profile"


class Field(Enum):
    """Campo del modulo."""

    NAME = "name"
    FOLDER = "folder"
    MIN_BPM = "min_bpm"
    MAX_BPM = "max_bpm"
    THRESHOLD = "threshold"


class Problem(Enum):
    """Che cosa non va in un campo."""

    NAME_EMPTY = (Field.NAME, "name_empty")
    NAME_TOO_LONG = (Field.NAME, "name_too_long")
    NAME_INVALID = (Field.NAME, "name_invalid")
    FOLDER_EMPTY = (Field.FOLDER, "folder_empty")
    FOLDER_NOT_ABSOLUTE = (Field.FOLDER, "folder_not_absolute")
    FOLDER_NOT_EMPTY = (Field.FOLDER, "folder_not_empty")
    MIN_NOT_BELOW_MAX = (Field.MIN_BPM, "min_not_below_max")
    THRESHOLD_NOT_ABOVE_MIN = (Field.THRESHOLD, "threshold_not_above_min")
    THRESHOLD_ABOVE_MAX = (Field.THRESHOLD, "threshold_above_max")

    @property
    def field(self) -> Field:
        """Il campo a cui appartiene il problema."""
        return self.value[0]


def folder_name_from(profile_name: str) -> str:
    """Nome di cartella proposto da un nome di profilo.

    I caratteri vietati diventano `_`; un nome che Windows riserva a un dispositivo (CON, PRN,
    AUX, NUL, COM1-9, LPT1-9, anche con un'estensione) prende il suffisso `_profile` prima
    dell'estensione.
    """
    cleaned = _FORBIDDEN_IN_FOLDER_NAME.sub("_", unicodedata.normalize("NFC", profile_name))
    cleaned = cleaned.strip().rstrip(". ")
    stem, dot, extension = cleaned.partition(".")
    if stem.rstrip(" ").upper() in _RESERVED_DEVICE_NAMES:
        return f"{stem}{RESERVED_NAME_SUFFIX}{dot}{extension}"
    return cleaned


def estimated_threshold(max_bpm: int) -> int:
    """FC di soglia stimata dalla massima (90%), portata nei limiti ammessi."""
    return max(MIN_THRESHOLD_BPM, min(MAX_ALLOWED_BPM, round(max_bpm * THRESHOLD_SHARE_OF_MAX)))


class NewProfileForm:
    """Valori del modulo e regole. Non conosce widget né testi."""

    def __init__(self, default_parent_folder: Path, timezone_name: str) -> None:
        self._parent_folder = default_parent_folder
        self.timezone_name = timezone_name
        self.name = ""
        self.min_bpm = DEFAULT_MIN_BPM
        self.max_bpm = DEFAULT_MAX_BPM
        self.sex = Sex.MALE
        self._folder_text: str | None = None  # None: segue il nome
        self._threshold: int | None = None  # None: stimata dalla massima
        self.folder_state: FolderState | None = None  # ultimo esame noto della cartella

    # --- Cartella e soglia ------------------------------------------------------------

    @property
    def folder_text(self) -> str:
        """Cartella scelta, o quella proposta dal nome finché l'utente non la sceglie."""
        if self._folder_text is not None:
            return self._folder_text
        proposed = folder_name_from(self.name)
        return str(self._parent_folder / proposed) if proposed else ""

    @property
    def folder_is_proposed(self) -> bool:
        """Vero finché la cartella segue il nome."""
        return self._folder_text is None

    def choose_folder(self, text: str) -> None:
        """L'utente sceglie la cartella: non segue più il nome. Testo vuoto: torna alla proposta."""
        self._folder_text = text if text.strip() else None
        self.folder_state = None

    @property
    def threshold_bpm(self) -> int:
        """FC di soglia: quella fissata, o la stima dalla massima."""
        return estimated_threshold(self.max_bpm) if self._threshold is None else self._threshold

    @property
    def threshold_is_estimate(self) -> bool:
        """Vero finché la soglia è una stima da confermare con il test."""
        return self._threshold is None

    def set_threshold(self, bpm: int) -> None:
        """L'utente fissa la soglia: non è più una stima."""
        self._threshold = bpm

    # --- Validazione ------------------------------------------------------------------

    def problems(self) -> tuple[Problem, ...]:
        """Problemi dei valori attuali, nell'ordine dei campi (vuoto se tutto è valido)."""
        found = [*self._name_problems(), *self._folder_problems(), *self._heart_rate_problems()]
        return tuple(found)

    def _name_problems(self) -> list[Problem]:
        text = unicodedata.normalize("NFC", self.name).strip()
        if not text:
            return [Problem.NAME_EMPTY]
        if len(text) > NAME_MAX_CHARS:
            return [Problem.NAME_TOO_LONG]
        try:
            ProfileName.parse(self.name)
        except InvalidProfileNameError:
            return [Problem.NAME_INVALID]
        return []

    def _folder_problems(self) -> list[Problem]:
        text = self.folder_text.strip()
        if not text:
            return [Problem.FOLDER_EMPTY]
        try:
            absolute = Path(text).is_absolute()
        except (ValueError, OSError):
            absolute = False
        if not absolute:
            return [Problem.FOLDER_NOT_ABSOLUTE]
        if self.folder_state is FolderState.NOT_EMPTY:
            return [Problem.FOLDER_NOT_EMPTY]
        return []

    def _heart_rate_problems(self) -> list[Problem]:
        found: list[Problem] = []
        if self.min_bpm >= self.max_bpm:
            found.append(Problem.MIN_NOT_BELOW_MAX)
        if self.threshold_bpm <= self.min_bpm:
            found.append(Problem.THRESHOLD_NOT_ABOVE_MIN)
        if self.threshold_bpm > self.max_bpm:
            found.append(Problem.THRESHOLD_ABOVE_MAX)
        return found

    def build_request(self, today: date) -> NewProfileRequest | tuple[Problem, ...]:
        """La richiesta di creazione, oppure i problemi che la impediscono."""
        found = self.problems()
        if found:
            return found
        physiology = Physiology(
            HeartRateLimits(self.min_bpm, self.max_bpm),
            ThresholdHeartRate(self.threshold_bpm),
            self.sex,
        )
        return NewProfileRequest(
            ProfileName.parse(self.name),
            Path(self.folder_text.strip()),
            self.timezone_name,
            physiology,
            today,
        )
