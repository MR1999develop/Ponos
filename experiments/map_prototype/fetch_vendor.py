# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Downloads the prototype libraries from npm at a fixed version and verifies their hash.

For each package: `npm pack` (official registry), SHA-512 of the tarball compared with the one
declared by the registry (`dist.integrity`), licence read from the tarball `package.json`,
extraction of the useful `dist/` files only into `vendor/<name>/`. No CDN at runtime: the
prototype page loads local files only. Usage: `python experiments/map_prototype/fetch_vendor.py`.
"""

from __future__ import annotations

import base64
import hashlib
import json
import shutil
import subprocess
import tarfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
VENDOR = HERE / "vendor"
NPM = shutil.which("npm") or "npm"
# (package, fixed version, tarball files to extract)
PACKAGES = [
    (
        "maplibre-gl",
        "5.24.0",  # 6.x is ESM-only (needs a module server, not a plain script tag): see RESULTS.md
        (
            "dist/maplibre-gl-csp.js",
            "dist/maplibre-gl-csp-worker.js",
            "dist/maplibre-gl.css",
            "LICENSE.txt",
        ),
    ),
    ("leaflet", "1.9.4", ("dist/leaflet.js", "dist/leaflet.css", "LICENSE")),
    (
        "leaflet.markercluster",
        "1.5.3",
        (
            "dist/leaflet.markercluster.js",
            "dist/MarkerCluster.css",
            "dist/MarkerCluster.Default.css",
        ),
    ),
]


def run(*args: str) -> str:
    return subprocess.run(  # noqa: S603
        [NPM, *args], check=True, capture_output=True, text=True, timeout=300
    ).stdout.strip()


def fetch(name: str, version: str, wanted: tuple[str, ...]) -> dict[str, str]:
    spec = f"{name}@{version}"
    declared = run("view", spec, "dist.integrity")
    tarball_name = run("pack", spec, "--pack-destination", str(VENDOR)).splitlines()[-1]
    tarball = VENDOR / tarball_name
    digest = base64.b64encode(hashlib.sha512(tarball.read_bytes()).digest()).decode()
    if declared != f"sha512-{digest}":
        raise SystemExit(f"{spec}: the tarball hash does not match the registry one")
    target = VENDOR / name
    target.mkdir(exist_ok=True)
    with tarfile.open(tarball) as archive:
        info = json.load(archive.extractfile("package/package.json"))  # type: ignore[arg-type]
        for member in wanted:
            source = archive.extractfile(f"package/{member}")
            if source is not None:
                (target / Path(member).name).write_bytes(source.read())
    return {"package": spec, "license": info["license"], "integrity": declared}


def main() -> None:
    VENDOR.mkdir(exist_ok=True)
    report = [fetch(*package) for package in PACKAGES]
    (VENDOR / "VERSIONS.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    for line in report:
        print(line["package"], line["license"], "OK")


if __name__ == "__main__":
    main()
