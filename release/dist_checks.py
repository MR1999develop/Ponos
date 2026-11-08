# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Checks and packaging steps of `nox -s dist`: smoke test, local traces, archive, SHA256SUMS.

Run as `python -m release.dist_checks <command> ...` (see `main`). Pure helpers first, then the
commands that touch the disk and run the executable.
"""

from __future__ import annotations

import argparse
import getpass
import hashlib
import os
import platform
import re
import subprocess
import sys
import tarfile
import tempfile
import types
import zipfile
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path
from typing import Final

APP_FOLDER_NAME: Final = "PonosCoach"
CHECKSUMS_NAME: Final = "SHA256SUMS"
SMOKE_TIMEOUT_S: Final = 180
_BLOCK_BYTES: Final = 8 * 1024 * 1024
_MIN_USER_NAME_CHARS: Final = 4  # shorter names would match innocent byte sequences
_CHECKSUM_LINE: Final = re.compile(r"([0-9a-f]{64})  ([^/\\\n]+)")


class PackageError(Exception):
    """A release check failed; the message says which and never repeats a local path."""


# --- SHA256SUMS ------------------------------------------------------------------------------


def sha256_of(path: Path) -> str:
    """SHA-256 of a file, read in blocks."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(_BLOCK_BYTES):
            digest.update(block)
    return digest.hexdigest()


def format_checksums(files: Iterable[Path]) -> str:
    """`sha256sum` format (`<hex>  <name>`), sorted by name, one line per file."""
    ordered = sorted(files, key=lambda path: path.name)
    return "".join(f"{sha256_of(path)}  {path.name}\n" for path in ordered)


def verify_checksums(text: str, folder: Path) -> list[str]:
    """The names listed in `text` whose file in `folder` is missing or has another hash.

    Raises:
        PackageError: a line is not in `sha256sum` format or a name looks like a path.
    """
    wrong: list[str] = []
    for line in text.splitlines():
        match = _CHECKSUM_LINE.fullmatch(line)
        if match is None:
            raise PackageError("a SHA256SUMS line is not in the expected format")
        expected, name = match.groups()
        path = folder / name
        if not path.is_file() or sha256_of(path) != expected:
            wrong.append(name)
    return wrong


# --- Local traces ----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Needle:
    """Something that must not be in the package: `label` is what the report says."""

    label: str
    text: str
    case_sensitive: bool


def local_needles(project_root: Path) -> list[Needle]:
    """The user folder, the project folder (both with `\\` and `/`), the working directory
    and the user name, as they are on this machine."""
    needles: list[Needle] = []
    paths = {
        "user folder": Path.home(),
        "project folder": project_root,
        "working directory": Path.cwd(),
    }
    for label, path in paths.items():
        text = str(path)
        variants = {text, text.replace("\\", "/"), path.as_posix()}
        needles.extend(Needle(label, variant, case_sensitive=False) for variant in variants)
    user = getpass.getuser()
    if len(user) >= _MIN_USER_NAME_CHARS:
        needles += [
            Needle("user name in a path", f"{sep}{user}{sep}", False) for sep in ("\\", "/")
        ]
    return needles


def _encodings(needle: Needle) -> list[bytes]:
    """The needle as UTF-8 and as UTF-16 (Windows binaries keep wide strings), folded to
    lower case when the match ignores case (ASCII letters only)."""
    text = needle.text if needle.case_sensitive else needle.text.lower()
    return [text.encode("utf-8"), text.encode("utf-16-le")]


def _blocks(path: Path, overlap: int) -> Iterator[bytes]:
    """The file in blocks that overlap by `overlap` bytes, so no match is cut in two."""
    with path.open("rb") as handle:
        tail = b""
        while block := handle.read(_BLOCK_BYTES):
            yield tail + block
            tail = block[-overlap:] if overlap else b""


