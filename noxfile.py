# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Controlli obbligatori: `uv run nox` in locale e in CI (stesso comando)."""

import nox

nox.options.sessions = ["lint", "types", "arch", "tests", "audit"]


# Le sessioni usano l'ambiente del progetto, sincronizzato dal lock con
# `uv run --locked` (fallisce se uv.lock non è aggiornato).
@nox.session(python=False)
def lint(session: nox.Session) -> None:
    """Formattazione e lint con ruff."""
    session.run("uv", "run", "--locked", "ruff", "format", "--check", ".", external=True)
    session.run("uv", "run", "--locked", "ruff", "check", ".", external=True)


@nox.session(python=False)
def types(session: nox.Session) -> None:
    """mypy in modalità strict su src, tests e noxfile."""
    session.run("uv", "run", "--locked", "mypy", external=True)


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
