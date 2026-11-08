# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Vendors the map page libraries from npm at fixed versions, verifying the registry hash.

For each package: `npm pack` (official registry), SHA-512 of the tarball compared with the one the
registry declares (`dist.integrity`), licence read from the tarball `package.json`, extraction of
the files the page needs and of the licence text into `src/ponos_coach/ui/web/vendor/`, and
`VENDOR.json` with the SHA-256 of every extracted file (a test checks the packaged files against
it). Nothing is loaded from a CDN at runtime. Usage: `python tools/vendor_map_libraries.py`.
"""

from __future__ import annotations

import base64
import hashlib
import json
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "src" / "ponos_coach" / "ui" / "web" / "vendor"
NPM = shutil.which("npm") or "npm"
# (package, fixed version, files of the tarball to extract, licence file of the tarball)
PACKAGES = (
    ("leaflet", "1.9.4", ("dist/leaflet.js", "dist/leaflet.css"), "LICENSE"),
    (
        "leaflet.markercluster",
        "1.5.3",
        (
            "dist/leaflet.markercluster.js",
            "dist/MarkerCluster.css",
            "dist/MarkerCluster.Default.css",
        ),
        "MIT-LICENCE.txt",
    ),
)


def _npm(*args: str) -> str:
    return subprocess.run(  # noqa: S603 - fixed program, fixed arguments
        [NPM, *args], check=True, capture_output=True, text=True, timeout=300
    ).stdout.strip()


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _vendor(name: str, version: str, wanted: tuple[str, ...], licence: str, work: Path) -> dict:
    spec = f"{name}@{version}"
    declared = _npm("view", spec, "dist.integrity")
    tarball = work / _npm("pack", spec, "--pack-destination", str(work)).splitlines()[-1]
    digest = base64.b64encode(hashlib.sha512(tarball.read_bytes()).digest()).decode()
    if declared != f"sha512-{digest}":
        raise SystemExit(f"{spec}: the tarball hash does not match the registry one")
    files: dict[str, str] = {}
    with tarfile.open(tarball) as archive:
        package = json.load(archive.extractfile("package/package.json"))  # type: ignore[arg-type]
        for member in (*wanted, licence):
            source = archive.extractfile(f"package/{member}")
            if source is None:
                raise SystemExit(f"{spec}: {member} is missing from the tarball")
            data = source.read()
            file_name = f"LICENSE-{name}.txt" if member == licence else Path(member).name
            (TARGET / file_name).write_bytes(data)
            files[file_name] = _sha256(data)
    return {
        "package": name,
        "version": version,
        "license": package["license"],
        "integrity": declared,
        "files": files,
    }


def main() -> None:
    TARGET.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as temporary:
        report = [_vendor(*package, Path(temporary)) for package in PACKAGES]
    (TARGET / "VENDOR.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    for entry in report:
        print(entry["package"], entry["version"], entry["license"], "OK")


if __name__ == "__main__":
    main()