def find_traces(root: Path, needles: Sequence[Needle]) -> list[tuple[Path, str]]:
    """(file relative to `root`, needle label) for every file that contains a needle.

    Every regular file under `root` is read, binaries included; links are not followed.
    """
    patterns = [(n, _encodings(n)) for n in needles if n.text]
    overlap = max((len(e) for _, encodings in patterns for e in encodings), default=1) - 1
    found: set[tuple[Path, str]] = set()
    for path in sorted(root.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        for block in _blocks(path, overlap):
            folded = block.lower()
            for needle, encodings in patterns:
                haystack = block if needle.case_sensitive else folded
                if any(e in haystack for e in encodings):
                    found.add((path.relative_to(root), needle.label))
    return sorted(found)


def code_strings(code: types.CodeType) -> Iterator[str]:
    """The file names and the text constants of a code object and of the ones nested in it."""
    yield code.co_filename
    for constant in code.co_consts:
        if isinstance(constant, str):
            yield constant
        elif isinstance(constant, types.CodeType):
            yield from code_strings(constant)


def find_traces_in_code(
    modules: Iterable[tuple[str, types.CodeType]], needles: Sequence[Needle]
) -> list[tuple[str, str]]:
    """(module name, needle label) for every module whose code mentions a needle.

    The modules of a PyInstaller executable are compressed, so a scan of the bytes cannot see
    them: this one reads the strings of the code objects, the file names included.
    """
    found: set[tuple[str, str]] = set()
    for name, code in modules:
        for text in code_strings(code):
            for needle in needles:
                if needle.text and (
                    needle.text in text
                    if needle.case_sensitive
                    else needle.text.lower() in text.lower()
                ):
                    found.add((name, needle.label))
    return sorted(found)


def packaged_modules(executable: Path) -> Iterator[tuple[str, types.CodeType]]:
    """The Python modules inside a PyInstaller executable (needs PyInstaller installed)."""
    from PyInstaller.archive.readers import CArchiveReader, ZlibArchiveReader  # noqa: PLC0415

    with tempfile.TemporaryDirectory(prefix="ponos-pyz-") as scratch:
        pyz = Path(scratch) / "PYZ.pyz"
        pyz.write_bytes(CArchiveReader(str(executable)).extract("PYZ.pyz"))
        reader = ZlibArchiveReader(str(pyz))
        for name in list(reader.toc):
            extracted = reader.extract(name)
            code = extracted[1] if isinstance(extracted, tuple) else extracted
            if isinstance(code, types.CodeType):
                yield name, code


# --- Package ---------------------------------------------------------------------------------


def folder_size(root: Path) -> int:
    """Bytes of all the regular files under `root`."""
    return sum(p.stat().st_size for p in root.rglob("*") if p.is_file() and not p.is_symlink())


def human_size(size: int) -> str:
    """`123.4 MiB`."""
    value = float(size)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if value < 1024 or unit == "GiB":
            return f"{value:.1f} {unit}"
        value /= 1024
    raise AssertionError("unreachable")  # pragma: no cover


def package_name(version: str, system: str, machine: str) -> str:
    """`PonosCoach-<version>-<windows|linux>-<arch>` (no extension)."""
    arch = {"amd64": "x64", "x86_64": "x64", "arm64": "arm64", "aarch64": "arm64"}.get(
        machine.lower(), machine.lower()
    )
    return f"{APP_FOLDER_NAME}-{version}-{system.lower()}-{arch}"


def make_archive(app_folder: Path, destination: Path) -> None:
    """The app folder as a `.zip` (Windows) or `.tar.gz` (other systems); never replaces a file.

    Raises:
        FileExistsError: `destination` exists.
    """
    if destination.suffix == ".zip":
        with zipfile.ZipFile(destination, "x", zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(app_folder.rglob("*")):
                archive.write(path, Path(APP_FOLDER_NAME) / path.relative_to(app_folder))
    else:
        with tarfile.open(destination, "x:gz") as archive:
            archive.add(app_folder, arcname=APP_FOLDER_NAME)


def _executable(app_folder: Path) -> Path:
    name = "ponos-coach.exe" if sys.platform == "win32" else "ponos-coach"
    executable = app_folder / name
    if not executable.is_file():
        raise PackageError("the executable is missing from the package")
    return executable


def smoke_test(app_folder: Path) -> str:
    """Runs `ponos-coach --self-check` offscreen from the package; returns the report.

    Raises:
        PackageError: the executable fails, times out or reports a failed check.
    """
    executable = _executable(app_folder)
    with tempfile.TemporaryDirectory(prefix="ponos-smoke-") as scratch:
        report = Path(scratch) / "report.txt"
        env = {**os.environ, "QT_QPA_PLATFORM": "offscreen"}
        try:
            done = subprocess.run(  # noqa: S603 - the executable was just built by us
                [str(executable), "--self-check", str(report)],
                env=env,
                cwd=scratch,
                timeout=SMOKE_TIMEOUT_S,
                check=False,
                capture_output=True,
            )
        except subprocess.TimeoutExpired as exc:
            raise PackageError(f"the executable did not finish in {SMOKE_TIMEOUT_S} s") from exc
        text = report.read_text(encoding="utf-8") if report.is_file() else ""
    if done.returncode != 0 or not text or "FAIL" in text:
        raise PackageError(f"smoke test failed (exit {done.returncode}):\n{text}")
    return text


# --- Command line ----------------------------------------------------------------------------


def _command_smoke(app_folder: Path) -> int:
    print(smoke_test(app_folder), end="")  # noqa: T201
    return 0


def _command_traces(app_folder: Path, project_root: Path) -> int:
    needles = local_needles(project_root)
    traces = [(path.as_posix(), label) for path, label in find_traces(app_folder, needles)]
    traces += [
        (f"module {name}", label)
        for name, label in find_traces_in_code(packaged_modules(_executable(app_folder)), needles)
    ]
    for where, label in traces:
        print(f"{label}: {where}", file=sys.stderr)  # noqa: T201
    if traces:
        raise PackageError(f"{len(traces)} file(s) contain local paths or the user name")
    print("no local path or user name in the package")  # noqa: T201
    return 0


def _command_package(app_folder: Path, out_dir: Path) -> int:
    version = metadata.version("ponos-coach")
    name = package_name(version, platform.system(), platform.machine())
    suffix = ".zip" if sys.platform == "win32" else ".tar.gz"
    out_dir.mkdir(parents=True, exist_ok=True)
    archive = out_dir / (name + suffix)
    make_archive(app_folder, archive)
    sums = out_dir / CHECKSUMS_NAME
    sums.write_text(format_checksums([archive]), encoding="utf-8", newline="\n")
    if verify_checksums(sums.read_text(encoding="utf-8"), out_dir):
        raise PackageError("SHA256SUMS does not match the archive just written")
    print(f"app folder: {human_size(folder_size(app_folder))}")  # noqa: T201
    print(f"{archive.name}: {human_size(archive.stat().st_size)}")  # noqa: T201
    return 0


def main(argv: Sequence[str]) -> int:
    """Commands: `smoke APP`, `traces APP PROJECT_ROOT`, `package APP OUT`. Exit 1 on failure."""
    parser = argparse.ArgumentParser(prog="release.dist_checks")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("smoke").add_argument("app", type=Path)
    traces = commands.add_parser("traces")
    traces.add_argument("app", type=Path)
    traces.add_argument("project_root", type=Path)
    package = commands.add_parser("package")
    package.add_argument("app", type=Path)
    package.add_argument("out", type=Path)
    args = parser.parse_args(list(argv))
    try:
        if args.command == "smoke":
            return _command_smoke(args.app)
        if args.command == "traces":
            return _command_traces(args.app, args.project_root.resolve())
        return _command_package(args.app, args.out)
    except (PackageError, OSError) as exc:
        print(f"release.dist_checks: {args.command}: {exc}", file=sys.stderr)  # noqa: T201
        return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main(sys.argv[1:]))
