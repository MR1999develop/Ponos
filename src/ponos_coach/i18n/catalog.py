# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Controlli sul catalogo di traduzione (`.ts` di Qt Linguist), senza Qt.

La sessione `i18n` di nox genera con `pyside6-lupdate` un catalogo "fresco" dai sorgenti e lo
confronta con quello versionato: `find_problems` dice cosa manca, cosa e obsoleto, cosa non e
tradotto e quali segnaposto (`{nome}`, `%n`) non coincidono tra sorgente e traduzione.

Uso da riga di comando: `python -m ponos_coach.i18n.catalog FRESCO.ts VERSIONATO.ts`
(uscita 0 se tutto coincide, 1 se ci sono problemi, 2 per un uso scorretto).
"""

from __future__ import annotations

import argparse
import re
import sys
import xml.etree.ElementTree as ET
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

MAX_CATALOG_BYTES = 8 * 1024 * 1024
_PLACEHOLDER = re.compile(r"\{\w*\}|%n")


class CatalogError(ValueError):
    """Il file non e un catalogo `.ts` leggibile entro i limiti."""


class MessageStatus(StrEnum):
    """Stato di una voce, come lo scrive Linguist."""

    FINISHED = "finished"
    UNFINISHED = "unfinished"
    OBSOLETE = "obsolete"


@dataclass(frozen=True, slots=True)
class Message:
    """Una voce del catalogo; `forms` ha una sola traduzione, o una per forma plurale."""

    context: str
    source: str
    comment: str
    plural: bool
    forms: tuple[str, ...]
    status: MessageStatus

    @property
    def key(self) -> tuple[str, str, str]:
        """Identita della voce: contesto, testo sorgente e commento di disambiguazione."""
        return (self.context, self.source, self.comment)


class ProblemKind(StrEnum):
    """Che cosa non va in una voce."""

    MISSING = "missing"  # nei sorgenti ma non nel catalogo versionato
    OBSOLETE = "obsolete"  # nel catalogo versionato ma non piu nei sorgenti
    UNFINISHED = "unfinished"
    EMPTY = "empty"
    PLACEHOLDERS = "placeholders"


@dataclass(frozen=True, slots=True)
class Problem:
    """Un problema del catalogo, riferito a una voce."""

    kind: ProblemKind
    context: str
    source: str

    def __str__(self) -> str:
        return f"{self.kind.value}: [{self.context}] {self.source!r}"


def parse_catalog(data: bytes) -> tuple[Message, ...]:
    """Legge un `.ts`. Rifiuta file troppo grandi e dichiarazioni di entita (nessuna espansione)."""
    if len(data) > MAX_CATALOG_BYTES:
        raise CatalogError("catalog too large")
    if b"<!ENTITY" in data:
        raise CatalogError("entity declarations are not allowed")
    try:
        root = ET.fromstring(data)  # noqa: S314 - dimensione limitata e niente entita
    except ET.ParseError as error:
        raise CatalogError("catalog is not well-formed XML") from error
    if root.tag != "TS":
        raise CatalogError("not a Qt Linguist catalog (root element is not TS)")
    return tuple(
        _message_of(context.findtext("name", default=""), element)
        for context in root.iter("context")
        for element in context.iter("message")
    )


def _message_of(context: str, element: ET.Element) -> Message:
    translation = element.find("translation")
    plural = element.get("numerus") == "yes"
    forms: tuple[str, ...] = ("",)
    status = MessageStatus.FINISHED
    if translation is not None:
        kind = translation.get("type", "")
        if kind == "unfinished":
            status = MessageStatus.UNFINISHED
        elif kind in {"obsolete", "vanished"}:
            status = MessageStatus.OBSOLETE
        if plural:
            forms = tuple(form.text or "" for form in translation.iter("numerusform"))
        else:
            forms = (translation.text or "",)
    return Message(
        context,
        element.findtext("source", default=""),
        element.findtext("comment", default=""),
        plural,
        forms,
        status,
    )


def placeholders_of(text: str) -> frozenset[str]:
    """Segnaposto con nome (`{name}`) e `%n` presenti in un testo."""
    return frozenset(_PLACEHOLDER.findall(text))


def find_problems(current: Sequence[Message], versioned: Sequence[Message]) -> list[Problem]:
    """Confronta i testi attuali dei sorgenti con il catalogo versionato.

    In ogni forma plurale `%n` e i segnaposto con nome devono comparire come nel sorgente.
    """
    current_keys = {message.key for message in current}
    live = {m.key: m for m in versioned if m.status is not MessageStatus.OBSOLETE}
    problems = [
        Problem(ProblemKind.MISSING, m.context, m.source) for m in current if m.key not in live
    ]
    problems += [
        Problem(ProblemKind.OBSOLETE, m.context, m.source)
        for m in versioned
        if m.status is MessageStatus.OBSOLETE or m.key not in current_keys
    ]
    for message in live.values():
        if message.key in current_keys:
            problems += _translation_problems(message)
    return problems


def _translation_problems(message: Message) -> list[Problem]:
    def problem(kind: ProblemKind) -> list[Problem]:
        return [Problem(kind, message.context, message.source)]

    if message.status is MessageStatus.UNFINISHED:
        return problem(ProblemKind.UNFINISHED)
    if any(not form.strip() for form in message.forms):
        return problem(ProblemKind.EMPTY)
    expected = placeholders_of(message.source)
    if any(placeholders_of(form) != expected for form in message.forms):
        return problem(ProblemKind.PLACEHOLDERS)
    return []


def _read(path: Path) -> tuple[Message, ...]:
    with path.open("rb") as stream:
        data = stream.read(MAX_CATALOG_BYTES + 1)
    return parse_catalog(data)


def main(argv: Sequence[str] | None = None) -> int:
    """Confronta due cataloghi; i problemi vanno su stderr, uno per riga."""
    parser = argparse.ArgumentParser(prog="python -m ponos_coach.i18n.catalog")
    parser.add_argument("fresh", type=Path, help=".ts generated from the sources")
    parser.add_argument("versioned", type=Path, help=".ts kept in the repository")
    try:
        args = parser.parse_args(sys.argv[1:] if argv is None else list(argv))
    except SystemExit as exit_request:
        return exit_request.code if isinstance(exit_request.code, int) else 2
    try:
        problems = find_problems(_read(args.fresh), _read(args.versioned))
    except (OSError, CatalogError) as error:
        sys.stderr.write(f"catalog: {error}\n")
        return 1
    for problem in problems:
        sys.stderr.write(f"catalog: {args.versioned.name}: {problem}\n")
    if problems:
        sys.stderr.write(f"catalog: {len(problems)} problem(s) found\n")
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
