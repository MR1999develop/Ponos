# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Controlli obbligatori: `uv run nox` in locale e in CI (stesso comando)."""

from pathlib import Path

import nox

nox.options.sessions = ["lint", "types", "arch", "tests", "audit", "i18n"]

_SOURCE_DIR = Path("src/ponos_coach")
_CATALOG = _SOURCE_DIR / "i18n" / "ponos_it.ts"  # versionato: lo compila il traduttore
_COMPILED_CATALOG = _CATALOG.with_suffix(".qm")  # generato e ignorato da git
_FRESH_CATALOG = Path(".nox") / "i18n" / "fresh.ts"  # solo i testi attuali dei sorgenti


# Le sessioni usano l'ambiente del progetto, sincronizzato dal lock con
# `uv run --locked` (fallisce se uv.lock non è aggiornato).
@nox.session(python=False)
def lint(session: nox.Session) -> None:
    """Formattazione e lint con ruff."""
    session.run("uv", "run", "--locked", "ruff", "format", "--check", ".", external=True)
    session.run("uv", "run", "--locked", "ruff", "check", ".", external=True)


@nox.session(python=False)
def types(session: nox.Session) -> None:
    """mypy in modalità strict su src, tests e noxfile, per Windows e per Linux."""
    for platform in ("win32", "linux"):
        session.run("uv", "run", "--locked", "mypy", "--platform", platform, external=True)


@nox.session(python=False)
def arch(session: nox.Session) -> None:
    """Contratti di architettura con import-linter."""
    session.run("uv", "run", "--locked", "lint-imports", external=True)


@nox.session(python=False)
def tests(session: nox.Session) -> None:
    """pytest (Qt in modalità offscreen), esclusi i test di collaudo."""
    session.run(
        "uv",
        "run",
        "--locked",
        "pytest",
        *session.posargs,
        env={"QT_QPA_PLATFORM": "offscreen"},
        external=True,
    )


@nox.session(python=False)
def audit(session: nox.Session) -> None:
    """pip-audit sulle dipendenze bloccate nel lock."""
    session.run(
        "uv",
        "export",
        "--locked",
        "--no-dev",
        "--no-emit-project",
        "--format=requirements.txt",
        "--output-file=.nox/audit-requirements.txt",
        "--quiet",
        external=True,
    )
    session.run(
        "uv",
        "run",
        "--locked",
        "pip-audit",
        "--requirement",
        ".nox/audit-requirements.txt",
        "--require-hashes",
        "--disable-pip",
        external=True,
    )


@nox.session(python=False, default=False)
def collaudo(session: nox.Session) -> None:
    """Campagna lunga: test marcati `collaudo` con il profilo hypothesis lungo."""
    session.run(
        "uv",
        "run",
        "--locked",
        "pytest",
        "-m",
        "collaudo",
        "--hypothesis-profile=collaudo",
        *session.posargs,
        env={"QT_QPA_PLATFORM": "offscreen"},
        external=True,
    )


def _translatable_sources() -> list[str]:
    """Sorgenti Python con testi da tradurre (`tr()`, `translate()`), in ordine stabile."""
    return sorted(path.as_posix() for path in _SOURCE_DIR.rglob("*.py") if "i18n" not in path.parts)


def _lupdate(session: nox.Session, target: Path, *options: str) -> None:
    """Estrae i testi dei sorgenti in `target` (lo crea o lo aggiorna), senza posizioni."""
    session.run(
        "uv",
        "run",
        "--locked",
        "pyside6-lupdate",
        *_translatable_sources(),
        "-locations",
        "none",
        "-source-language",
        "en",
        "-target-language",
        "it",
        *options,
        "-ts",
        str(target),
        external=True,
    )


@nox.session(python=False)
def i18n(session: nox.Session) -> None:
    """Traduzioni complete e coerenti con i sorgenti, poi compila i `.qm`."""
    _FRESH_CATALOG.parent.mkdir(parents=True, exist_ok=True)
    _FRESH_CATALOG.unlink(missing_ok=True)
    _lupdate(session, _FRESH_CATALOG)
    session.run(
        "uv",
        "run",
        "--locked",
        "python",
        "-m",
        "ponos_coach.i18n.catalog",
        str(_FRESH_CATALOG),
        str(_CATALOG),
        external=True,
    )
    session.run(
        "uv",
        "run",
        "--locked",
        "pyside6-lrelease",
        str(_CATALOG),
        "-qm",
        str(_COMPILED_CATALOG),
        external=True,
    )


@nox.session(python=False, default=False)
def i18n_update(session: nox.Session) -> None:
    """Aggiorna `ponos_it.ts` dai sorgenti: aggiunge i testi nuovi, toglie gli obsoleti."""
    _lupdate(session, _CATALOG, "-no-obsolete")
