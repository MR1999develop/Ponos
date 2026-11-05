# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Un solo punto d'ingresso per decodificare un file di attività (FIT o GPX).

Il formato si riconosce dal contenuto (la firma `.FIT` dell'intestazione, l'elemento radice
`gpx`) e deve coincidere con l'estensione del nome: un `.gpx` che contiene un FIT, o il
contrario, non si decodifica mai con l'altro lettore. Nessun I/O. L'errore è uno solo,
`ActivityFileError`, con un motivo enumerato; il comportamento dei due lettori non cambia.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from ponos_coach.activities.domain import Activity, FileFormat
from ponos_coach.activities.fit_decoder import (
    FitDecodeError,
    FitLimits,
    decode_activity,
)
from ponos_coach.activities.gpx_decoder import GpxLimits, decode_gpx, read_xml_root

_FIT_SIGNATURE = b".FIT"
_FIT_SIGNATURE_OFFSET = 8
_FIT_HEADER_SIZES = (12, 14)
_GPX_ROOT = "gpx"
_FORMAT_BY_EXTENSION = {"fit": FileFormat.FIT, "gpx": FileFormat.GPX}


class ActivityFileReason(Enum):
    """Motivo per cui un file non diventa un'attività. Il valore è stabile."""

    EMPTY = "empty"
    TOO_LARGE = "too_large"
    CORRUPT = "corrupt"
    NOT_AN_ACTIVITY = "not_an_activity"
    UNSUPPORTED_FORMAT = "unsupported_format"
    EXTENSION_MISMATCH = "extension_mismatch"
    TOO_MANY_RECORDS = "too_many_records"
    TOO_LONG = "too_long"


class ActivityFileError(Exception):
    """Il file non è un'attività valida entro i limiti; `reason` dice perché."""

    def __init__(self, reason: ActivityFileReason) -> None:
        super().__init__(reason.value)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class ActivityFileLimits:
    """Tetti dei due lettori (default del progetto: 50 MB, un milione di record o punti)."""

    fit: FitLimits = field(default_factory=FitLimits)
    gpx: GpxLimits = field(default_factory=GpxLimits)


class DetectedContent(Enum):
    """Che cosa sembra il contenuto di un file, a prescindere dal nome."""

    FIT = "fit"
    GPX = "gpx"
    OTHER_XML = "other_xml"  # per esempio TCX: riconosciuto ma non supportato
    UNKNOWN = "unknown"


def detect_content(data: bytes) -> DetectedContent:
    """Riconosce il formato dal contenuto: firma FIT, radice XML `gpx`, altro XML, ignoto.

    Guarda solo l'inizio del file; non lo decodifica né ne verifica la validità.
    """
    start = _FIT_SIGNATURE_OFFSET
    if (
        len(data) >= start + len(_FIT_SIGNATURE)
        and data[0] in _FIT_HEADER_SIZES
        and data[start : start + len(_FIT_SIGNATURE)] == _FIT_SIGNATURE
    ):
        return DetectedContent.FIT
    root = read_xml_root(data)
    if root is None:
        return DetectedContent.UNKNOWN
    return DetectedContent.GPX if root.name == _GPX_ROOT else DetectedContent.OTHER_XML


def _extension(file_name: str) -> str:
    """Estensione minuscola, senza punto, dopo l'ultimo punto del nome (vuota se manca)."""
    _, dot, extension = file_name.rpartition(".")
    return extension.lower() if dot else ""


def _expected_format(file_name: str) -> FileFormat:
    expected = _FORMAT_BY_EXTENSION.get(_extension(file_name))
    if expected is None:
        raise ActivityFileError(ActivityFileReason.UNSUPPORTED_FORMAT)
    return expected


def _check_content(data: bytes, expected: FileFormat) -> None:
    detected = detect_content(data)
    if detected is DetectedContent.OTHER_XML:
        raise ActivityFileError(ActivityFileReason.UNSUPPORTED_FORMAT)
    if detected is DetectedContent.UNKNOWN:
        raise ActivityFileError(ActivityFileReason.CORRUPT)
    if detected.value != expected.value:
        raise ActivityFileError(ActivityFileReason.EXTENSION_MISMATCH)


def decode_activity_file(
    data: bytes, file_name: str, limits: ActivityFileLimits | None = None
) -> Activity:
    """Decodifica un file di attività FIT o GPX.

    Di `file_name` conta solo l'estensione (`.fit` o `.gpx`, senza distinguere maiuscole):
    mai un percorso. L'ordine dei controlli è: vuoto, estensione, contenuto riconosciuto e
    coerente con l'estensione, poi il lettore del formato con i suoi limiti.

    Raises:
        ActivityFileError: l'unico errore in uscita. EMPTY; UNSUPPORTED_FORMAT per estensione
            diversa da `.fit` e `.gpx` o per un XML che non è GPX (per esempio TCX); CORRUPT se
            il contenuto non è né FIT né XML ammesso; EXTENSION_MISMATCH se contenuto ed
            estensione sono formati diversi; poi i motivi dei lettori (TOO_LARGE, CORRUPT,
            NOT_AN_ACTIVITY, TOO_MANY_RECORDS, TOO_LONG).
    """
    limits = limits or ActivityFileLimits()
    if not data:
        raise ActivityFileError(ActivityFileReason.EMPTY)
    expected = _expected_format(file_name)
    _check_content(data, expected)
    try:
        if expected is FileFormat.FIT:
            return decode_activity(data, limits.fit)
        return decode_gpx(data, limits.gpx)
    except FitDecodeError as exc:
        raise ActivityFileError(ActivityFileReason(exc.reason.value)) from exc
