# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Mandatory checks: `uv run nox` locally and in CI (same command)."""

from pathlib import Path

import nox

nox.options.sessions = ["lint", "types", "arch", "tests", "audit", "i18n"]

_SOURCE_DIR = Path("src/ponos_coach")
_CATALOG = _SOURCE_DIR / "i18n" / "ponos_it.ts"  # versioned: the translator fills it in
_COMPILED_CATALOG = _CATALOG.with_suffix(".qm")  # generated and ignored by git
_FRESH_CATALOG = Path(".nox") / "i18n" / "fresh.ts"  # only the current texts of the sources


# The sessions use the project environment, synchronised from the lock with
# `uv run --locked` (fails if uv.lock is not up to date).
@nox.session(python=False)
def lint(session: nox.Session) -> None:
    """Formatting and lint with ruff."""
    session.run("uv", "run", "--locked", "ruff", "format", "--check", ".", external=True)
    session.run("uv", "run", "--locked", "ruff", "check", ".", external=True)


@nox.session(python=False)
def types(session: nox.Session) -> None:
    """mypy in strict mode on src, tests and noxfile, for Windows and for Linux."""
    for platform in ("win32", "linux"):
        session.run("uv", "run", "--locked", "mypy", "--platform", platform, external=True)


@nox.session(python=False)
def arch(session: nox.Session) -> None:
    """Architecture contracts with import-linter."""
    session.run("uv", "run", "--locked", "lint-imports", external=True)


@nox.session(python=False)
def tests(session: nox.Session) -> None:
    """pytest (Qt in offscreen mode), excluding the acceptance campaign tests."""
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
    """pip-audit on the dependencies locked in the lock file."""
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
    """Long campaign: tests marked `collaudo` with the long hypothesis profile."""
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
    """Python sources with texts to translate (`tr()`, `translate()`), in stable order."""
    return sorted(path.as_posix() for path in _SOURCE_DIR.rglob("*.py") if "i18n" not in path.parts)


def _lupdate(session: nox.Session, target: Path, *options: str) -> None:
    """Extracts the texts of the sources into `target` (creates or updates it), without
    positions."""
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
    """Complete translations consistent with the sources, then compiles the `.qm` files."""
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
    """Updates `ponos_it.ts` from the sources: adds the new texts, removes the obsolete ones."""
    _lupdate(session, _CATALOG, "-no-obsolete")
