# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""T-SEC (supply chain): the map libraries in the package are the ones the npm registry published.

`tools/vendor_map_libraries.py` took them from the registry after checking the SHA-512 of the
tarball against the registry's own `dist.integrity`; `VENDOR.json` keeps that value and the SHA-256
of every file taken out. These tests fail if a packaged file is changed by hand or a version moves.
"""

from __future__ import annotations

import hashlib
import json
import re
from importlib import resources

import pytest

from ponos_coach.ui.web_resources import resource_for_path

# Pinned here as well, so that changing VENDOR.json alone is not enough (the ADR and the register of
# dependencies carry the same values). Registry `dist.integrity` of the tarballs.
INTEGRITY = {
    "leaflet": (
        "1.9.4",
        "BSD-2-Clause",
        "sha512-nxS1ynzJOmOlHp+iL3FyWqK89GtNL8U8rvlMOsQdTTssxZwCXh8N2NB3GDQOL+YR3XnWyZAxwQixURb+FA74PA==",
    ),
    "leaflet.markercluster": (
        "1.5.3",
        "MIT",
        "sha512-vPTw/Bndq7eQHjLBVlWpnGeLa3t+3zGiuM7fJwCkiMFq+nmRuG3RI3f7f4N4TDX7T4NpbAXpR2+NTRSEGfCSeA==",
    ),
}


def vendor_dir() -> resources.abc.Traversable:
    return resources.files("ponos_coach.ui") / "web" / "vendor"


def manifest() -> list[dict[str, object]]:
    data = json.loads((vendor_dir() / "VENDOR.json").read_text(encoding="utf-8"))
    assert isinstance(data, list)
    return data


def test_the_manifest_names_exactly_the_pinned_packages_versions_licences_and_hashes() -> None:
    found = {
        entry["package"]: (entry["version"], entry["license"], entry["integrity"])
        for entry in manifest()
    }

    assert found == INTEGRITY


def test_every_packaged_file_matches_its_recorded_sha256() -> None:
    recorded: dict[str, str] = {}
    for entry in manifest():
        files = entry["files"]
        assert isinstance(files, dict)
        recorded.update(files)

    packaged = {item.name for item in vendor_dir().iterdir() if item.name != "VENDOR.json"}

    assert packaged == set(recorded)  # nothing extra, nothing missing
    for name, digest in recorded.items():
        assert hashlib.sha256((vendor_dir() / name).read_bytes()).hexdigest() == digest, name


def test_the_licence_texts_travel_with_the_libraries() -> None:
    leaflet = (vendor_dir() / "LICENSE-leaflet.txt").read_text(encoding="utf-8")
    cluster = (vendor_dir() / "LICENSE-leaflet.markercluster.txt").read_text(encoding="utf-8")

    assert "BSD 2-Clause" in leaflet or "Redistribution and use" in leaflet
    assert "Agafonkin" in leaflet
    assert "MIT" in cluster or "Permission is hereby granted" in cluster


@pytest.mark.parametrize(
    "path",
    [
        "/leaflet.js",
        "/leaflet.css",
        "/leaflet.markercluster.js",
        "/MarkerCluster.css",
        "/MarkerCluster.Default.css",
        "/map.html",
        "/map.js",
        "/track.html",
        "/track.js",
        "/qwebchannel.js",
    ],
)
def test_the_page_resources_are_served_from_the_closed_list(path: str) -> None:
    resource = resource_for_path(path)

    assert resource is not None
    assert resource.content


@pytest.mark.parametrize("path", ["/map.html", "/track.html"])
def test_the_map_pages_load_only_local_scripts_and_styles(path: str) -> None:
    page = resource_for_path(path)
    assert page is not None
    html = page.content.decode("utf-8")

    sources = re.findall(r'(?:src|href)="([^"]+)"', html)

    assert sources
    assert all("://" not in source and not source.startswith("//") for source in sources)
    assert "cdn" not in html.lower()
    assert "unpkg" not in html.lower()
    csp = re.search(r'Content-Security-Policy" content="([^"]+)"', html)
    assert csp is not None
    assert "default-src 'none'" in csp.group(1)
    assert "connect-src 'none'" in csp.group(1)
    assert "http:" not in csp.group(1)
    assert "https:" not in csp.group(1)
    assert "*" not in csp.group(1)


@pytest.mark.parametrize("path", ["/map.js", "/track.js"])
def test_the_page_script_has_no_network_call_of_its_own(path: str) -> None:
    script = resource_for_path(path)
    assert script is not None
    text = script.content.decode("utf-8")

    for forbidden in (
        "fetch(",
        "XMLHttpRequest",
        "WebSocket",
        "http://",
        "https://",
        "eval(",
        "innerHTML",
    ):
        assert forbidden not in text, forbidden
