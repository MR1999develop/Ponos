# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Checks on the `.ts` catalog: missing, obsolete, untranslated entries, mismatched placeholders."""

from __future__ import annotations

from pathlib import Path

import pytest

from ponos_coach.i18n.catalog import (
    MAX_CATALOG_BYTES,
    CatalogError,
    Message,
    MessageStatus,
    Problem,
    ProblemKind,
    find_problems,
    main,
    parse_catalog,
    placeholders_of,
)


def message(
    source: str,
    *forms: str,
    context: str = "Ctx",
    status: MessageStatus = MessageStatus.FINISHED,
    plural: bool = False,
    comment: str = "",
) -> Message:
    return Message(context, source, comment, plural or len(forms) > 1, forms or ("x",), status)


def catalog_xml(*messages: str) -> bytes:
    body = "".join(messages)
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE TS>\n'
        f'<TS version="2.1" language="it" sourcelanguage="en"><context><name>Ctx</name>{body}'
        "</context></TS>"
    ).encode()


def kinds(problems: list[Problem]) -> list[tuple[ProblemKind, str]]:
    return [(p.kind, p.source) for p in problems]


# --- Reading ----------------------------------------------------------------------------


def test_parse_reads_plain_unfinished_obsolete_and_plural_messages() -> None:
    data = catalog_xml(
        "<message><source>Open</source><translation>Apri</translation></message>",
        '<message><source>Save</source><translation type="unfinished"></translation></message>',
        '<message><source>Old</source><translation type="vanished">Vecchio</translation></message>',
        '<message numerus="yes"><source>%n file(s)</source><translation>'
        "<numerusform>%n file</numerusform><numerusform>%n file</numerusform>"
        "</translation></message>",
        "<message><source>Cut</source><comment>verb</comment>"
        "<translation>Taglia</translation></message>",
    )

    opened, saved, old, files, cut = parse_catalog(data)

    assert (opened.context, opened.source, opened.forms, opened.status) == (
        "Ctx",
        "Open",
        ("Apri",),
        MessageStatus.FINISHED,
    )
    assert saved.status is MessageStatus.UNFINISHED
    assert old.status is MessageStatus.OBSOLETE
    assert files.plural
    assert files.forms == ("%n file", "%n file")
    assert cut.key == ("Ctx", "Cut", "verb")


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"<TS><context>",
        b"<other/>",
        b'<!DOCTYPE TS [<!ENTITY x "y">]><TS version="2.1"></TS>',
        b"x" * (MAX_CATALOG_BYTES + 1),
    ],
)
def test_parse_rejects_hostile_or_malformed_input(data: bytes) -> None:
    with pytest.raises(CatalogError):
        parse_catalog(data)


def test_external_entities_are_never_resolved(tmp_path: Path) -> None:
    secret = tmp_path / "secret.txt"
    secret.write_text("TOP-SECRET", encoding="utf-8")
    data = (
        f'<!DOCTYPE TS [<!ENTITY leak SYSTEM "{secret.as_uri()}">]>'
        "<TS><context><name>Ctx</name><message><source>&leak;</source></message></context></TS>"
    ).encode()

    with pytest.raises(CatalogError):
        parse_catalog(data)


# --- Placeholders -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Plain text", set()),
        ("Version {version}", {"{version}"}),
        ("Read {done} of %n file(s)", {"{done}", "%n"}),
        ("{a} and {a}", {"{a}"}),
        ("100% sure", set()),
    ],
)
def test_placeholders_are_named_braces_and_percent_n(text: str, expected: set[str]) -> None:
    assert placeholders_of(text) == expected


# --- Comparison --------------------------------------------------------------------------


def test_a_complete_catalog_has_no_problems() -> None:
    current = [
        message("Open", "Apri"),
        message("Read {done} of %n file(s)", "%n {done}", "%n {done}"),
    ]

    assert find_problems(current, current) == []


def test_missing_and_obsolete_entries_are_reported() -> None:
    current = [message("Open", "Apri"), message("New text", "Nuovo")]
    versioned = [
        message("Open", "Apri"),
        message("Gone", "Via"),
        message("Marked", "Segnata", status=MessageStatus.OBSOLETE),
    ]

    problems = find_problems(current, versioned)

    assert kinds(problems) == [
        (ProblemKind.MISSING, "New text"),
        (ProblemKind.OBSOLETE, "Gone"),
        (ProblemKind.OBSOLETE, "Marked"),
    ]


def test_same_text_in_another_context_or_comment_is_a_different_entry() -> None:
    current = [message("Cut", "Taglia", comment="verb"), message("Cut", "Tagliato", context="B")]
    versioned = [message("Cut", "Taglia")]

    assert kinds(find_problems(current, versioned)) == [
        (ProblemKind.MISSING, "Cut"),
        (ProblemKind.MISSING, "Cut"),
        (ProblemKind.OBSOLETE, "Cut"),
    ]


def test_unfinished_and_empty_translations_are_reported_once_each() -> None:
    entries = [
        message("A", "", status=MessageStatus.UNFINISHED),
        message("B", ""),
        message("C", "   "),
        message("D %n", "%n uno", "", plural=True),
    ]

    problems = find_problems(entries, entries)

    assert kinds(problems) == [
        (ProblemKind.UNFINISHED, "A"),
        (ProblemKind.EMPTY, "B"),
        (ProblemKind.EMPTY, "C"),
        (ProblemKind.EMPTY, "D %n"),
    ]


@pytest.mark.parametrize(
    ("source", "forms"),
    [
        ("Version {version}", ("Versione {versione}",)),  # name translated by mistake
        ("Version {version}", ("Versione",)),  # lost placeholder
        ("Plain", ("Semplice {extra}",)),  # invented placeholder
        ("%n file(s)", ("un file", "%n file")),  # %n lost in a plural form
        ("{n} of %n", ("{n} su",)),
    ],
)
def test_placeholder_mismatch_is_reported(source: str, forms: tuple[str, ...]) -> None:
    entries = [message(source, *forms)]

    assert kinds(find_problems(entries, entries)) == [(ProblemKind.PLACEHOLDERS, source)]


def test_problem_is_formatted_with_kind_context_and_source() -> None:
    assert str(Problem(ProblemKind.MISSING, "Main", "Quit")) == "missing: [Main] 'Quit'"


# --- Command line --------------------------------------------------------------------


def write_catalog(path: Path, *messages: str) -> Path:
    path.write_bytes(catalog_xml(*messages))
    return path


GOOD = "<message><source>Open</source><translation>Apri</translation></message>"
UNTRANSLATED = (
    '<message><source>Open</source><translation type="unfinished"></translation></message>'
)


def test_main_returns_0_for_matching_catalogs(tmp_path: Path) -> None:
    fresh = write_catalog(tmp_path / "fresh.ts", GOOD)
    versioned = write_catalog(tmp_path / "ponos_it.ts", GOOD)

    assert main([str(fresh), str(versioned)]) == 0


def test_main_returns_1_and_reports_each_problem_on_stderr(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    fresh = write_catalog(tmp_path / "fresh.ts", GOOD)
    versioned = write_catalog(tmp_path / "ponos_it.ts", UNTRANSLATED)

    assert main([str(fresh), str(versioned)]) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "catalog: ponos_it.ts: unfinished: [Ctx] 'Open'" in captured.err


def test_main_returns_1_for_an_unreadable_file_and_2_for_wrong_usage(tmp_path: Path) -> None:
    fresh = write_catalog(tmp_path / "fresh.ts", GOOD)

    assert main([str(fresh), str(tmp_path / "missing.ts")]) == 1
    assert main([str(fresh)]) == 2
