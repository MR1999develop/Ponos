# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Checks on the translation catalog (Qt Linguist `.ts`), without Qt.

The nox `i18n` session generates a "fresh" catalog from the sources with `pyside6-lupdate` and
compares it with the versioned one: `find_problems` says what is missing, what is obsolete, what is
not
translated and which placeholders (`{name}`, `%n`) do not match between source and translation.

Command-line use: `python -m ponos_coach.i18n.catalog FRESH.ts VERSIONED.ts`
(exit 0 if everything matches, 1 if there are problems, 2 for incorrect usage).
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
    """The file is not a readable `.ts` catalog within the limits."""


class MessageStatus(StrEnum):
    """State of an entry, as Linguist writes it."""

    FINISHED = "finished"
    UNFINISHED = "unfinished"
    OBSOLETE = "obsolete"


@dataclass(frozen=True, slots=True)
class Message:
    """An entry of the catalog; `forms` has a single translation, or one per plural form."""

    context: str
    source: str
    comment: str
    plural: bool
    forms: tuple[str, ...]
    status: MessageStatus

    @property
    def key(self) -> tuple[str, str, str]:
        """Identity of the entry: context, source text and disambiguation comment."""
        return (self.context, self.source, self.comment)


class ProblemKind(StrEnum):
    """What is wrong with an entry."""

    MISSING = "missing"  # in the sources but not in the versioned catalog
    OBSOLETE = "obsolete"  # in the versioned catalog but no longer in the sources
    UNFINISHED = "unfinished"
    EMPTY = "empty"
    PLACEHOLDERS = "placeholders"


@dataclass(frozen=True, slots=True)
class Problem:
    """A catalog problem, referring to an entry."""

    kind: ProblemKind
    context: str
    source: str

    def __str__(self) -> str:
        return f"{self.kind.value}: [{self.context}] {self.source!r}"


def parse_catalog(data: bytes) -> tuple[Message, ...]:
    """Reads a `.ts`. Rejects files that are too large and entity declarations (no expansion)."""
    if len(data) > MAX_CATALOG_BYTES:
        raise CatalogError("catalog too large")
    if b"<!ENTITY" in data:
        raise CatalogError("entity declarations are not allowed")
    try:
        root = ET.fromstring(data)  # noqa: S314 - bounded size and no entities
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
    """Named placeholders (`{name}`) and `%n` present in a text."""
    return frozenset(_PLACEHOLDER.findall(text))


def find_problems(current: Sequence[Message], versioned: Sequence[Message]) -> list[Problem]:
    """Compares the current texts of the sources with the versioned catalog.

    In every plural form `%n` and the named placeholders must appear as in the source.
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
    """Compares two catalogs; problems go to stderr, one per line."""
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
