# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Phase 8 acceptance campaign, the package the user receives (T-SEC-12): the built archive and
its `SHA256SUMS`, what is in it and what is not, no local trace, and the executable's self-check.

These tests look at `dist/` as `nox -s dist` left it (never at the source tree); without a built
package they are skipped. The slow ones (scan of every byte, running the executable) are in the
`collaudo` campaign.
"""

from __future__ import annotations

import zipfile
from pathlib import Path, PurePosixPath

import pytest
from release.dist_checks import (
    APP_FOLDER_NAME,
    CHECKSUMS_NAME,
    find_traces,
    local_needles,
    sha256_of,
    smoke_test,
    verify_checksums,
)

ROOT = Path(__file__).resolve().parents[2]
DIST = ROOT / "dist"
RELEASE = DIST / "release"
APP = DIST / "app" / APP_FOLDER_NAME
ARCHIVES = sorted(RELEASE.glob("PonosCoach-*.zip")) if RELEASE.is_dir() else []

pytestmark = pytest.mark.skipif(not ARCHIVES, reason="no package built (nox -s dist)")

FORBIDDEN_PARTS = {
    "tests", ".git", ".claude", ".github", ".venv", "experiments", "docs", "__pycache__x",
}  # fmt: skip
FORBIDDEN_NAMES = {
    "PLAN.md", "CLAUDE.md", "TIDY.md", "SECURITY.md", "uv.lock", "noxfile.py", "pyproject.toml",
    ".env", ".gitignore", "deny.toml",
}  # fmt: skip


@pytest.fixture(scope="module")
def names() -> list[str]:
    with zipfile.ZipFile(ARCHIVES[0]) as archive:
        return archive.namelist()


def test_t_sec_12_the_hashes_in_sha256sums_are_those_of_the_archives() -> None:
    text = (RELEASE / CHECKSUMS_NAME).read_text(encoding="utf-8")

    assert verify_checksums(text, RELEASE) == []
    assert all(archive.name in text for archive in ARCHIVES)
    assert sha256_of(ARCHIVES[0]) in text


def test_t_sec_12_a_changed_archive_no_longer_matches(tmp_path: Path) -> None:
    text = (RELEASE / CHECKSUMS_NAME).read_text(encoding="utf-8")
    (tmp_path / ARCHIVES[0].name).write_bytes(b"not the archive")

    assert verify_checksums(text, tmp_path) == [ARCHIVES[0].name]


def test_t_sec_12_every_entry_of_the_archive_stays_inside_the_app_folder(names: list[str]) -> None:
    assert names
    for name in names:
        path = PurePosixPath(name)
        assert not path.is_absolute() and ".." not in path.parts, name
        assert "\\" not in name and ":" not in name, name
        assert path.parts[0] == APP_FOLDER_NAME, name


def test_t_sec_12_the_archive_has_the_executable_and_the_files_the_app_reads_at_run_time(
    names: list[str],
) -> None:
    joined = "\n".join(names)

    assert (
        f"{APP_FOLDER_NAME}/ponos-coach.exe" in names or f"{APP_FOLDER_NAME}/ponos-coach" in names
    )
    for needed in ("ponos_it.qm", "plotly"):
        assert needed in joined, needed


def test_t_sec_12_the_map_libraries_are_in_the_package_or_the_package_is_stale(
    names: list[str],
) -> None:
    """The spec ships the whole `ui/web` folder; a package built before the map libraries were
    merged lacks them, which is staleness (rebuild with `nox -s dist`), not a packaging defect."""
    vendored = ROOT / "src" / "ponos_coach" / "ui" / "web" / "vendor" / "leaflet.js"
    assert "ui/web" in (ROOT / "release" / "ponos_coach.spec").read_text(encoding="utf-8")
    if ARCHIVES[0].stat().st_mtime < vendored.stat().st_mtime:
        pytest.skip("package older than the map libraries: rebuild it before the release")

    assert any(name.endswith("vendor/leaflet.js") for name in names)
    assert any(name.endswith("vendor/leaflet.markercluster.js") for name in names)


def test_t_sec_12_the_archive_holds_no_development_file(names: list[str]) -> None:
    offenders = [
        name
        for name in names
        if (set(PurePosixPath(name).parts[1:]) & FORBIDDEN_PARTS)
        or PurePosixPath(name).name in FORBIDDEN_NAMES
    ]

    assert offenders == []


def test_t_sec_12_no_name_in_the_archive_carries_the_user_or_the_project_folder(
    names: list[str],
) -> None:
    needles = [n.text.lower().replace("\\", "/") for n in local_needles(ROOT)]
    lowered = "\n".join(names).lower()

    assert not [needle for needle in needles if needle in lowered]


@pytest.mark.collaudo
def test_t_sec_12_no_byte_of_the_package_holds_a_local_path_or_the_user_name() -> None:
    assert APP.is_dir(), "dist/app/PonosCoach missing"

    assert find_traces(APP, local_needles(ROOT)) == []


@pytest.mark.collaudo
def test_the_packaged_executable_passes_its_own_self_check() -> None:
    report = smoke_test(APP)

    assert "FAIL" not in report and report.strip()
