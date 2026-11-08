# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Mandatory checks: `uv run nox` locally and in CI (same command)."""

import shutil
from pathlib import Path

import nox

nox.options.sessions = ["lint", "types", "arch", "tests", "audit", "i18n"]

_SOURCE_DIR = Path("src/ponos_coach")
_CATALOG = _SOURCE_DIR / "i18n" / "ponos_it.ts"  # versioned: the translator fills it in
_COMPILED_CATALOG = _CATALOG.with_suffix(".qm")  # generated and ignored by git
_FRESH_CATALOG = Path(".nox") / "i18n" / "fresh.ts"  # only the current texts of the sources
_DIST_DIR = Path("dist")  # ignored by git
_APP_FOLDER = _DIST_DIR / "app" / "PonosCoach"
_RELEASE_FOLDER = _DIST_DIR / "release"  # archive and SHA256SUMS: what is attached to a release


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
    _compile_catalog(session)  # the app loads the `.qm`, which git does not keep
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
    _compile_catalog(session)
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


def _compile_catalog(session: nox.Session) -> None:
    """Compiles `ponos_it.ts` into the `.qm` that the app and the package load."""
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
    _compile_catalog(session)


@nox.session(python=False, default=False)
def i18n_update(session: nox.Session) -> None:
    """Updates `ponos_it.ts` from the sources: adds the new texts, removes the obsolete ones."""
    _lupdate(session, _CATALOG, "-no-obsolete")


@nox.session(python=False, default=False)
def dist(session: nox.Session) -> None:
    """Release package: PyInstaller build, smoke test of the executable, scan for local paths and
    the user name, archive and `SHA256SUMS` (sizes are printed). Not part of the default checks:
    the release workflow runs it on Windows and Ubuntu."""
    _compile_catalog(session)  # the spec needs it; completeness is the `i18n` session's job
    shutil.rmtree(_DIST_DIR, ignore_errors=True)
    session.run(
        "uv",
        "run",
        "--locked",
        "pyinstaller",
        "--noconfirm",
        "--clean",
        "--distpath",
        str(_DIST_DIR / "app"),
        "--workpath",
        "build/pyinstaller",
        "release/ponos_coach.spec",
        external=True,
    )
    checks = ("uv", "run", "--locked", "python", "-m", "release.dist_checks")
    session.run(*checks, "smoke", str(_APP_FOLDER), external=True)
    session.run(*checks, "traces", str(_APP_FOLDER), ".", external=True)
    session.run(*checks, "package", str(_APP_FOLDER), str(_RELEASE_FOLDER), external=True)
