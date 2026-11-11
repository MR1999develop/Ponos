# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Release checks: SHA256SUMS, scan for local paths, archives (T-SEC-12)."""

from __future__ import annotations

import hashlib
import tarfile
import zipfile
from pathlib import Path

import pytest
from release.dist_checks import (
    Needle,
    PackageError,
    code_strings,
    find_traces,
    find_traces_in_code,
    format_checksums,
    human_size,
    local_needles,
    make_archive,
    package_name,
    sha256_of,
    verify_checksums,
)

from release import dist_checks

_HOME = Needle("user folder", "C:\\Users\\Atleta", case_sensitive=False)


class TestChecksums:
    def test_format_is_sha256sum_sorted_by_name(self, tmp_path: Path) -> None:
        (tmp_path / "b.zip").write_bytes(b"second")
        (tmp_path / "a.zip").write_bytes(b"first")

        text = format_checksums([tmp_path / "b.zip", tmp_path / "a.zip"])

        assert text == (
            f"{hashlib.sha256(b'first').hexdigest()}  a.zip\n"
            f"{hashlib.sha256(b'second').hexdigest()}  b.zip\n"
        )

    def test_matching_files_verify(self, tmp_path: Path) -> None:
        (tmp_path / "a.zip").write_bytes(b"first")

        assert verify_checksums(format_checksums([tmp_path / "a.zip"]), tmp_path) == []

    def test_changed_and_missing_files_are_reported(self, tmp_path: Path) -> None:
        (tmp_path / "a.zip").write_bytes(b"first")
        (tmp_path / "b.zip").write_bytes(b"second")
        text = format_checksums([tmp_path / "a.zip", tmp_path / "b.zip"])
        (tmp_path / "a.zip").write_bytes(b"first, altered")
        (tmp_path / "b.zip").unlink()

        assert verify_checksums(text, tmp_path) == ["a.zip", "b.zip"]

    @pytest.mark.parametrize(
        "line",
        [
            "nothex  a.zip",
            "0" * 64 + " a.zip",  # one space
            "0" * 64 + "  ../a.zip",  # a name that is a path
            "0" * 64 + "  sub\\a.zip",
            "0" * 64 + "  ",
            "A" * 64 + "  a.zip",
        ],
    )
    def test_malformed_lines_are_errors_not_guesses(self, tmp_path: Path, line: str) -> None:
        with pytest.raises(PackageError):
            verify_checksums(line + "\n", tmp_path)

    def test_hash_of_a_file_larger_than_a_block(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(dist_checks, "_BLOCK_BYTES", 7)
        (tmp_path / "f").write_bytes(b"x" * 100)

        assert sha256_of(tmp_path / "f") == hashlib.sha256(b"x" * 100).hexdigest()


class TestLocalTraces:
    def test_clean_package_has_none(self, tmp_path: Path) -> None:
        (tmp_path / "a.bin").write_bytes(b"nothing personal here" * 10)

        assert find_traces(tmp_path, [_HOME]) == []

    @pytest.mark.parametrize(
        "encoded",
        [
            b"x C:\\Users\\Atleta\\proj y",
            "x C:\\Users\\Atleta\\proj y".encode("utf-16-le"),
            b"x c:\\users\\ATLETA\\proj y",  # case differs
            "x c:\\USERS\\atleta".encode("utf-16-le"),
        ],
    )
    def test_path_is_found_as_utf8_or_utf16_ignoring_case(
        self, tmp_path: Path, encoded: bytes
    ) -> None:
        (tmp_path / "sub").mkdir()
        (tmp_path / "sub" / "lib.dll").write_bytes(b"\x00" * 50 + encoded + b"\x00" * 50)

        assert find_traces(tmp_path, [_HOME]) == [(Path("sub/lib.dll"), "user folder")]

    def test_a_match_split_between_two_blocks_is_found(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(dist_checks, "_BLOCK_BYTES", 40)
        (tmp_path / "f").write_bytes(b"." * 30 + b"C:\\Users\\Atleta" + b"." * 9)

        assert find_traces(tmp_path, [_HOME]) == [(Path("f"), "user folder")]

    def test_case_sensitive_needle_does_not_match_other_case(self, tmp_path: Path) -> None:
        (tmp_path / "f").write_bytes(b"\\atleta\\")
        needle = Needle("user name in a path", "\\Atleta\\", case_sensitive=True)

        assert find_traces(tmp_path, [needle]) == []

    def test_local_needles_cover_home_project_and_working_directory(self, tmp_path: Path) -> None:
        labels = {needle.label for needle in local_needles(tmp_path)}

        assert {"user folder", "project folder", "working directory"} <= labels

    def test_on_a_hosted_runner_the_generic_account_is_not_a_needle(self, tmp_path: Path) -> None:
        # The account of a GitHub runner (runneradmin) is the one that also built third-party
        # wheels such as numpy: its folder in their metadata is no trace of the maintainer.
        labels = {n.label for n in local_needles(tmp_path, hosted_runner=True)}

        assert labels == {"project folder", "working directory"}

    def test_on_a_maintainer_machine_the_user_folder_is_always_a_needle(
        self, tmp_path: Path
    ) -> None:
        labels = {n.label for n in local_needles(tmp_path, hosted_runner=False)}

        assert "user folder" in labels

    @pytest.mark.parametrize(
        ("environment", "hosted"),
        [
            ({"GITHUB_ACTIONS": "true", "RUNNER_ENVIRONMENT": "github-hosted"}, True),
            ({"GITHUB_ACTIONS": "true", "RUNNER_ENVIRONMENT": "self-hosted"}, False),
            ({"RUNNER_ENVIRONMENT": "github-hosted"}, False),
            ({}, False),
        ],
        ids=["hosted", "self-hosted", "no-actions", "local"],
    )
    def test_only_a_github_hosted_runner_counts_as_hosted(
        self, environment: dict[str, str], *, hosted: bool
    ) -> None:
        assert dist_checks.on_github_hosted_runner(environment) is hosted

    def test_every_form_of_the_project_folder_is_a_needle(self, tmp_path: Path) -> None:
        texts = {n.text for n in local_needles(tmp_path) if n.label == "project folder"}

        assert str(tmp_path) in texts
        assert tmp_path.as_posix() in texts


class TestTracesInCode:
    _NEEDLE = Needle("user folder", "C:/Users/Atleta", case_sensitive=False)

    def test_file_name_and_text_constants_are_searched(self) -> None:
        clean = compile("x = 'fine'", "pkg/mod.py", "exec")
        by_name = compile("x = 1", "c:/users/atleta/proj/a.py", "exec")
        by_text = compile("x = 'C:/Users/Atleta/data'", "pkg/b.py", "exec")
        nested = compile("def f():\n    return 'C:/Users/Atleta/x'", "pkg/c.py", "exec")

        found = find_traces_in_code(
            [("clean", clean), ("by_name", by_name), ("by_text", by_text), ("nested", nested)],
            [self._NEEDLE],
        )

        assert found == [
            ("by_name", "user folder"),
            ("by_text", "user folder"),
            ("nested", "user folder"),
        ]

    def test_strings_of_nested_code_objects(self) -> None:
        code = compile("def f():\n    return 'inside'", "m.py", "exec")

        assert {"inside", "m.py"} <= set(code_strings(code))


class TestArchives:
    def _app(self, tmp_path: Path) -> Path:
        app = tmp_path / "app"
        (app / "_internal").mkdir(parents=True)
        (app / "ponos-coach.exe").write_bytes(b"exe")
        (app / "_internal" / "data.bin").write_bytes(b"data")
        return app

    def test_zip_has_the_app_folder_at_its_root(self, tmp_path: Path) -> None:
        destination = tmp_path / "out.zip"

        make_archive(self._app(tmp_path), destination)

        with zipfile.ZipFile(destination) as archive:
            assert "PonosCoach/ponos-coach.exe" in archive.namelist()
            assert "PonosCoach/_internal/data.bin" in archive.namelist()

    def test_tar_has_the_app_folder_at_its_root(self, tmp_path: Path) -> None:
        destination = tmp_path / "out.tar.gz"

        make_archive(self._app(tmp_path), destination)

        with tarfile.open(destination) as archive:
            assert "PonosCoach/ponos-coach.exe" in archive.getnames()

    @pytest.mark.parametrize("name", ["out.zip", "out.tar.gz"])
    def test_an_existing_archive_is_never_replaced(self, tmp_path: Path, name: str) -> None:
        destination = tmp_path / name
        destination.write_bytes(b"mine")

        with pytest.raises(FileExistsError):
            make_archive(self._app(tmp_path), destination)

        assert destination.read_bytes() == b"mine"


class TestNaming:
    @pytest.mark.parametrize(
        ("system", "machine", "expected"),
        [
            ("Windows", "AMD64", "PonosCoach-1.2.3-windows-x64"),
            ("Linux", "x86_64", "PonosCoach-1.2.3-linux-x64"),
            ("Linux", "aarch64", "PonosCoach-1.2.3-linux-arm64"),
        ],
    )
    def test_package_name(self, system: str, machine: str, expected: str) -> None:
        assert package_name("1.2.3", system, machine) == expected

    @pytest.mark.parametrize(
        ("size", "text"),
        [(0, "0.0 B"), (1023, "1023.0 B"), (1024, "1.0 KiB"), (256_382_778, "244.5 MiB")],
    )
    def test_human_size(self, size: int, text: str) -> None:
        assert human_size(size) == text
